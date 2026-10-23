---
title: MCP tools reference
audience: both
kind: reference
summary: Complete reference for the seven MCP tools the netstead server exposes — three generic corral tools plus four GMNS-aware ones.
---

# MCP tools reference

## Summary

`netstead mcp serve` runs a stdio MCP server that exposes seven tools: three generic Frictionless-package tools inherited from `corral.mcp` (`describe_package`, `validate_package`, `list_tables`), plus four GMNS-aware tools (`describe_network`, `quality_check`, `connected_components`, `scope_from_nodes`). Every tool is **stateless** — each call takes a `source` (path or URL) and loads the package fresh. No session, no cache, no cross-call mutation. A stateful surface (editing sessions, indexed scope ops) is deferred.

All tools return JSON-shaped dicts (or, for `list_tables`, a list of strings); shapes are stable inside a major version. The example outputs below are what the tools return for the bundled Leavenworth fixture (`packages/netstead/netstead/fixtures/leavenworth/csv`: 339 links, 121 nodes, nine CSV tables, no `datapackage.json`). For wiring the server into Claude Desktop or Claude Code, see [Wire the MCP server](../cookbook/serve-mcp.md).

## Tools

### `describe_package(source)`

Generic package overview. Available on any Frictionless package, not just GMNS. It loads the source as a bare `corral.dataset.Package`, so on a CSV directory without a `datapackage.json` the inferred package `name` is `"auto"`; `describe_network` is the GMNS-aware variant.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Filesystem path or URL to a package directory / `datapackage.json` |

**Returns** — `dict`:

```json
{
  "source": "<echoed input>",
  "name": "auto",
  "engine": "IbisEngine",
  "table_count": 9,
  "tables": [
    {"name": "geometry", "rows": 339, "columns": ["geometry_id", "geometry"]},
    {"name": "lane", "rows": 429, "columns": ["lane_id", "link_id", "lane_num", "allowed_uses", "width"]},
    {"name": "link", "rows": 339, "columns": ["link_id", "name", "from_node_id", "to_node_id", "..."]},
    {"name": "node", "rows": 121, "columns": ["node_id", "name", "x_coord", "y_coord", "node_type", "ctrl_type", "osm_node_id"]}
  ]
}
```

`rows` / `columns` may be `null` for an individual table if its count fails (e.g. unreadable file).

**Example**

```json
{"name": "describe_package", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv"}}
```

### `validate_package(source)`

Run corral's **generic** validation pipeline (structural + schema + foreign-key + sync-state) against whatever spec the source carries, and return the report. It does **not** apply the GMNS spec: on a bare CSV directory such as the Leavenworth fixture there is no spec to check against, so `spec_version` is `null` and the findings are one `structural.spec_missing` info (no `datapackage.json`, no spec given) and nine `structural.unexpected_resource` warnings (one per CSV the empty spec does not declare). For GMNS validation use `netstead validate <source> --json`, which emits the same report shape with the vendored spec applied.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Path or URL to the package |

**Returns** — `dict`, the canonical `ValidationReport.to_dict()` shape:

```json
{
  "report_version": 1,
  "spec_version": null,
  "source": "<echoed input>",
  "created_at": "2026-10-19T13:12:08.078865",
  "metadata": {},
  "summary": {"error": 0, "warning": 9, "info": 1, "data_quality": 0, "is_clean": false},
  "issues": [
    {
      "severity": "info",
      "category": "structural",
      "code": "structural.spec_missing",
      "message": "package source '...': no spec was given and none was found beside the source, so no table was checked against a schema",
      "table": null,
      "column": null,
      "row": null,
      "fix_hint": "Pass spec= to Package.from_source. Only a datapackage.json inside a source directory is discovered automatically; one inside a zip, next to a single file, or at a remote prefix is not.",
      "extra": {}
    },
    {
      "severity": "warning",
      "category": "structural",
      "code": "structural.unexpected_resource",
      "message": "package source '...': resource 'geometry' was found but is not declared in the spec",
      "table": "geometry",
      "column": null,
      "row": null,
      "fix_hint": "Add geometry to the spec, rename it to match an existing declared resource, or remove it from the source.",
      "extra": {"path": ".../geometry.csv", "format": "csv"}
    }
  ]
}
```

`severity` is one of `error`, `warning`, `info`; `category` is one of `schema`, `structural`, `foreign_key`, `sync_state`, `data_quality`. `summary.is_clean` is `true` when there are no `error` or `warning` issues; `info` issues do not affect it. The example above shows the `spec_missing` info and the first of the nine warnings.

**Example**

```json
{"name": "validate_package", "arguments": {"source": "s3://my-bucket/regional/datapackage.json"}}
```

### `list_tables(source)`

Cheap variant of `describe_package` — returns just the sorted list of table names with no row counts. Useful as a first call when the agent doesn't know what's in a package.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Path or URL |

**Returns** — `list[str]`:

