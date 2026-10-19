---
title: Build a scope from seed nodes
audience: users
kind: howto
summary: Network-aware scope from a list of node ids — BFS path-between, FK pushdown, chainable set ops.
---

# Build a scope from seed nodes

## When to use this

You have a list of node ids (e.g. study-area boundary nodes, signalised intersections, a corridor's endpoints) and want the subgraph that connects them — every link on a shortest path, every dependent `lane` / `link_tod` / `signal_*` row, with foreign keys pre-filtered.

## Quick example

Build a scope from three seed nodes. With `path_between=True` (the default), BFS shortest paths between every pair of seeds determine what's included; `.apply()` materialises the result as a normal `Network`:

```python
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.scope import from_nodes

net = Network.from_source(leavenworth.csv_dir())
sub = from_nodes(net, [1, 25, 50], path_between=True).apply()
print(f"scoped: {sub.links.count()} links, {sub.nodes.count()} nodes")
```

Expected:

```text
scoped: 28 links, 14 nodes
```

![Scoped Leavenworth subnetwork rendered on a folium map](../assets/screenshots/leavenworth-scoped-map.png){ .screenshot }
*The scope from seed nodes 1, 25, and 50. Shortest paths between the seeds determine which interior links are kept; every dependent `lane`, `link_tod`, and signal row is FK-filtered automatically.*

## Step-by-step

### 1. Install the `[graph]` extra

`from_nodes` walks the network through a scipy sparse-graph index (`netstead.graph.GMNSGraph`); scipy ships in the `[graph]` extra. Without it the first scope call raises `ImportError: netstead.graph requires the [graph] extra: pip install 'netstead[graph]'`:

```bash
pip install 'netstead[graph]'
```

### 2. Decide `path_between=True` vs `False`

* `path_between=True` (default) — runs BFS shortest paths between every pair of seed nodes and includes all nodes + links on those paths. Gives you the *connected subgraph* containing the seeds.
* `path_between=False` — keeps just the seed nodes and their incident links. Useful when the seeds already describe a closed region and you don't want extra interior links.

### 3. Build the scope (lazy)

A `NetworkScope` is a description of *which rows* belong to the subset, not the subset itself. `scope.provenance` records how it was built (operation, seeds, parameters) for audit / debugging:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.scope import from_nodes

net = netstead.read(leavenworth.csv_dir())
scope = from_nodes(net, [1, 25, 50], path_between=True)

print(f"nodes in scope:    {len(scope.node_ids)}")
print(f"links in scope:    {len(scope.link_ids)}")
print(f"provenance:        {scope.provenance}")   # str describing the op
```

### 4. Apply to materialise a sub-network

`.apply()` returns a normal `Network` you can validate, run quality checks on, or write out. Every table is pre-filtered by FK chain — lane rows whose `link_id` isn't in scope are dropped, signal rows for missing controllers are dropped, etc. A table the scope empties entirely (e.g. `signal_controller` when no signals fall in the subgraph) survives as a zero-row table with its schema intact:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.scope import from_nodes

net = netstead.read(leavenworth.csv_dir())
scope = from_nodes(net, [1, 25, 50], path_between=True)

# Every table is FK-filtered to the scope:
#   link / node    — only ids in scope.link_ids / scope.node_ids
#   lane, link_tod — only rows whose link_id is in scope
#   geometry       — only ids referenced by surviving links
sub = scope.apply()
print(f"{sub.links.count()} links, {sub.nodes.count()} nodes")
```

### 5. Chain with set ops + buffers

Scopes compose with union / intersect / subtract and with network / spatial buffering. Build a corridor scope, union it with a second scope, then add a 100 m network buffer — all in one expression:

```python
import netstead
from netstead.fixtures import leavenworth
from netstead.scope import from_nodes

net = netstead.read(leavenworth.csv_dir())
corridor = from_nodes(net, [1, 25, 50])
downtown = from_nodes(net, [10, 11, 12])

combined = corridor.union(downtown).buffer_network("100m")
# .intersect(other) and .subtract(other) compose the same way.
# .buffer_spatial(distance) buffers by straight-line distance in the geometry
# column's CRS units; it needs an inline link.geometry column and is a silent
# no-op on Leavenworth, which has none.
sub = combined.apply()
print(f"combined scope: {sub.links.count()} links, {sub.nodes.count()} nodes")
```

Expected:

```text
combined scope: 58 links, 24 nodes
```

### 6. CLI equivalent

The same operation runs from the shell. Add `--json` to any `netstead` (or `corral`) CLI command and the output becomes a single machine-readable JSON document on stdout — pipe into `jq`, save to a file, feed to a script or AI agent. Default output is human-readable rich panels:

```bash
netstead scope from-nodes packages/netstead/netstead/fixtures/leavenworth/csv 1 25 50 --json
```

Expected:

```json
{
  "node_count": 14,
  "link_count": 28,
  "node_ids": [1, "2", "3", "10", "11", "15", 25, 50, "63", "74", "78", "96", "99", "100"],
  "link_ids": [1, 4, 5, 6, 8, 9, 27, 29, 30, 31, 34, 44, 46, 77, 79, 160, 196, 197, 225, 226, 238, 239, 283, 284, 290, 292, 293, 295],
  "provenance": "from_nodes([1, 25, 50], path_between=True)"
}
```

Every id in `node_ids` and `link_ids` is a plain integer.

## Common variations

???+ note "Default — multi-node BFS with path_between"
    Most common pattern: a handful of seed ids, paths between them filled in automatically.

    ```python
    sub = from_nodes(net, [1, 25, 50], path_between=True).apply()
    ```

??? note "Single seed with a network-distance buffer"
    Expands outward from one node along network edges up to a budget.

    ```python
    from netstead.scope import from_node
    sub = from_node(net, 1, network_buffer="200m").apply()
    ```

??? note "Single link with a network or spatial buffer"
    Useful for corridor studies. Pass exactly one of `network_buffer` (graph distance along `link.length`, metres or a string like `"200m"`) or `spatial_buffer_m` (straight-line distance in the geometry column's CRS units — degrees on an EPSG:4326 network, despite the name). The network form works on Leavenworth:

    ```python
    from netstead.scope import from_link
    sub = from_link(net, 42, network_buffer="200m").apply()
    ```

    The spatial form needs an inline `link.geometry` column. Leavenworth keeps its shapes in the `geometry` table, so this raises `ScopeError: from_link(spatial_buffer_m=...) requires a geometry column on links.`:

    <!-- doctest: skip -->
    ```python
    sub = from_link(net, 42, spatial_buffer_m=0.001).apply()   # ~100 m in degrees
    ```

??? note "Point with a spatial buffer"
    Scope = every link within `spatial_buffer_m` of an `(x, y)` point, in the geometry column's CRS units. Like the spatial `from_link`, it needs an inline `link.geometry` column and raises `ScopeError: from_point requires a geometry column on links.` on Leavenworth:

    <!-- doctest: skip -->
    ```python
    from netstead.scope import from_point
    sub = from_point(net, (-120.66, 47.59), spatial_buffer_m=0.005).apply()   # ~500 m in degrees
    ```

??? note "Whole connected component"
    Drops everything not reachable from the seed via the graph.

    ```python
    from netstead.scope import connected_component
    sub = connected_component(net, seed_node_id=1).apply()
    ```

??? note "Nodes tagged with a zone id"
    `from_zone` keeps every node whose `node.zone_id` is in the list, plus the links between those nodes. It is an attribute filter, not a polygon scope, and it requires a `zone_id` column on the node table — Leavenworth has none, so this raises `ScopeError: from_zone requires a 'zone_id' column on the node table.`:

    <!-- doctest: skip -->
    ```python
    from netstead.scope import from_zone
    sub = from_zone(net, [12]).apply()
    ```

## Pitfalls

* **Auto-build threshold.** The scipy `GMNSGraph` is built on the first scope call and cached on the network. On networks above 50k nodes that first call logs an INFO notice (`auto-building graph index over N nodes`) because the build can take a few seconds. Pre-build with `net.build_indexes(graph=True)` to control timing, or raise the bar with `NETSTEAD_AUTO_INDEX_THRESHOLD=200000`.
* **scipy is in `[graph]`.** On a pure `pip install netstead` the first scope call raises `ImportError: netstead.graph requires the [graph] extra: pip install 'netstead[graph]'`.
* **`path_between=True` is O(seeds²)** in shortest-path calls. For 100s of seeds, prefer an attribute scope (`from_zone`) or the generic [bounding-box scope](https://e-lo.github.io/netstead/corral/cookbook/scope-bbox/), or build the graph index first.
* **Spatial scope (`from_point`, `from_link(spatial_buffer_m=…)`, `buffer_spatial`) needs an inline `geometry` column on links, and the `_m` parameters are in the geometry column's CRS units** — degrees on an EPSG:4326 network, not metres. Leavenworth carries geometry in a separate `geometry` table via `link.geometry_id`, so `from_point` / `from_link(spatial_buffer_m=…)` raise `ScopeError` and `buffer_spatial` is a no-op. `netstead.semantics.assemble_link_geometry(net)` resolves each link's shape into a `(link_id, geometry, source)` table, but nothing in netstead writes that back onto `link.geometry` for you. Network buffers (`buffer_network`, `from_link(network_buffer=…)`) work straight off the FK graph — no geometry needed.

## See also

* [Bounding-box and polygon scope](https://e-lo.github.io/netstead/corral/cookbook/scope-bbox/) — generic spatial scope on any package.
* [API reference](../reference/api.md) — `netstead.scope.from_nodes`, `Scope.apply`, `Scope.union`, ...
