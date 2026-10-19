---
title: Edit a network with atomic rollback
audience: users
kind: howto
summary: Run geometry-aware cleanup ops (merge close nodes, drop orphans, simplify geometry) inside a corral.editing.Session — every op returns an EditResult with a diff, and the session commits on clean exit or rolls back on exception.
---

# Edit a network with atomic rollback

## When to use this

You're applying a destructive, geometry-aware cleanup to a network — merge near-coincident nodes, drop orphan nodes, simplify link geometry, recompute lengths — and you want a safety net. The `netstead.clean` ops run inside a `Session` that records every change, so you can undo all of them in LIFO order: by raising inside the `with` block, by calling `session.rollback()`, or by replaying a persisted log later.

These ops are the geometry-aware layer on top of the raw edit primitives. For low-level row edits (`update_rows`, `delete_rows`, `add_rows`) and querying across foreign keys, see [Query and update linked tables](query-and-update.md).

## Quick example

Open a session, merge nodes that sit almost on top of each other, and exit cleanly so the edit commits. `merge_close_nodes` rewrites the `from_node_id` / `to_node_id` foreign key on every incident link to point at the surviving node — the whole point of doing it inside a session:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.clean import merge_close_nodes
from corral.editing import Session

net = netstead.read(leavenworth.csv_dir())
print(f"before: {net.nodes.count()} nodes, {net.links.count()} links")

# Leavenworth's coords are WGS84 degrees, so threshold_m is in degrees:
# 0.0005 deg ≈ 56 m at this latitude. Closest nodes are ~27 m apart, so
# this deliberately-large threshold collapses 22 of them (121 → 99 nodes).
with Session(net) as s:
    node_edit, link_edit = merge_close_nodes(net, s, threshold_m=0.0005)
    print(f"merged away {node_edit.diff.rows_removed} nodes; "
          f"link table replaced ({link_edit.diff.rows_changed} rows)")

print(f"after:  {net.nodes.count()} nodes")
```

Expected:

```text
before: 121 nodes, 339 links
merged away 22 nodes; link table replaced (339 rows)
after:  99 nodes
```

Both ops are bulk `replace_table` edits, so `rows_changed` counts every row of the replaced table (339 links, 99 surviving nodes) — not just the 84 links whose end nodes were actually rewired. `rows_removed` on the node result is the number to read.

If the `with` block raises instead, the session reverses every op and `net` is back to its pre-edit state.

![Diff card showing a clean-op EditResult on Leavenworth](../assets/screenshots/leavenworth-simplify-edit-result.png){ .screenshot }
*EditResult diff card: per-table row counts (added / removed / changed) and capped before/after row samples.*

## Step-by-step

### 1. Install

The `[clean]` extra brings in the geometry stack the ops depend on — `shapely`, `geopandas`, `pyproj`:

```bash
pip install 'netstead[clean]'
```

### 2. Open a Session

A `Session` wraps the network in an editable, transactional view. Inside the `with` block you pass *both* `net` and `s` into each op — the op reads state from `net` and records its change on `s` so the session can undo it later:

```python
import netstead
from netstead.fixtures import leavenworth
from corral.editing import Session

net = netstead.read(leavenworth.csv_dir())
with Session(net) as s:
    ...  # editing ops go here — see the next step
```

### 3. Apply one or more ops

Multiple ops in the same `with` block share one transactional boundary — rollback undoes all of them. A realistic cleanup pass merges coincident nodes, then sweeps up any node the merge left with no incident links:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.clean import merge_close_nodes, remove_orphans
from corral.editing import Session

net = netstead.read(leavenworth.csv_dir())
with Session(net) as s:
    node_edit, link_edit = merge_close_nodes(net, s, threshold_m=0.0005)
    orphan_edit = remove_orphans(net, s)
    print(f"merge removed {node_edit.diff.rows_removed} nodes; "
          f"orphan sweep removed {orphan_edit.diff.rows_removed} more")
```

Each op returns an `EditResult` (note: `merge_close_nodes` touches two tables, so it returns a **list** of two — one for `node`, one for `link`). An `EditResult` carries three fields:

| Field | Type | Description |
|---|---|---|
| `.diff` | `Diff` | Per-table counts: `rows_added`, `rows_removed`, `rows_changed`, plus capped before/after samples. Cheap to print / log. |
| `.edit` | `Edit` | The underlying op (op name, target table, payload). |
| `.rollback_data` | `Any` | The state captured before the op ran — opaque, consumed by `Session.rollback()` / `corral.editing.rollback`. |

