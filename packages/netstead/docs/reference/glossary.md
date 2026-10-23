---
title: Glossary
audience: both
kind: reference
summary: GMNS-domain terms (link, lane, TOD, segment, …) and project conventions (sync state, scope, EditResult, Skill, MCP, …) defined in one place.
---

# Glossary

## Summary

Two sections: **GMNS domain** terms come from the spec itself and apply to any GMNS toolkit. **Project conventions** are Netstead + corral specific — names that appear in the API, CLI, and docs of this toolkit. Entries are alphabetical within each section.

## GMNS domain

**Allowed uses** — the set of uses permitted to traverse a link or lane (e.g., `auto, truck, transit`). Encoded as a comma-separated string on `link.allowed_uses` / `lane.allowed_uses` whose members name a `use_definition.use` or a `use_group.use_group` (a `use_group` row lists its members in its own comma-separated `uses` field). GMNS 0.97 has no `prohibited_uses` field. See [`use_definition`](table-of-tables.md#dimension-tables).

**Capacity** — the maximum hourly throughput of a link or lane under prevailing conditions, in vehicles per hour. Carried on `link.capacity` (and overridable per period via `link_tod.capacity`). Units are vehicles per hour per lane unless the producer notes otherwise; GMNS is silent on the LOS model used to compute it.

**Curb segment** — a managed length of curb (parking, loading zone, no-stopping). One row per curb segment in `curb_seg`, keyed to a `link_id`. New in recent GMNS versions; supports curb-management modeling.

**Facility type** — descriptor of a link's roadway class (e.g., `motorway`, `primary`, `tertiary`, `residential`, `service`). In GMNS 0.97 `link.facility_type` is free text — the spec does not enumerate it (`shared_categories.json` only defines `barrier`, `bike_facility`, `ped_facility`, `parking` categories, `movement_type` and `ctrl_type`). The quality rule pack reads it by name (e.g. `residential` for the high-speed-residential rule).

**Free speed** — the posted or signed speed limit, or the speed at which vehicles travel under uncongested conditions. Carried on `link.free_speed`; the unit is network-wide, declared in `config.speed` (usually `long_length` units per hour), not per link. Distinct from any modeled travel speed.

**Geometry** — a row in the optional `geometry` table holding a WKT LineString, referenced by `link.geometry_id`. When absent, the link is implicit straight-line from `from_node` to `to_node`. The geometry table exists so multiple links can share one shape (e.g., a divided highway represented as two opposing links sharing a centerline).

**GMNS** — General Modeling Network Specification. The Zephyr Foundation's open standard for routable transportation networks in tabular Frictionless Data Package form. See [What is GMNS?](../what-is-gmns.md).

**Lane** — a sub-element of a link with its own lane number, optional turn restrictions, and allowed uses. One row per travel lane per link in the `lane` table; `lane.lane_id` is the primary key and `lane.link_id` is the foreign key to `link.link_id`. A link with `lanes=3` typically has three corresponding `lane` rows.

**Link** — a directed edge in the routable network, from `from_node_id` to `to_node_id`. The required spine table; carries facility type, lanes, free speed, length, allowed uses. Undirected networks are represented by paired links (one per direction).

**Movement** — a turning movement at a node, from an inbound link to an outbound link. One row per allowed turn in the `movement` table; carries movement type (left/through/right/u-turn) and permitted uses. Used by signal-control tables to map phases to movements.

**Node** — a vertex in the routable network — intersection, dead-end, zone centroid. The required vertices table; carries x/y coordinates and optional `ctrl_type` (signal, stop, yield, no-control).

**Segment** — a sub-division of a link where attributes change mid-link (e.g., a lane drop, a facility-type change). One row per segment in the `segment` table; segments tile a link end-to-end. Used when link-level attributes are too coarse.

**Spec version** — the GMNS spec release the data conforms to. Netstead vendors 0.95, 0.96, and 0.97. Validation reports always include the spec version. The default is 0.97; override via `Network.from_source(path, spec_version="0.96")`.

**TOD (time-of-day)** — per-time-period overrides on link / lane / segment / movement attributes. Keyed by `timeday_id` into `time_set_definitions`. A period like `weekday_am_peak` (Mon-Fri 07:00-09:00) is defined once and referenced from every `*_tod` row that varies by that period.

**Zone** — a traffic analysis zone (TAZ) for demand modeling. Nodes carry an optional `zone_id`. The `zone` table holds polygon or centroid geometry; not every network has zones (routing-only networks often don't).

## Project conventions

**ApprovalRequired** — exception raised when a gated operation's cost estimate exceeds the approval threshold (default 180 seconds) and the caller has not set `approve=True`. See the cost-model section of [Architecture](https://e-lo.github.io/netstead/corral/architecture/#65-pooled-operations-cost-model).

**Auto-build threshold** — the network size (in nodes) above which a `netstead.scope` op logs an INFO message when it has to build the scipy graph index on first call (the build happens either way; the threshold only controls the log). Default 50,000 nodes. Configurable via the `NETSTEAD_AUTO_INDEX_THRESHOLD` environment variable. Pre-build explicitly with `net.build_indexes(graph=True)` to control timing.

**CORRAL_AUTO_APPROVE** — environment variable (`CORRAL_AUTO_APPROVE=1`) that bypasses the cost-model approval prompt for gated operations. Equivalent to CLI `--yes` or programmatic `approve=True`. Intended for batch / CI use.

**EditResult** — the value returned by every `netstead.clean` op. Carries the diff per affected table, the log entry for the operation, and a `_repr_html_` visual summary for notebook rendering. Integrated with `corral.editing.Session` for rollback.

**Engine** — the compute backend. corral has one: `IbisEngine` (lazy expressions over DuckDB). pandas / polars / pyarrow are I/O *formats* (`.to_pandas()` / `.to_polars()`), not compute engines. See `corral.engines`.

**NETSTEAD_AUTO_INDEX_THRESHOLD** — see *Auto-build threshold*.

**Lazy expression** — a `Table.expr` that has not been materialized. Operations on a lazy expression return a new lazy expression. Materialization happens explicitly via `.to_pandas()`, `.to_polars()`, `.collect()`, `.head()`, or `.count()`.

**MCP (Model Context Protocol)** — the open protocol Claude and other AI agents use to discover and call tools. Netstead ships an MCP server via `pip install 'netstead[mcp,graph]'`; start it with `netstead mcp serve`. Tools exposed: `describe_package`, `validate_package`, `list_tables`, `describe_network`, `quality_check`, `connected_components`, `scope_from_nodes` — all stateless, each taking a `source`. See the [MCP tools reference](../ai/mcp-tools.md) and [Architecture §6.9](https://e-lo.github.io/netstead/corral/architecture/#69-ai-accessibility).

**Network buffer** — a Dijkstra-bounded expansion in **graph distance** (using `link.length`). Used by `netstead.scope.from_node(id, network_buffer="200m")`. Distinct from spatial buffer.

**OutOfSyncWarning** — warning raised before `write()` or `validate(strict=True)` when any tracked foreign-key's source or target table hash has changed since the last validation. Indicates the in-memory FK graph may no longer match the data. Promote to an error with `strict=True`.

**Scope** — a `NetworkScope` is a (node_ids, link_ids) pair that, when applied via `.apply()`, returns a filtered `Network` with every other table pre-filtered by FK chain. Scopes are composable and chainable: `net.scope.from_nodes([1,2,3]).buffer_network("0.5mi").buffer_spatial(30)`. See [Architecture §6.2](https://e-lo.github.io/netstead/corral/architecture/#62-memory-efficient-scoping).

**Session** — `corral.editing.Session`, a context manager around one or more `EditResult`s with atomic rollback. Open with `with Session(net, log_path=...) as s: ...`; `s.rollback()` (no arguments) reverses every edit of the open session in LIFO order. The parquet log is written on clean exit only when `log_path=` is given; `corral.editing.rollback(pkg, log_path, to=session_id_or_datetime)` replays that log later to reverse edits selectively. (The Workbench's `netstead.workbench.Session` is a different class — the action bus of the local web app.)

**Severity / Category / Issue** — see the [corral API reference](https://e-lo.github.io/netstead/corral/reference/api/). `Severity` is one of `ERROR`, `WARNING`, `INFO` (serialized lower-case). `Category` discriminates rule families: `SCHEMA`, `STRUCTURAL`, `FOREIGN_KEY`, `SYNC_STATE`, `DATA_QUALITY` — a quality finding carries `DATA_QUALITY` plus one of the three severities. `Issue` is the per-finding record carried in a `ValidationReport`.

**Skill (Claude Code Skill)** — a packaged set of instructions and prompts a Claude Code agent can load on demand. Netstead ships six (`gmns-author`, `gmns-build`, `gmns-validate`, `gmns-convert`, `gmns-clean`, `corral-validate`) under `skills/` in the repo. Install with `claude code skill add <git-url>#path=skills/<name>`. See [Architecture §6.9](https://e-lo.github.io/netstead/corral/architecture/#69-ai-accessibility).

**Spatial buffer** — a shapely-bounded expansion in **CRS units** (degrees for WGS84, meters for projected CRSs). Used by `netstead.scope.from_link(id, spatial_buffer_m=...)` and the `buffer_spatial()` chain method. Distinct from network buffer.

**Sync state** — content-hash tracking on every table via `DirtyTracker` (in `corral.validation.sync_state`). Records source + target hashes per FK at validation time so the validator can detect when a previously-checked FK has gone stale. Direct DataFrame mutations bypass the tracker — call `net.invalidate("link")` to force.

## See also

* [Table of tables](table-of-tables.md) — catalog of every GMNS resource referenced above.
* [Schema reference](spec.md) — field-level reference for the vendored spec.
* [Architecture](https://e-lo.github.io/netstead/corral/architecture/) — design rationale for the project-convention terms.
