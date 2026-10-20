---
title: Architecture
audience: contributors
kind: reference
summary: How netstead + corral are built today — repo layout, module maps, engine + I/O defaults, validation, editing, CLI, server, MCP and the rationale behind each choice.
---

# Software architecture — Netstead + corral v1.0

This document is the **single source of truth** for the *current* software design of netstead and corral. It exists so that any contributor — human or AI sub-agent — landing in the repo can pick up cold and make decisions consistent with the rest of the work.

For the **GMNS data model** (link/node/lane/etc. ER diagrams), see [gmns-data-model.md](https://e-lo.github.io/netstead/netstead/gmns-data-model/). Decisions (ADRs), PRDs, feature designs and implementation plans live in [`docs/design/`](https://github.com/e-lo/netstead/tree/main/docs/design); start at its [README](https://github.com/e-lo/netstead/blob/main/docs/design/README.md), which indexes every record with its status.

---

## 1. Mission

Netstead is a Python toolkit for the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS), the Zephyr Foundation's open standard for routable transportation network data. v1.0 is a major rewrite of the v0.3.x alpha to deliver:

- **Regional-scale performance** — load + scope + validate Bay-Area-class networks without melting RAM or CPU
- **Modern formats** — Parquet (default persistent), DuckDB (default API download), zipped CSV, CSV; remote URLs with credentials
- **Foreign-key validation with sync-state awareness** — warn on writes when a network is mid-edit and FKs are stale
- **Network-aware scoping** — bbox + polygon + BFS-induced subgraph + network-distance buffer + spatial buffer from any link/point/node, with eager spatial+graph indexes for fast repeat queries (memory-for-compute tradeoff)
- **Data-quality warnings** beyond the spec (high-speed-residential, disconnected components, etc.) via configurable plugin pattern
- **Editing with atomic rollback + audit log** (`netstead[clean]`)
- **Self-hostable API server** (`netstead[server]`) — FastAPI + auto-OpenAPI; we ship the package + Dockerfile, not a service
- **AI accessibility** — Claude Code Skills (in-repo, git-URL install) + MCP server (`netstead[mcp]`); zero hosting commitment
- **Three usage surfaces** — interactive CLI, Jupyter notebook, programmatic API
- **Awesome docs** — for both human and AI consumers

Full requirements list (29 items) traces to phase tasks via the GitHub issue tree.

## 2. Repo layout (monorepo, two PyPI packages)

```
Netstead/                                       # git repo
├── pyproject.toml                            # uv workspace root + shared dev tooling
├── uv.lock
├── packages/
│   ├── corral/                            # PyPI package #1 — generic engine
│   │   ├── pyproject.toml
│   │   └── corral/
│   └── netstead/                               # PyPI package #2 — GMNS-specific (depends on corral)
│       ├── pyproject.toml
│       └── netstead/
│           └── spec/{0.95,0.96,0.97}/        # vendored upstream spec versions
├── skills/                                   # Claude Code Skills (git-URL install)
├── docs/                                     # mkdocs site (covers both packages + GMNS itself)
├── scripts/
└── .github/workflows/                        # tests / publish-corral / publish / documentation / spec-sync / bench / release-drafter
```

**Per-package release tags:** `corral-vX.Y.Z`, `netstead-vX.Y.Z`. PyPI trusted publishing fires per-tag.

**Branch model:** `main` is the trunk; short-lived feature branches merge into `main` via PR. The v0.3 code is preserved on `legacy-gmnspy`.

## 3. Two packages, one principle

`corral` holds **generic primitives / frameworks**. `netstead` holds the **GMNS-specific assembly** that composes those primitives with domain knowledge. The same composition pattern applies to every cross-cutting concern:

| Concern | corral (generic) | netstead (GMNS-specific) |
|---|---|---|
| editing | `editing/` (Edit/Diff/Session/Rollback framework) | `clean/` (simplify_geometry, merge_close_nodes, …) |
| HTTP server | `api/` (FastAPI primitives, routers, OpenAPI helpers) | `server/` (assembled app, GMNS endpoints, Dockerfile) |
| MCP | `mcp/` (server primitives, tool decorators) | `mcp/` (GMNS tool registrations) |
| CLI | `cli/` (validate, convert, info; entry: `corral`) | `cli/` (GMNS overrides of validate / info / convert plus quality, doctor, bench, bench-suite, build, select, select-serve, viz, app, spec, server, mcp, clean, scope, index, llm; entry: `netstead`) |
| Quality | `quality/` (Rule base class, plugin discovery; **no domain rules**) | `quality/` (GMNS rule pack via entry point) |
| Notebook | `notebook/` (`_repr_html_` for Package/Table/ValidationReport/EditResult) | `notebook/` (Network repr + scope widgets) |
| Validation | `validation/` (schema, structural, FK, sync-state) | (uses corral validation as-is) |
| Engines | `engines/` (Engine protocol + the one DuckDB-via-ibis engine) | (uses) |
| I/O | `io/` (FormatAdapter ABC + csv/parquet/duckdb/zipcsv/remote) | (uses) |
| Dataset | `dataset/` (lazy `Package`, `Table`, `View`) | `network.py` (`Network` = `Package` + GMNS accessors) |
| Spec | `spec/` (Pydantic Frictionless models, multi-version loader) | `spec/<version>/` (vendored GMNS schemas) |