`EditResult` also carries `.applied_at` (timestamp) and `.session_id`.

### 4. Commit or rollback

Clean exit from the `with` block commits all ops. An exception or an explicit `s.rollback()` reverses them in LIFO order:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.clean import merge_close_nodes, remove_orphans
from corral.editing import Session

net = netstead.read(leavenworth.csv_dir())

# Commit:
with Session(net) as s:
    remove_orphans(net, s)
# committed at __exit__

# Rollback by raising — net is restored to the pre-edit state:
before = net.nodes.count()
try:
    with Session(net) as s:
        merge_close_nodes(net, s, threshold_m=0.0005)
        raise RuntimeError("changed my mind")
except RuntimeError:
    pass
assert net.nodes.count() == before

# Rollback explicitly while staying in the block:
with Session(net) as s:
    node_edit, _ = merge_close_nodes(net, s, threshold_m=0.0005)
    if node_edit.diff.rows_removed > 100:
        s.rollback()  # too aggressive — undo
```

### 5. Persist the edit log (optional)

Pass `log_path=` to write a chronological record of each op (name, params, diff, rollback blob) to a sidecar parquet. The log is the only way to undo edits across process boundaries:

<!-- doctest: skip -->
```python
with Session(net, log_path="history.parquet") as s:
    merge_close_nodes(net, s, threshold_m=0.0005)
    remove_orphans(net, s)
```

Later, in a fresh process, replay the log to undo every recorded op in LIFO order:

<!-- doctest: skip -->
```python
from netstead import Network
from corral.editing import rollback

net = Network.from_source("./edited.parquet")
rollback(net, log_path="history.parquet")  # replay in LIFO
```

### 6. Write the result

Persist the edited network with `net.write(...)`. An edit marks the touched tables dirty, so writing emits a benign `OutOfSyncWarning` (the validator's foreign-key index hasn't been re-resolved against the edited rows — tracked in [#164](https://github.com/e-lo/netstead/issues/164)). Suppress it for the write, exactly as the CLI does:

<!-- doctest: skip -->
```python
import warnings
from corral.dataset.package import OutOfSyncWarning

with warnings.catch_warnings():
    warnings.simplefilter("ignore", OutOfSyncWarning)
    net.write("./leavenworth-edited.parquet", overwrite=False)