```json
["geometry", "lane", "link", "link_tod", "node", "signal_controller", "time_set_definitions", "use_definition", "use_group"]
```

**Example**

```json
{"name": "list_tables", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv"}}
```

### `describe_network(source)`

GMNS-aware version of `describe_package`. Loads the source as a `netstead.Network` (so the GMNS spec is applied and `name` is `"gmns"`) and surfaces the fields an agent typically wants up front — spec version, link/node counts — without iterating tables.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Path or URL to a GMNS network |

**Returns** — `dict`:

```json
{
  "source": "<echoed input>",
  "name": "gmns",
  "spec_version": "0.97",
  "engine": "IbisEngine",
  "links": 339,
  "nodes": 121,
  "table_count": 9,
  "tables": ["geometry", "lane", "link", "link_tod", "node", "signal_controller", "time_set_definitions", "use_definition", "use_group"]
}
```

`links` / `nodes` may be `null` if the network doesn't have those tables or the count fails.

**Example**

```json
{"name": "describe_network", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv"}}
```

### `quality_check(source)`

Run the GMNS data-quality rule pack (high-speed-residential, disconnected components, lane-count mismatch, duplicate-near-nodes, sharp-angle bends, implausible v/c, missing critical fields). Requires the `[graph]` extra (scipy) on the server side for the connectivity rule.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Path or URL to a GMNS network |

**Returns** — same `ValidationReport.to_dict()` shape as `validate_package`. The report's own `spec_version` and `source` are `null` (the rule runner does not fill them in); every issue has `category: "data_quality"`:

```json
{
  "report_version": 1,
  "spec_version": null,
  "source": null,
  "created_at": "2026-10-19T13:12:08.141843",
  "metadata": {},
  "summary": {"error": 0, "warning": 271, "info": 0, "data_quality": 271, "is_clean": false},
  "issues": [
    {
      "severity": "warning",
      "category": "data_quality",
      "code": "quality.high_speed_residential",
      "message": "link row 0 (link_id=1): facility_type='residential' with free_speed=40.0 > threshold 35.0.",
      "table": "link",
      "column": "free_speed",
      "row": 0,
      "fix_hint": "If the speed is correct, change facility_type to a higher class (e.g. 'tertiary', 'secondary').",
      "extra": {}
    }
  ]
}
```

Default-pack issues are WARNING / INFO — the GMNS spec is silent on these checks, but they catch common data-quality misses. Customize thresholds via the Python API ([Customize the data-quality rule pack](../cookbook/customize-quality.md)).

**Example**

```json
{"name": "quality_check", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv"}}
```

### `connected_components(source)`

Count weakly-connected components in the network graph. Requires the `[graph]` extra (scipy) on the server side.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `source` | string | Path or URL to a GMNS network |

**Returns** — `dict`:

```json
{
  "source": "<echoed input>",
  "component_count": 1,
  "sizes": [121]
}
```

`component_count == 1` means the network is fully connected. `sizes` is the descending list of node counts per component — `[121]` for the fully-connected 121-node Leavenworth fixture; `[60, 12, 3]` for a three-component network whose largest piece has 60 nodes.

**Example**

```json
{"name": "connected_components", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv"}}
```

### `scope_from_nodes(source, node_ids, path_between)`

Build a network-aware scope from a list of seed node ids and return the resulting node + link sets.

**Parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `source` | string | — | Path or URL to a GMNS network |
| `node_ids` | list[int] | — | Seed node ids |
| `path_between` | bool | `true` | If true, includes all nodes/links on shortest paths between every pair of seeds. If false, the docstring promises the seeds plus their incident links, but today the scope holds only the seed nodes and no links (seeds `[1, 25, 50]` on Leavenworth give 3 nodes, 0 links, although 20 links touch them). |

**Returns** — `dict` (intended; see the known issue). For seeds `[1, 25, 50]` on Leavenworth the scope has 14 nodes and 28 links:

```json
{
  "source": "<echoed input>",
  "seed_node_ids": [1, 25, 50],
  "path_between": true,
  "result_node_count": 14,
  "result_link_count": 28,
  "node_ids": [1, 2, 3, 10, 11, 15, 25, 50, 63, 74, 78, 96, 99, 100],
  "link_ids": [1, 4, 5, 6, 8, 9, 27, 29, "..."]
}
```

The returned ids are sorted. For chaining (union / intersect / buffer), drop into the Python API — the MCP surface only exposes the leaf operation.

**Example**

```json
{"name": "scope_from_nodes", "arguments": {"source": "packages/netstead/netstead/fixtures/leavenworth/csv", "node_ids": [1, 25, 50], "path_between": true}}
```

## See also

* [Wire the MCP server to Claude Code / Claude Desktop](../cookbook/serve-mcp.md) — host configuration + transport details.
* [AI surface](https://e-lo.github.io/netstead/corral/ai/) — how MCP fits with `llms.txt`, `api-index.json`, and the Claude Code Skills.
* [API reference](../reference/api.md) — the underlying Python symbols each tool wraps.