**Hard rule (lint-enforced via import-linter):** `corral` may not import from `netstead`. Optional-extra submodules in netstead (`clean`/`server`/`mcp`) may not be imported from netstead core modules. **No raw SQL strings** anywhere except inside `corral.engines.ibis_engine`.

Promotion criterion to extract `corral` to a separate repo: a second consumer (e.g., GTFSpy) has consumed it for ≥1 month with no breaking-change requests. Until then, monorepo.

## 4. Module map — corral

```
corral/
├── __init__.py   # top-level re-exports: Package, Table, read(), OutOfSyncWarning / OutOfSyncError
├── types.py      # shared type aliases
├── spec/         # Pydantic v2 models for Frictionless DataPackage/Resource/Schema/Field/ForeignKey/MissingValues/SharedCategory + loader (resolves $ref, shared_categories) + multi-version
├── engines/      # Engine protocol + IbisEngine (DuckDB backend) — the only compute engine; pandas/polars/arrow are I/O formats
├── io/           # FormatAdapter ABC + csv / parquet (opt-in Hive partitioning) / duckdb / zipcsv / remote (fsspec) + credentials cascade
├── validation/   # schema_check + foreign_keys + structural + sync_state (DirtyTracker) — each appends Issues to a ValidationReport
├── operations/   # cost_model (OperationCost) + gating (>30s estimate, >3min approval) + pool/batch + progress (rich, notebook-aware)
├── dataset/      # Package / Table (lazy ibis-backed) / view.py (from_bbox / from_polygon / from_geometry_buffer) / geometry.py (WKT<->WKB, GeoParquet bounds) / filter.py (dict-spec row filters)
├── reports/      # types.py (ValidationReport, Issue, Severity = error/warning/info, Category = schema/structural/foreign_key/sync_state/data_quality) + rich console / JSON / interactive single-file HTML renderers
├── docgen/       # markdown + llms.txt + machine-readable api-index.json
├── editing/      # generic Edit / Diff / EditResult / Session / rollback() framework (no domain semantics)
├── api/          # FastAPI primitives (build_app, PackageRegistry, auth deps) — FastAPI itself is not a dbcorral dependency
├── mcp/          # MCP server primitives (build_server: describe_package / validate_package / list_tables)
├── cli/          # generic typer CLI: validate / convert / info (+ approval prompts, render helpers)
├── quality/      # generic rule framework (Rule protocol, RuleConfig, entry-point plugin discovery, run_quality)
├── notebook/     # generic _repr_html_ for Package/Table/ValidationReport/EditResult
└── fixtures/     # bundled generic sample package (author / venue / book) for corral's own doctests + docs
```

## 5. Module map — netstead

```
netstead/
├── spec/<version>/    # Vendored GMNS spec JSONs per supported version (0.95/, 0.96/, 0.97/, …)
├── network.py         # Network = corral.Package + GMNS-aware accessors (.links, .nodes, .segments, …) + add_*/update_* routed through DirtyTracker
├── semantics/         # connectivity, geometry assembly from geometry_id, TOD resolution
├── scope/             # network-aware scope ops: from_nodes, from_node, from_link, from_point, connected_component, from_zone
├── indexes/           # spatial (shapely STRtree) + graph (via netstead.graph) build/cache/load; sidecar parquet keyed on content hash
├── graph/             # OPTIONAL [graph] — scipy-CSR routing: components, shortest paths, isochrones, nearest-node snap
├── quality/           # GMNS rule pack: high-speed-on-residential, disconnected components, lane-count mismatch, …
├── clean/             # OPTIONAL [clean] — simplify_geometry, merge_close_nodes, snap_to_reference, …; uses corral.editing for rollback
├── server/            # OPTIONAL [server] — assembled FastAPI app on top of corral.api primitives
├── mcp/               # OPTIONAL [mcp] — assembled MCP server on top of corral.mcp primitives
├── cli/               # GMNS commands registered onto the corral typer app
├── notebook/          # Network._repr_html_ + scope-builder ipywidget; extends corral.notebook
├── osm/               # OPTIONAL [osm] — build GMNS from OpenStreetMap (Overpass/Nominatim, maintained tag mappings)
├── overture/          # OPTIONAL [overture] — build GMNS from Overture Maps (mirrors osm/)
├── config.py          # layered settings (default / user / project / env / session)
├── workbench/         # `netstead app`: Session + typed Action bus, registry, jobs, SSE, FastAPI server, ES-module front end
├── llm/               # OPTIONAL [nl] — provider adapters (Anthropic / OpenAI / Gemini / Ollama over httpx), model catalog, keyring
├── select/            # natural-language selection → validated GMNS link_id set (2026-09-23 design)
├── viz/               # binary render buffers, styling and paged DuckDB table reads used by the Workbench
├── map/               # embeddable Leaflet map component + edit log (ProjectCard-shaped YAML)
├── reports/           # findings CSV/XLSX writers
├── bench/             # benchmark harness (time + peak memory) behind `netstead bench`
└── fixtures/leavenworth/  # bundled tiny GMNS network for tests + docs
```