```

The warning is informational: it tells you the in-memory FK index reflects the pre-edit rows. A fresh `netstead.read` of the written network re-resolves the index from scratch, and `netstead validate` re-checks the foreign keys.

## Common variations

???+ note "Default — transactional block with a single op"
    Most common pattern: open session, run one op, exit cleanly to commit.

    ```python
    import netstead
    from netstead.fixtures import leavenworth
    from netstead.clean import remove_orphans
    from corral.editing import Session

    net = netstead.read(leavenworth.csv_dir())
    with Session(net) as s:
        remove_orphans(net, s)
    ```

??? note "Chain multiple ops in one atomic block"
    All-or-nothing: rollback undoes the whole batch.

    ```python
    import netstead
    from netstead.fixtures import leavenworth
    from netstead.clean import merge_close_nodes, remove_orphans
    from corral.editing import Session

    net = netstead.read(leavenworth.csv_dir())
    with Session(net) as s:
        merge_close_nodes(net, s, threshold_m=0.0005)
        remove_orphans(net, s)
    ```

??? note "Preview without committing"
    `--dry-run` on the CLI runs the op, prints the diff, and reverts before exit.

    ```bash
    netstead clean merge-close-nodes <src> --threshold-m 0.0005 --dry-run
    ```

??? note "Run one op from the shell"
    Useful in pipelines; the CLI opens the session, writes the result, and suppresses the dirty-write warning for you.

    ```bash
    netstead clean remove-orphans <src> --dest ./out.parquet
    ```

??? note "Capture the diff for a log line"
    `EditResult` renders as a card in notebooks (`_repr_html_`); in plain code, read the `.diff` counts directly.

    ```python
    import netstead
    from netstead.fixtures import leavenworth
    from netstead.clean import merge_close_nodes
    from corral.editing import Session

    net = netstead.read(leavenworth.csv_dir())
    with Session(net) as s:
        node_edit, _ = merge_close_nodes(net, s, threshold_m=0.0005)
    d = node_edit.diff
    print(f"+{d.rows_added} / -{d.rows_removed} / ~{d.rows_changed}")
    ```

??? note "Geometry ops find the shapes wherever they live"
    GMNS lets a network keep link shapes inline (`link.geometry`) or in a separate `geometry` table keyed by `link.geometry_id`. Leavenworth uses the `geometry` table, and the geometry ops need no preparation for that: `simplify_geometry` edits the table that holds the shapes (so its `EditResult` targets `geometry` here, and rollback restores that table), while `recompute_lengths` resolves each link's shape the way `netstead.semantics.assemble_link_geometry` does and writes `link.length`:

    ```python
    import netstead
    from netstead.fixtures import leavenworth
    from netstead.clean import recompute_lengths, simplify_geometry
    from corral.editing import Session

    net = netstead.read(leavenworth.csv_dir())
    with Session(net) as s:
        shape_edit = simplify_geometry(net, s, mode="douglas_peucker", tolerance=0.00001)
        length_edit = recompute_lengths(net, s, geodesic=True)
    print(f"simplified {shape_edit.edit.table}; recomputed length on {length_edit.edit.table}")
    ```

    The CLI does the same; write to a new location so the source stays intact:

    ```bash
    netstead clean simplify-geometry <src> --dest <out> --mode douglas_peucker --tolerance 0.00001
    ```

    When the link table carries an inline `geometry` column, that column wins (the same priority as `assemble_link_geometry`). A network with shapes in neither place raises a `CleanError` that names both.

## Pitfalls

* **`threshold_m` is in coordinate units, not always meters.** The name reflects the common projected case. On a WGS84 (lat/lon) network like Leavenworth the units are *degrees* — `0.0005` ≈ 56 m. Pass a value in your network's CRS units, not a string like `"2m"`.
* **`merge_close_nodes` returns a list, not one `EditResult`.** It touches two tables (`node` + `link`), so it returns `[node_result, link_result]`. The single-table ops (`remove_orphans`, `simplify_geometry`, `recompute_lengths`) return one `EditResult`.
* **`simplify_geometry` edits whichever table holds the shapes.** Its `EditResult` targets `link` when geometry is inline and `geometry` when it is carried via `geometry_id` — read `result.edit.table` rather than assuming `link`. `recompute_lengths` always edits `link`, because that is where `length` lives (see the variation above).
* **Pass `--dest` to the CLI ops when the source is a CSV directory.** Without it the op writes back over the source as parquet, and that in-place rewrite currently fails partway after the CSVs are gone ([tracked as a follow-up](https://github.com/e-lo/netstead/issues)). `--dry-run` is safe either way.
* **`OutOfSyncWarning` on write is expected after an edit** ([#164](https://github.com/e-lo/netstead/issues/164)). It means the in-memory FK index still reflects the pre-edit rows; it is not an error. Suppress it for the write (step 6) — re-reading the written network rebuilds the index.
* **A 100%-null column can break the write** ([#163](https://github.com/e-lo/netstead/issues/163)). Writing an edited table could fail when a column was entirely null. The issue's original repro (`netstead clean remove-orphans` on the Leavenworth fixture) now succeeds. If you still hit it, cast the all-null column to a concrete type (or drop it) before the write, and add your case to the issue. The old `--engine pandas` workaround no longer exists: DuckDB is the only engine since #195.
* **Don't mutate tables outside the session.** Direct `net.links.expr.execute().assign(...)` writes bypass the session entirely and aren't rollback-able.
* **The session captures snapshots, not journals.** Memory cost scales with the size of the tables an op touches; the bulk `replace_table` path snapshots the whole affected table. Long sessions over large networks should commit periodically.

## See also

* [Query and update linked tables](query-and-update.md) — FK joins + the low-level `update_rows` / `delete_rows` / `add_rows` edit primitives these ops are built on.
* [Architecture](https://e-lo.github.io/netstead/corral/architecture/) — Session / EditResult / Diff design.
* [Validate a network](validate-network.md) — `sync.fk_stale` is the validator's way of telling you to re-read after an edit.
* [API reference](../reference/api.md) — `Session`, `EditResult`, `Diff`, `rollback`.
