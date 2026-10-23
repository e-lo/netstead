---
title: Query and update linked tables
audience: users
kind: howto
summary: Join GMNS tables across foreign keys, inspect results without going through pandas, and apply Network-Wrangler-style edits with atomic rollback.
---

# Query and update linked tables

## When to use this

You want to *ask questions that span tables* ("which lanes sit on links faster than 45 mph?") and *make targeted edits* ("set every residential link to 25 mph") — the bread-and-butter of network analysis and the kind of thing [Network Wrangler](https://github.com/network-wrangler/network_wrangler) does. This page covers both: querying across foreign keys, looking at results without materializing a DataFrame you don't need, and editing rows with an undo log.

## Quick example

Every table exposes a lazy [ibis](https://ibis-project.org/) expression via `.expr`. Join two tables on their foreign key, filter, and look at the result — all lazy until the last line.

```python
import netstead
from netstead.fixtures import leavenworth

net = netstead.read(leavenworth.csv_dir())
link = net.tables["link"].expr
lane = net.tables["lane"].expr

# "Which lanes sit on links faster than 45 mph?"  (lane.link_id → link.link_id)
fast_lanes = lane.join(
    link.filter(link.free_speed > 45),
    lane.link_id == link.link_id,
)
print(f"{fast_lanes.count().execute()} lanes on links over 45 mph")
```

## Step-by-step

### 1. The foreign-key map

The bundled Leavenworth fixture wires its tables together like this:

```text
node ──< link >── node        link.from_node_id / to_node_id → node.node_id
         │
         ├──< lane             lane.link_id → link.link_id
         ├──< link_tod         link_tod.link_id → link.link_id
         └──< geometry         link.geometry_id → geometry.geometry_id
```

A join is just `child.join(parent, child.fk == parent.pk)`. Reach the lazy expression with `net.tables["link"].expr` (or the named accessor's `.expr` — `net.links.expr`).

### 2. Build the query — it stays lazy

`.filter`, `.join`, `.select`, `.group_by`, `.order_by` all *add to a query plan*. Nothing touches data until you call a terminal method. So you can compose as deep as you like for free — the whole chain compiles to a single DuckDB SQL statement:

```python
import netstead
from netstead.fixtures import leavenworth

net = netstead.read(leavenworth.csv_dir())
link = net.tables["link"].expr
lane = net.tables["lane"].expr

q = (
    lane.join(link.filter(link.free_speed > 45), lane.link_id == link.link_id)
        .select("lane_id", "link_id", "lane_num", "width", "free_speed", "facility_type")
        .order_by("link_id", "lane_num")
)
# `q` is still just a plan — no data has been read yet.
```

### 3. Look at it interactively — no pandas needed

Turn on ibis interactive mode once per session and just evaluate the expression. It renders a rich table (via Arrow, not pandas); `.head(n)` pushes a `LIMIT` so DuckDB only returns those rows:

```python
import ibis
import netstead
from netstead.fixtures import leavenworth

net = netstead.read(leavenworth.csv_dir())
link = net.tables["link"].expr
lane = net.tables["lane"].expr
q = lane.join(link.filter(link.free_speed > 45), lane.link_id == link.link_id)

ibis.options.interactive = True     # set once per session/notebook
print(q.head(5))                    # DuckDB runs LIMIT 5; renders a table
```

In a Jupyter notebook the same expression renders as an HTML table — no `print`, no `.to_pandas()`. Interactive mode is a global, session-wide switch; in a notebook you set it once at the top and leave it on.

### 4. Materialize only when you hand the result to other code

`.execute()` runs the query at any point. Pick the container that fits what comes next — you rarely need pandas:

```python
n      = q.count().execute()       # Python int  — DuckDB COUNT(*), builds no table
arrow  = q.to_pyarrow()            # pa.Table     — zero-copy, no pandas dependency
# polars_df = q.to_polars()        # pl.DataFrame — if polars is your downstream
df     = q.to_pandas()             # pd.DataFrame — for plotting / sklearn / .describe()
```

`.execute()` is always available — call it the moment you want values, skip it while you're still composing. Reach for `.to_pandas()` only when something downstream actually wants a pandas frame.

### 5. See the SQL (or drop to raw DuckDB)

To understand *why* a query is fast or slow, print the compiled SQL:

```python
print(ibis.to_sql(q))   # the exact DuckDB SELECT ... JOIN ... WHERE ...
```

The predicate pushes down to DuckDB, so a bbox or facility-type filter on a partitioned-Parquet source prunes partitions before reading.

### 6. Update rows — Network-Wrangler style, with rollback

Edits go through a `Session`: every change is recorded with a before/after diff and an undo record, so a failed batch leaves the network untouched. The four ops are `add_rows`, `update_rows`, `delete_rows`, and `replace_table`.

The predicate for `update_rows` / `delete_rows` is a **callable** `(table) -> boolean column` — it's re-applied against the table inside the edit, so write it as a `lambda`:

```python
import netstead
from netstead.fixtures import leavenworth
from corral.editing import Session, Edit

net = netstead.read(leavenworth.csv_dir())

# "Set every residential link to 25 mph."
with Session(net) as s:
    result = s.add_edit(
        Edit(
            op="update_rows",
            table="link",
            payload={
                "predicate": lambda t: t.facility_type == "residential",
                "set": {"free_speed": 25.0},
            },
        )
    )

print(f"{result.diff.rows_changed} links updated")

# The network now reflects the change — re-query to confirm:
link = net.tables["link"].expr
still_40 = link.filter((link.facility_type == "residential") & (link.free_speed == 40.0))
print(f"residential links still at 40 mph: {still_40.count().execute()}")
```

`result.diff` carries `rows_added` / `rows_removed` / `rows_changed` plus capped before/after samples, and `result.applied_at` / `result.session_id` for the audit trail.

### 7. Undo a change

Call `session.rollback()` to reverse every edit applied in that session — useful when an edit didn't do what you expected, or you're exploring "what if":

```python
import netstead
from netstead.fixtures import leavenworth
from corral.editing import Session, Edit

net = netstead.read(leavenworth.csv_dir())
print(f"before: {net.tables['link'].count()} links")

with Session(net) as s:
    s.add_edit(Edit(op="delete_rows", table="link",
                    payload={"predicate": lambda t: t.facility_type == "primary"}))
    print(f"after delete: {net.tables['link'].count()} links")
    s.rollback()

print(f"after rollback: {net.tables['link'].count()} links")
```

A `Session(net, log_path="edits.parquet")` persists the audit log to disk alongside the network so the history survives the process.

## Common variations

???+ note "Default — join, filter, count"
    The most common shape: join across the FK, filter the parent, count the survivors.

    ```python
    lane.join(link.filter(link.free_speed > 45), lane.link_id == link.link_id).count().execute()
    ```

??? note "Aggregate across a join"
    Group-by on a joined expression — e.g. average lane width per facility type.

    ```python
    joined = lane.join(link, lane.link_id == link.link_id)
    joined.group_by(link.facility_type).aggregate(avg_width=lane.width.mean()).execute()
    ```

??? note "Two-hop traversal (lane → link → node)"
    Chain joins to reach a grandparent table — e.g. lanes on links that depart a 4-way stop.

    ```python
    node = net.tables["node"].expr
    stop4 = node.filter(node.ctrl_type == "stop_4_way")
    links_at_stop4 = link.join(stop4, link.from_node_id == stop4.node_id).select("link_id")
    lane.join(links_at_stop4, lane.link_id == links_at_stop4.link_id).count().execute()
    ```

??? note "Add rows"
    `add_rows` appends; the payload is a list of dicts matching the table schema.

    ```python
    with Session(net) as s:
        s.add_edit(Edit(op="add_rows", table="node",
                        payload={"rows": [{"node_id": 9001, "x_coord": -120.66, "y_coord": 47.59}]}))
    ```

??? note "Higher-level cleanup ops"
    For geometry-aware edits (simplify, merge close nodes, drop orphans) use the `netstead.clean` helpers, which wrap these primitives with domain logic — see [Edit with rollback](edit-with-rollback.md).

## Pitfalls

* **`update_rows` / `delete_rows` predicates are callables, not expressions.** Write `lambda t: t.col == x`, not `link.col == x`. The edit re-applies the predicate against the table's own expression.
* **Joins need the `.expr`, not the `Table` wrapper.** `net.links` is the convenience wrapper (`.count()`, `.columns()`, `.to_pandas()`); `net.links.expr` (or `net.tables["link"].expr`) is the ibis expression you join + filter on.
* **A filter on a table with no geometry column silently returns everything for spatial predicates.** For `from_bbox`-style spatial filters, run them on the table that actually carries the WKT (in Leavenworth that's `geometry`, not `link`).
* **Materialize once, at the end.** Calling `.to_pandas()` mid-chain forces a DuckDB→Arrow→pandas round-trip and then re-wraps for the next step. Compose lazily; execute last.

## See also

* [The compute engine (DuckDB) and dataframe formats](https://e-lo.github.io/netstead/corral/concepts/engines/) — why lazy-by-default, and getting data in/out as pandas/polars/Arrow.
* [Edit with rollback](edit-with-rollback.md) — the geometry-aware `netstead.clean` ops on top of these primitives.
* [Scope from seed nodes](scope-from-nodes.md) — FK-aware subsetting that returns a whole sub-network.
* [ibis docs](https://ibis-project.org/) — the full expression API (`mutate`, window functions, `case`, …).