### 5.1 Workbench (`netstead.workbench`)

`netstead app SOURCE` serves one live editing session as a local web app (needs the `[server]` extra). The pieces, all under `packages/netstead/netstead/workbench/`:

- **`Session`** (`session.py`) — the action bus. Holds the open networks, settings and history; `dispatch()` / `dispatch_recorded()` run one typed Action and record a `HistoryEntry` that knows how to undo itself.
- **Actions + `ActionRegistry`** (`actions.py`) — Pydantic models for everything the UI can ask for (`OpenNetwork`, `Select`, `Filter`, `EditCells`, `RunValidation`, `SaveNetwork`, ...). The registry is the discriminated union the HTTP layer parses requests into; plugins register extra Action types onto it.
- **Jobs + events** (`jobs.py`, `events.py`) — long-running actions run as background `Job`s via `JobRunner`; progress and state changes fan out through an `EventBus` to Server-Sent-Events subscribers on `GET /events`.
- **Plugins** (`plugins/`) — discovered from the `netstead.workbench.plugins` entry-point group. Each entry point is a zero-argument factory returning a `WorkbenchPlugin` (actions, routes, settings, an optional ES-module front end mounted at `/plugins/<id>`); a plugin that fails to install is recorded in `Session.plugin_status`, never fatal.
- **HTTP layer** (`server.py`, `routes/`) — `build_app(session)` assembles the FastAPI app: core / network / select / edit / io / llm / assistant routers plus the static front end.
- **Front end** (`static/`) — plain ES modules (`index.html` loads `/static/js/main.js` as `type="module"`), no bundler or build step.

## 6. Defaults & key design decisions

### 6.1 Engine + I/O

- **One compute engine: DuckDB, driven through ibis.** Lazy expressions throughout. pandas / polars / Arrow are I/O formats only — frames come in via `from_arrow` / `from_records` and go out via `.to_pandas()` / `.to_polars()`. There is no per-call engine switch. Decision record: [engine strategy ADR](https://github.com/e-lo/netstead/blob/main/docs/design/2026-10-01-engine-strategy-reevaluation.md) (pandas and polars engines removed in #195).

    *Why ibis vs alternatives?* Writing SQL directly couples query intent to one dialect and makes lint-based composition checks brittle; agents would have to parse SQL strings to reason about intent. SQLModel is ORM-shaped — fine for transactional row work, wrong shape for analytic column work over millions of rows. Pandas-only forces eager materialisation, which kills the regional-scale story (Bay-Area links don't fit RAM-comfortably on a laptop). Polars-only gives lazy evaluation but locks us into one backend and one expression language. Ibis is the only option that gives lazy expressions, lazy pushdown into DuckDB, and a clean escape valve via `.to_pandas()` / `.to_polars()` when the consumer wants a familiar frame.

    *Why DuckDB as the default ibis backend?* SQLite is single-table-write-locked and has no spatial pushdown — fine for a config store, wrong for a network. A PostgreSQL backend would force every user to stand up a server, which kills the laptop-friendly story. In-memory pandas misses the whole point of lazy evaluation. DuckDB is single-file, embeddable, has native Parquet reads (with predicate pushdown), a working spatial extension, no server to run, and is RAM-resident at regional scale — exactly the laptop-to-server gradient the toolkit targets.

- **No raw SQL strings** anywhere except inside `corral.engines.ibis_engine`. Lint-enforced by `scripts/lint_no_sql.py`.

    *Why this rule?* Once a SQL string leaks into business logic, the composition contract breaks — any backend change (or ibis upgrade) suddenly requires a dialect audit across the whole codebase. Keeping SQL inside one module future-proofs the engine layer and lets AI agents reason about query intent through ibis expressions rather than string-parsing SQL. The lint script is cheap; the long-term churn it prevents is not.

- **I/O front door:** `corral.read(source, *, format=None, credentials=None, engine=None, scope=None, spec=None, tables=None)`. `netstead.read(source, *, engine=None, spec_version=None, tables=None)` wraps it with the vendored GMNS spec.
- **Format detection:** explicit `format=` overrides; else extension sniff (`.parquet`, `.csv`, `.csv.zip`, `.zip`, `.duckdb`); else `FormatAdapter.probe()` chain.
- **Adapter registration refuses collisions.** `register_adapter` raises `DuplicateAdapterError` when a different adapter class already holds the name, or another adapter owns one of its extensions or URL schemes; `replace=True` overrides on purpose. Re-registering the same class (a module reload) just replaces the instance.

    *Why refuse rather than overwrite?* Adapters from third-party packages (entry-point discovery, when it lands) load in an order nobody controls. Silent overwrite would let that order decide which package reads `.xlsx`; an error at import names both parties.
- **Defaults:** API/URL downloads default to **DuckDB**; persistent local writes default to a **Parquet directory** — an extension-less or `.parquet` destination gets one GeoParquet file per table (`link.parquet`, `node.parquet`, ...), no `datapackage.json`. Hive partitioning is opt-in via `ParquetAdapter.write(expr, dest, engine, partition_by=[...])`; `Package.write` does not partition by default.

    *Why partitioned Parquet for persistent writes?* A single Parquet file works until the network grows past laptop-memory scale, at which point you want partition pruning on bbox / zone scope ops — the dispatcher pushes the predicate down and only reads the relevant partitions. CSV loses dtypes, has no predicate pushdown, isn't splittable, and balloons disk footprint. A single `.duckdb` file is great for downloads but not for modelling tool interop: every GIS tool reads Parquet natively, very few read DuckDB. Partitioned Parquet is the only format that is columnar, splittable, predicate-pushdown-friendly, and readable by every downstream tool a modeller might use.

    *Why DuckDB for URL / API downloads?* A single `.duckdb` file round-trips multi-table packages with zero schema loss (Parquet would need a sidecar manifest; CSV would need a zip), and the consumer can query it in-place without unpacking. For an API response where the consumer is most likely going to load → query → discard, this is strictly better than handing them a directory tree.

- **Credentials cascade (corral-owned):** kwarg → `CORRAL_CRED_<host>_TOKEN` env → `keyring` (service `"corral"`) → `.netrc`. fsspec underneath. The env prefix lives in `corral` because credential resolution is a generic concern; `netstead` consumes it as-is.
- **Persistent layout today** (what `Package.write("mynet.gmns")` produces):
  ```
  mynet.gmns/
    link.parquet
    node.parquet
    ...
  ```
  The spec is not written alongside — keep the `datapackage.json` you loaded with and pass it as `spec=` on reload (only the zip output inlines one). A `_netstead_meta.json` sidecar (spec version, write timestamp, dirty flags, per-file content hash) is **planned, not implemented**: nothing writes or reads it yet.

#### Engine ↔ Adapter dispatch (issue #134 — single source of truth)

The `Engine` protocol and the `FormatAdapter` registry have an
**inverted** relationship that keeps format dispatch in exactly one
place:

- **Engines expose per-format primitives** — `read_csv`,
  `read_parquet`, `read_duckdb_table`, `from_records` plus the matching
  `write_*` — and a `cast_schema` helper. These are what adapters
  actually call.
- **Adapters own format dispatch.** `Adapter.read(source, engine, ...)`
  calls `engine.read_<format>(...)` directly. No engine-name `if/elif`
  inside adapters; no per-format `if/elif` inside engines.
- **`Engine.scan(source)` is a 3-line convenience** that resolves
  `source` via `corral.io.dispatch` and delegates to the chosen
  adapter's `read`. It exists so callers who don't want to think about
  adapters can still write `engine.scan(path)`. The single carve-out
  is dict sources (`{"data": ...}`, `{"format": "duckdb", ...}`) which
  the dispatcher can't sniff — `scan` short-circuits those to
  `from_records` / `read_duckdb_table` before delegating.
- **`Engine.write(expr, dest, fmt)` is symmetric** — a 3-line
  convenience over `corral.io.get_adapter(fmt).write`.

- **Adapters own package layout too.** `Package.write` stages the
  write and hands every table to `corral.io.write_package`, which calls
  the adapter's optional `write_package` (the `PackageWriter`
  protocol): csv / parquet write a directory plus `datapackage.json`,
  duckdb one multi-table file, zipcsv one zip. An adapter without it
  gets one `dest/<table>` location per table. `Package.write` has no
  per-format branches.

This means a new format (xlsx, geoparquet, …) is added by writing one
`FormatAdapter` plus a `read_<format>` primitive on the DuckDB engine —
no central dispatch needs editing.

The format-interop test
(`packages/corral/tests/dataset/test_format_interop.py`) locks the
input/output contract: pandas / polars / pyarrow in and out round-trip
through DuckDB with the nullable dtype family preserved. A regression
test pins that `Engine.scan()` stays a thin delegator (≤10 top-level
statements) so no future contributor can re-grow the per-format
if/elif inside it.

### 6.2 Memory-efficient scoping

- **Lazy by default.** `netstead.read(...)` returns a `Network` whose tables are unmaterialized ibis expressions. Materialization on `.to_pandas()` / `.collect()` / `.head()` / explicit consumer.
- **Spatial scopes (generic, in `corral.dataset.view`):** `from_bbox`, `from_polygon`, `from_geometry_buffer`.
- **Network-aware scopes (in `netstead.scope`):** `from_nodes(ids, path_between=True)` (BFS / shortest-path induced subgraph), `from_node(id, network_buffer="0.5mi")` (Dijkstra), `from_link(id, spatial_buffer_m | network_buffer)`, `from_point(xy, spatial_buffer_m)` (snaps + buffers), `connected_component(seed)`, `from_zone(zone_ids)`.
- **Composite + chainable:** `net.scope.from_nodes([1,2,3]).buffer_network("0.5mi").buffer_spatial(30)`.
- **Eager-index opt-in (memory-for-compute):** `net.build_indexes(spatial=True, graph=True)` builds a shapely STRtree + a scipy CSR adjacency (`netstead.graph`) once; subsequent scopes use them. Indexes cached as sidecar parquet keyed on content hash (auto-invalidated on edit). Auto-build heuristic: trigger when network exceeds N nodes (configurable; default 50k) AND user calls a network-aware scope op.

    *Why ~50k nodes as the auto-build threshold?* Empirically calibrated against the Leavenworth (75 nodes) and synthetic regional (~500k nodes) fixtures: below ~50k, the first scope op runs faster than the index build, so building eagerly is a net loss. Above ~50k, the second scope op pays back the build cost, and by the third the user is clearly going to repeat-query. Setting the threshold low (e.g. 1k) burns CPU on networks that don't need it; setting it high (e.g. 1M) makes regional networks feel slow on the second scope op. The number is empirical, not load-bearing — configurable via `NETSTEAD_AUTO_INDEX_THRESHOLD` env var for users with atypical workloads.
- **Predicate pushdown** to all other tables by FK chain (links → TOD tables, nodes → zone references, etc.). For partitioned parquet, bbox scope becomes true partition prune via duckdb pushdown — verified via `EXPLAIN` snapshot tests.
- **Geometry encoding:** geometry is WKB in memory; CSV stores WKT and Parquet stores WKB with GeoParquet `geo` metadata + bbox. See the [geometry encoding ADR](https://github.com/e-lo/netstead/blob/main/docs/design/2026-10-01-geometry-encoding-adr.md).
- **Partial loads:** `Package.from_source(src, tables=["link", "node"])` / `corral.read(src, tables=[...])` / `netstead.read(src, tables=[...])`. FK validation degrades gracefully: an FK whose target table was not loaded is reported as an `fk.unverifiable` warning rather than failing.

### 6.3 Validation + sync state

`ValidationReport` is the single object returned by all validation paths (schema + structural + FK + sync + data-quality). `Severity` has three levels — `ERROR` / `WARNING` / `INFO` — and the separate `Category` field discriminates rule families: `schema` / `structural` / `foreign_key` / `sync_state` / `data_quality` (quality rules emit `data_quality`, not a fourth severity). Renderers: rich console / JSON / **interactive single-file HTML** (Jinja2 + DataTables + Vega-Lite map view for geo-located issues; severity ranking; filter by table/severity/rule/category; click-to-expand row context).

**Integrity is reported, not enforced.** PK / FK / NOT NULL / enum checks run as pushed-down aggregates and anti-joins and become findings; tables never carry native DuckDB constraints (measured ~46–120× slower to load, and all-or-nothing on the first bad row). See the [FK constraints ADR](https://github.com/e-lo/netstead/blob/main/docs/design/2026-10-01-fk-constraints-adr.md).

**Sync state model:**
- `DirtyTracker` (in `corral.validation.sync_state`) records content hashes per table.
- FK validations stamp source+target hashes at validation time.
- Before any `write()` or `validate(strict=True)`: walk FK graph; if any FK's recorded hashes don't match current hashes, raise `OutOfSyncWarning` (warning by default; error under `--strict`).
- Direct DataFrame mutations bypass tracker — documented; user calls `net.invalidate("link")`.
- Auto-detection of stale FKs on read (a persisted hash sidecar) is planned, not implemented — today the tracker starts fresh per process.

**Data-quality framework (`corral.quality`):**
- `Rule` is a Protocol: `code` / `description` / `severity` attributes plus `applies_to(package) -> bool` (cheap pre-check) and `run(package, report)` — or `run(package, report, config)` for threshold-driven rules — which appends `Issue`s with `category=data_quality` to the report it is handed.
- Per-rule knobs via the frozen `RuleConfig` dataclass (`enabled`, `severity_override`, `thresholds`).
- Entry-point plugin discovery — packages register their rule packs under the `corral.quality.rules` group.
- Run via `corral.quality.run_quality(package, *, config=None, report=None)` — every registered rule whose config is enabled and whose `applies_to` returns true; `config` is a `{rule_code: RuleConfig}` map.
- netstead ships the GMNS rule pack: high-speed-residential, disconnected components, lane-count mismatch, near-duplicate nodes, sharp-angle bends, implausible v/c, missing critical-but-optional fields. Configurable thresholds; warnings not errors by default.

### 6.4 Editing + rollback

`corral.editing` provides the framework: `Edit` (op + table + payload), `EditResult` (edit + `Diff` + rollback blob + `applied_at` + `session_id`), `Session(package, log_path=...)` (context manager — applies edits as they are added, reverses them all if the block raises, persists the log to the parquet sidecar at `log_path` on clean exit), `rollback(package, log_path, to=None | session_id | datetime)` (replays the persisted log LIFO) and `affected_rows(result)`.

`netstead.clean` (optional `[clean]` extra) provides domain ops: `simplify_geometry(net, mode="redundant_only" | "douglas_peucker", tolerance=...)`, `merge_close_nodes(threshold_m=5)`, `remove_orphans()`, `split_link_at_node(...)`, `connect_disconnected_components(...)`, `recompute_lengths()`, `snap_to_reference(other_net)`. Each returns `EditResult` integrated with `corral.editing.Session`.

Edits inside `with Session(pkg, log_path=log) as s: s.add_edit(...)` produce a chronological log; `corral.editing.rollback(pkg, log, to=session_id_or_timestamp)` reverses. `Network` has no `.session()` / `.rollback()` shortcuts — use the `corral.editing` functions directly (the `netstead clean` commands do this for you).

### 6.5 Pooled operations + cost model

**Pool/batch:** `with net.batch(): ...` defers + coalesces ops, validates once on `__exit__`. Atomic on exception (state unchanged on raise). There is no interactive `netstead edit` REPL; the `netstead clean <op>` subcommands are the CLI editing surface.

**Cost model (heuristic):** `OperationCost(op_name, n_rows, n_tables=1, fmt=None).est_seconds()` per op — `coefficient x (n_rows / 1e6) x n_tables`, coefficients keyed by op and format. Coefficients calibrated on the Leavenworth fixture + a synthetic ~regional fixture. Nightly bench job re-fits on Python/duckdb minor releases.

**Gating:**
- `<30s` → run silently
- `30s ≤ est < 180s` → emit estimate + run with progress bar
- `est ≥ 180s` → require user approval

CLI `--yes` (on the commands that expose it), env `CORRAL_AUTO_APPROVE=1`, programmatic `approve=True` skip prompts. CLI surfaces actual time after each gated op so the model self-improves over time. Documented as heuristic — not authoritative.

*Why 30s estimate / 180s approval thresholds?* 30 seconds is the high end of what feels interactive — past that, the user starts wondering whether something broke, so we owe them an estimate and a progress bar. 180 seconds (three minutes) is the low end of "I've gone to get coffee" — by then a silent op risks the user closing the laptop and coming back to a half-finished session, so we require an explicit approval. Both numbers are calibrated to typical interactive vs batch UX expectations rather than load on the machine. The cost model itself is heuristic and self-improving; the thresholds are stable UX contracts.

### 6.6 CLI

`typer` + `rich`. Two entry points:

- `corral …` — generic commands on any Frictionless package: `validate`, `convert`, `info` (per `corral --help`).
- `netstead …` — extends the corral typer app: GMNS-aware `validate` / `info` / `convert`, plus `quality`, `doctor`, `bench`, `bench-suite`, `build`, `select`, `select-serve`, `viz`, `app`, `spec`, `server`, `mcp`, `clean`, `scope`, `index`, `llm` (per `netstead --help`). Spatial scope (bbox / polygon / geometry-buffer) is a Python API (`corral.dataset.view`, `Package.scope`), not a CLI command.

`--json` flag on every command emits structured JSON for AI agent consumption. Claude Code-style short interactive prompts; default in brackets; summary before destructive ops.

### 6.7 Notebook

`_repr_html_` on `Package`, `Table`, `ValidationReport`, `EditResult` (in corral); on `Network` (in netstead). Rich progress with `force_terminal=False` for inline rendering. Optional ipywidget for interactive scope construction (gated behind `netstead[notebook]` extra).

### 6.8 Self-hostable API server

`netstead.server` (optional `[server]` extra) ships a FastAPI app with auto-generated OpenAPI at `/docs`. Endpoints:
- `GET /networks` — list configured networks
- `GET /networks/{id}` — metadata + spec version + table list + last-validated timestamp
- `GET /networks/{id}/tables/{table}?bbox=...&zone_ids=...&columns=...&format=parquet|csv|duckdb|json` — scoped table download
- `GET /networks/{id}/spec` — return resolved spec (Frictionless JSON)
- `POST /networks/{id}/validate` — run validation, return `ValidationReport` JSON (or HTML if Accept header)
- `GET /networks/{id}/quality` — data-quality report

Pluggable auth: none / bearer-token / OAuth2 (config-driven). Default download format = DuckDB. Config-file driven; backend points at any `corral.read()`-compatible source. Ships `Dockerfile` + `docker-compose.yml` example. **We don't host.**

*Why bearer-token default + warn-on-unsafe over hard-block?* The deployment shape is intentionally mixed: a researcher running `netstead serve` on `localhost` for a notebook session shouldn't be forced through OAuth, but the same binary running behind a reverse proxy on a campus network needs auth. Hard-blocking unauthenticated mode would push users to roll their own server; silently allowing it would leak networks. Warn-on-unsafe + bearer-token default puts the security decision in the operator's config file where it's auditable and version-controlled. The right paranoia level is "loud about the risk, not paternalistic about the choice."

*Why no rate limiting or token rotation in v1?* Both are real concerns at hosted-multi-tenant scale, but the v1 target is self-hosted single-team deployments. Building rate-limit middleware in v1 would either ship a token-bucket implementation that's worse than `nginx`/`caddy`/`traefik`, or pull in a Redis dependency that doubles the deployment surface. The recommended pattern is a reverse proxy in front, which gives rate limits, TLS, token rotation, and audit logs for free — features the project would otherwise have to reimplement badly.

### 6.9 AI accessibility

- **`docs/llms.txt`** + **`docs/llms-full.txt`** at site root (auto-generated from mkdocs nav).
- **`docs/ai/`** subtree (corral site): `index.md` (the AI surface overview) and `json-cli.md` (driving the CLI from an agent loop); `api-index.json` (machine-readable public API) is generated at build time by `corral.docgen.llms.generate_api_index_json`, not checked in.
- **Doctests in every public function** (Google-style docstrings with `Examples:` block), run in CI.
- **`--json` flag on every CLI command**.
- **Claude Code Skills** in `skills/` directory (six): `corral-validate`, `gmns-author`, `gmns-build`, `gmns-validate`, `gmns-convert`, `gmns-clean`. Installable via `claude code skill add <git-url>#path=skills/<name>`.
- **MCP server** — `netstead[mcp]` ships `netstead mcp serve` (stdio). corral has no `mcp` CLI command; it exposes `corral.mcp.build_server()`, which registers the generic tools `describe_package`, `validate_package`, `list_tables`. netstead's server adds `describe_network`, `quality_check`, `connected_components`, `scope_from_nodes`. Stateful tools (edit sessions with rollback, convert) are not yet shipped.

*Why stateless MCP tools?* MCP tool dispatch is a single request/response round-trip; the protocol does not currently have a first-class session abstraction the way SSE-based RPCs do. Trying to hide session state inside individual tool calls would either smuggle global mutable state across MCP clients (a footgun) or force the user to thread an opaque handle through every call (worse ergonomics than just reloading). The right seam is to keep v1 tools stateless and add session affinity later as an explicit protocol feature; the `state=` kwarg added in PR-A is the placeholder for that later seam without committing to its shape today.

*Why ship Skills in-repo + MCP via extra rather than hosting?* Hosting either would commit the project to an availability SLA, an auth story, and an upgrade cadence — three full-time jobs the project doesn't have. Shipping Skills as files in the git repo means installation is `git clone` + a one-line Claude Code config; shipping MCP behind a `[mcp]` extra means users run it under their own MCP client (Claude Desktop, Cursor, etc.) with their own auth model. Zero hosting commitment, full local control, no rug-pull risk if the project's funding changes.

## 7. Spec sync strategy

- Each supported GMNS spec version vendored under `packages/netstead/netstead/spec/<version>/` (e.g., `0.97/datapackage.json`, `0.97/link.schema.json`, `0.97/shared_categories.json`).
- `netstead.SUPPORTED_SPECS = ["0.95", "0.96", "0.97"]`; `DEFAULT_SPEC = "0.97"`.
- User override: `netstead.read(..., spec_version="0.96")`.
- `.github/workflows/spec-sync.yml` runs daily, checks upstream releases at zephyr-data-specs/GMNS, opens a PR labeled `spec-sync` against `main` with the new version added side-by-side. Maintainer reviews; default-version bump goes in a minor release.
- Validation reports always include `spec_version` in the header.

*Why bundle 0.95 / 0.96 / 0.97 side-by-side rather than auto-upgrade?* GMNS isn't a frozen spec — field renames and category enumerations shift between minor versions. An old fixture written against 0.95 should still validate against 0.95 without the user being forced to migrate; a silent auto-upgrade would either fabricate fields that didn't exist or fail with confusing errors about fields the user never wrote. Bundling versions side-by-side and letting the user select explicitly (or, once the planned `_netstead_meta.json` sidecar lands, letting it record what the file was written against) keeps old data loadable forever and makes spec drift a visible, version-controlled decision rather than a hidden one.

## 8. Quality bar

- **Coverage targets (gated at Phase 5):** corral ≥85%, netstead ≥75%.
- **Test pyramid:** unit (per-module) → contract (engine/adapter conformance) → fixture (Leavenworth) → perf (synthetic regional fixture, `pytest-benchmark`).
- **CI matrix:** Python 3.11 / 3.12 / 3.13 × Linux + macOS smoke. I/O formats (pandas / polars / Arrow in and out) pinned by `test_format_interop.py`.
- **No raw SQL, no `pandas` in corral core paths** (allowed via `to_pandas()` converter at the edge only).
- **Doctests run in CI** — public-API examples must execute.

## 9. Conventions

- **Docstrings:** Google style on every public function. **`Examples:` blocks required only on *user-facing* public symbols** — i.e., symbols re-exported via a package's `__init__.__all__`, the constructor / main methods of public classes, and convenience wrappers that appear in cookbook / quickstart material. Internal-public helpers (functions that lack a `_` prefix only because tests need to import them directly) get a Google-style summary docstring but skip the `Examples:` / doctest overhead. Examples bloat is real — see review-deferred LOC notes in #134-class issues. Rule of thumb: *if a user would type the symbol's name into their own code, it needs Examples; if only the test suite types it, the summary is enough.*
- **Logging:** `logging.getLogger(__name__)` per module. Library never configures the root logger; only the CLI does.
- **Pydantic:** v2, strict on corral core types.
- **Type hints:** required on all public APIs. `pyright` strict on `corral`, basic on `netstead`.
- **Errors:** structured exceptions in a per-module `errors.py`. Use specific subclasses, not bare `ValueError`.
- **Backwards compat:** v0.3.x is a clean break — no shims. Migration guide ([docs/migration/v0.3-to-v1.0.md](https://e-lo.github.io/netstead/netstead/migration/v0.3-to-v1.0/), Phase 4 task 4.14) explains old → new mappings.
- **Per-package semver.** `corral` and `netstead` version independently. Tags: `corral-vX.Y.Z`, `netstead-vX.Y.Z`.

## 10. Phase plan summary (historical)

> The phase plan below is how the v1.0 rewrite was staged (May–June 2026); phases 0–4 have shipped. Current and future work is tracked in [`docs/design/README.md`](https://github.com/e-lo/netstead/blob/main/docs/design/README.md) and GitHub issues.

Five phases, ~14–16 weeks to v1.0 GA. Full task tree in the [GitHub issue tree](https://github.com/e-lo/netstead/issues/115) (Epic).

| Phase | Focus | Duration |
|---|---|---|
| 0 | Repo prep — workspace, vendored specs, dev tooling, CI, issue tree, **module skeletons** | done |
| 1 | corral foundation — spec model, engines, IO adapters, Leavenworth fixture | weeks 1–4 |
| 2 | Validation + dataset surface — schema/FK/structural/sync, lazy Package/Table, generic edit framework, interactive HTML reports | weeks 4–7 |
| 3 | Operations + GMNS bindings + quality + clean — cost model, GMNS Network, semantics, indexes, scope, quality framework + GMNS rule pack, clean ops | weeks 7–11 |
| 4 | Surfaces — CLI (corral + netstead), notebook, server, MCP, Skills, awesome docs, migration guide, full PRD | weeks 11–14 |
| 5 | Hardening + beta + GA — coverage gate, perf bench, spec-sync bot, releases | weeks 14–16+ |

**Sub-agent friendly tasks** are tagged `subagent-friendly` in the issue tree. Phase 1–4 mid-batches have 5–10 truly parallel tasks (no file overlap, no inter-deps).

## 11. How to contribute (short version)

1. Pick an issue labeled `subagent-friendly` (or any task on the current phase).
2. Branch from `main` as `<type>/<slug>` (e.g. `feat/scope-zones`).
3. Write the code per the issue's Deliverable + Acceptance criteria.
4. Add tests under `packages/<pkg>/tests/`.
5. Run `uv run ruff check`, `uv run ruff format`, `uv run lint-imports`, `uv run pytest` locally.
6. Open PR against `main`. Use the issue body's acceptance checklist as your self-review.

Full contributor workflow: [CONTRIBUTING.md](https://github.com/e-lo/netstead/blob/main/CONTRIBUTING.md).

---

*This document is updated as architectural decisions evolve. When a decision changes, record it as an ADR in `docs/design/`, update this file, and update the [design index](https://github.com/e-lo/netstead/blob/main/docs/design/README.md). Authoritative source for any conflict: this file > accepted ADRs > PRDs / designs > plans > issue bodies > inline code comments.*
