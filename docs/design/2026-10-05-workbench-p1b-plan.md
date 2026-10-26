# Workbench P1b (Inspect + Settings) Implementation Plan

Status: **implemented** (#212) · Parent: [Workbench design](2026-10-02-netstead-workbench-design.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the map and the data table one linked view, and give every setting a home in the UI.
- Clicking a map feature focuses it: its row scrolls into view, and its details show. Clicking a row focuses it and flies
  the map there.
- A box-select becomes the table's "Highlighted" filter.
- A foreign-key cell (`from_node_id`) jumps to the node's row and its marker.
- When records are focused or highlighted, rows that are one foreign key away tint in a lighter color: in every table
  and on the map. The keys come from the GMNS spec.
- A **Settings** dialog is generated from the `Settings` JSON schema. It shows where each value comes from, saves
  through `SetSetting`, and shows refused keys read-only with the reason. The existing Language-models panel becomes
  one of its sections.
- Carried follow-ups:
  - the header no longer crowds at narrow widths;
  - `IbisEngine.to_pandas` converts outside the engine lock;
  - a basemap change applies without a reload;
  - the legacy `viz/server.py` and `select/webapp.py` app factories are deleted.

**Architecture:**
- `netstead.workbench.related` reads single-column foreign keys from the network's spec (`DataPackage.resources[*].table_schema.foreign_keys`).
  - It expresses every relation as `<column> IN (<values>)` on the related table.
  - An *inbound* key reuses the highlighted ids as they are (`lane.link_id IN highlighted links`).
  - An *outbound* key costs one query for the distinct key values of the highlighted rows (`node.node_id IN
    {from_node_id, to_node_id of highlighted links}`).
  - Values are therefore bounded by the highlight. Counts run as one DuckDB count per related table (pandas for the
    eager `link`/`node` frames).
- New read-only routes, none of them recorded:
  - `POST /api/n/{id}/{component}/related`: per-table counts, plus link/node ids for the map;
  - `POST .../table/{name}/rows`: the existing GET as a JSON body, plus `related` in `tint` or `filter` mode;
  - `POST .../table/{name}/locate`: where a row falls in the current order, so it can be scrolled to.
- Front-end view state:
  - **focus** is one clicked record. It is per tab and never recorded; see Open question 1.
  - It sits beside the existing per-tab `highlights` and the recorded server `selection`.
  - Linking rules live in a DOM-free `linking.js`, which is unit-tested under node.
- Settings:
  - `GET /api/settings` gains `readonly` (key → reason), `restart` and `notes`. The rules stay in Python, in one place.
  - A DOM-free `settingsform.js` turns the payload into field descriptors.
  - `settings.js` renders the dialog and dispatches `set_setting`.
  - `#llm-panel` moves inside the dialog as a registered section.
- corral: `IbisEngine.to_pandas` fetches Arrow under the backend lock (`expr.to_pyarrow()`). It then converts outside
  the lock, with the same converter ibis's duckdb `execute` uses.

**Tech Stack:**
- Python 3.11, pydantic v2, FastAPI.
- ibis 12 on DuckDB, through corral `Table.filter` / `filter_rows`. No raw SQL: `lint_no_sql.py` must stay clean.
- pandas for the eager link/node frames.
- MapLibre GL 4.7.1 and deck.gl 9.0.38, already loaded from the CDN.
- Native ES modules, with no build step.
- Node is used only in tests, through the existing `run_node` fixture.
- No new dependencies.

**Spec:** [2026-10-02-netstead-workbench-design.md](2026-10-02-netstead-workbench-design.md):
- §b "Inspect & Validate workspace": two-way linking, FK navigation, Related records (P1b).
- §g "Settings workspace".
- The phasing row P1b.
- [nl-providers design](2026-10-02-nl-providers-design.md), and the P1b follow-ups in its plan: mount `#llm-panel`, and
  hide `llm.*` from the generic form.

**Branch:** `feat/workbench-p1b`, cut from `origin/feat/nl-providers` (PR #211: P0 + P1a + NL providers) at `dfd73e7`.

**Conventions:**
- Run commands from the repo root.
- **Tiered tests:**
  - while iterating, the task's own paths: `uv run --all-extras pytest <paths> -q`;
  - before every commit: `uv run --all-extras pytest packages -n auto -q`, the fast default tier;
  - before merge, once, in Task 16: `uv run --all-extras pytest packages -n auto -q -m ""`, the full tier, including
    `slow` and `perf`.
- Lint: `uv run ruff check packages && uv run ruff format --check packages`. Task 16 also runs
  `uv run lint-imports` and `uv run python scripts/lint_no_sql.py`.
- Ruff enforces Google-style docstrings (`D`) on public module-level functions, classes and methods outside
  `tests/`. Line length is 120.
- `--doctest-modules` is on, so every `>>>` example below is a test.
- Never write under `netstead/fixtures`, because the conftest guard fails the session. Outputs go to `tmp_path`.
- Tests that need more than `link`/`node` use the committed **Leavenworth** parquet fixture: 339 links, 121 nodes,
  429 lanes, 1 `link_tod` row. RDU has only `link` and `node`.
- **Pure front-end logic goes in import-free modules** (`linking.js`, `settingsform.js`). These are tested under node by
  the new `node_module` fixture (Task 7). DOM wiring is covered by the static tests
  (`test_relative_imports_resolve_to_real_exports`, `test_every_element_id_used_by_js_exists_in_index`,
  `test_js_syntax`) and by the browser walk-through in Task 16.
- **Honesty note:**
  - The code below was written against `dfd73e7`, and these assumptions were probed on that commit:
    - the spec's FK list;
    - the Leavenworth table set and lane filtering through `filter_rows`;
    - the `to_pyarrow` + converter equivalence with `expr.execute()` on all nine Leavenworth tables, CSV and Parquet.
  - The plan as a whole has **not** been executed. Expected results therefore say "pass" plus the new test names, not
    exact counts. Where a step fails, fix the plan's code; don't weaken the test.

---

## Open questions (all decided 2026-10-05: the user approved every recommendation)

1. **What does a click do: focus, or select?**
   - **Decided 2026-10-05:** a click sets a per-tab focus that is not recorded (the recommendation; confirmed
     explicitly).
   - The design says "clicking a map feature selects…". In P0, a row click dispatches a recorded `Select(link_ids=[id])`.
   - **Recommended:** a click sets a per-tab **focus**: details, row scroll, map outline or marker, and related tint.
     - It is not recorded, so a session's history isn't flooded with clicks.
     - The recorded selection stays what NL selection and "Set as selection" produce.
     - This also fixes the carried bug by construction: a row click no longer replaces the selection.
   - *Alternative:* keep dispatching `Select` on click, but not in a filtered scope. That changes only Task 12's
     `rowClick`/`onLinkClick`.
2. **Box-select → table filter.**
   - **Decided 2026-10-05:** as recommended. Shift-drag box-select stays in Highlight mode, switches a visible table to
     Highlighted, and the checkbox becomes the All / Selection / Highlighted / Related scope menu.
   - **Recommended:**
     - shift-drag box-select still needs Highlight mode, as today;
     - when the table is visible, the box switches its filter to **Highlighted**;
     - the old "Filter to map selection" checkbox becomes a scope menu: **All / Selection / Highlighted / Related**.
3. **What drives the related tint?**
   - **Decided 2026-10-05:** the focus plus the highlights, not the recorded selection (the recommendation; confirmed
     explicitly).
   - **Recommended:** the focus plus the highlights, and *not* the recorded selection.
   - The selection still reaches other tables through the **Selection** scope. For a non-link table, that scope means
     "rows related to the selected links".
4. **`GET` or `POST` for related and rows?**
   - **Decided 2026-10-05:** as recommended. `POST` with a JSON body for `/related`, `/table/{name}/rows` (the GET
     stays) and `/locate`.
   - The design names `GET /related?table=&ids=`. Box-selections and NL selections can be thousands of ids, which
     overflows a URL: uvicorn's h11 limit is about 16 KB for the request line plus headers. The current GET `rows?ids=`
     has the same latent bug.
   - **Recommended:** `POST` with a JSON body, for `/related`, `/table/{name}/rows` (the GET stays) and `/locate`.
   - They are still read-only and unrecorded. They pass the same loopback Origin guard as other POSTs.
5. **Scope of relations.**
   - **Decided 2026-10-05:** as recommended. Single-column FKs only, no self-references, and a table is never revisited.
   - **Recommended:** single-column FKs only, and no self-references: `link.parent_link_id`, `node.parent_node_id`,
     `zone.super_zone`.
   - A table is never revisited: "Expand a hop" reaches *new* tables (link → lane → lane_tod), not link → node → link
     neighbours.
   - Neighbour links are a later feature.
6. **The Settings dialog's save model.**
   - **Decided 2026-10-05:** a Save to menu (User / This project / This session only) and each change saves instantly
     (the recommendation; confirmed explicitly).
   - **Recommended:**
     - a **Save to** menu: User (default) / This project / This session only;
     - each change saves on `change`;
     - an empty input means "reset": `value=None` removes the key from that layer;
     - a per-field **Reset** removes the value from the layer it actually comes from.
7. **Which keys are read-only?**
   - **Decided 2026-10-05:** as recommended. Exactly the refusal the session enforces, derived on the server;
     `io.allowed_roots` shown read-only with the reason.
   - The brief says "`io.*`, secret-named". Today the session refuses only `io.allowed_roots` (and `io` as a whole,
     which would replace it). `io.spec_version` and `io.default_format` are settable and harmless.
   - The schema has no secret-named fields: by design, keys live in the keyring.
   - **Recommended:**
     - keep exactly the refusal the session enforces;
     - show `io.allowed_roots` read-only, with the reason;
     - derive the read-only set on the server from the same rules, so a future secret-named field is read-only
       automatically.
8. **Sections that nothing reads yet.** `engine.*`, `validation.*` (P2) and `credentials.*` are in the schema but not
   wired.
   - **Decided 2026-10-05:** as recommended. Show them with a "not used by the workbench yet" note; `app.*` launch keys
     get an "applies on next launch" badge.
   - **Recommended:** show them, each with a "not used by the workbench yet" note.
   - `app.host/port/console` carry an "applies on next launch" badge. Session scope has no effect for them, and the
     form says so.
9. **The node table under the "Selection" scope.**
   - **Decided 2026-10-05:** all nodes of the selected links, through the FK relation (the recommendation; confirmed
     explicitly).
   - In P0 it showed only the from/to anchors.
   - **Recommended:** all nodes of the selected links, through the FK relation. The anchors stay drawn on the map.
10. **Legacy deletion.**
    - **Decided 2026-10-05:** as recommended (delete `_geojson.py`, its test and both `templates/` folders; keep
      `ClaudeParser` and the CLI aliases).
    - **Recommended:**
      - also delete `select/_geojson.py` and `tests/test_select_geojson.py` (only `webapp.py` uses them);
      - delete both `templates/` folders;
      - keep the `ClaudeParser` class: it is public back-compat API in `netstead.select`, even with no internal callers
        left;
      - keep the `netstead viz` / `select-serve` CLI aliases. They already route to the Workbench and are covered by
        `test_cli_workbench.py`.

## Decisions (technical, made here)

1. **One relation graph per network version:**
   - `relation_graph(spec, tables)` is cached on the handle (`h.cached("relations", …)`), so an edit (`bump`) rebuilds
     it.
   - It knows only the tables `NetworkHandle.tables()` exposes. Today those are link, node, lane, segment,
     segment_lane, zone, movement and link_tod, when present.
2. **Primary keys come from the spec when it declares a single present column**, else from the existing heuristic
   (`viz.tables.primary_key`).
   - The schema route and the rows route use the same `pks` map. The grid, `/locate` and `/related` therefore always
     agree.
   - This also fixes `movement`, whose spec key `mvmt_id` the heuristic could miss.
3. **Bounds:**
   - `MAX_SOURCE_IDS = 10_000` ids per request. Above that the answer is 422, "narrow the highlight".
   - `MAX_MAP_IDS = 50_000` link/node ids returned for map tinting. Above that, `truncated: true` and the first
     50 000 by key.
   - A hop-2 frontier is capped at `MAX_SOURCE_IDS`, and relations derived from a capped frontier are flagged `partial`.
4. **Tinting a page needs no extra query for inbound keys.** `row_vias` tests the page's own FK columns in pandas (at
   most 500 rows). Only outbound keys cost the one `distinct` query.
5. **An explicit empty `ids: []` in the POST body means "no rows"**, not "all rows". The GET keeps its old meaning: an
   empty `ids` is ignored.
6. **`/locate` supports the eager tables only** (link and node, the map-linked ones). For a lazy table it answers
   `{"index": null}`, and the grid says the row isn't on this page. Unsorted lazy pages have no stable order anyway.
7. **Focus effects:**
   - from the **table**: link → `fitLinks([id])`; node → `flyToNode`; any other table → only details and related tint;
   - from the **map**: no camera move; scroll the row if that table is showing.
   - Details use the generic `feature/{table}/{pk}` route, so the panel works for any table.
8. **Nodes become pickable on the map**, drawn above links. Without this, a map click cannot focus a node.
9. **The basemap swap uses `map.setStyle()`.**
   - The deck.gl `MapboxOverlay` is a non-interleaved control and survives a style swap.
   - Label visibility is re-applied on `style.load`.
   - The trigger is the `history` SSE entry for any successful `set_setting` on `viz.*`. It works from the UI, from
     Python and from the NL assistant.
10. **The Language-models panel is folded in, not re-generated:**
    - `#llm-panel` moves into the Settings dialog as a registered section;
    - `llm.*` and `select.*` are hidden from the generated form;
    - "Models…" in the header opens Settings at that section;
    - its controls keep saving to user scope, as today, so the dialog's Save-to menu is hidden on that section.
11. **The map gear keeps its ids but is relabelled "Map display".** The new header button is "Settings…".
12. **The header becomes two rows** (`.hrow`):
    - row 1 is network, Open/Import, Recent, view mode, counts, Jobs and Settings;
    - row 2 (`#nl-row`) is the utterance, the provider/model picker and Select.
    - At 800 px or less, the title and counts hide. At 640 px or less, "Make default" hides; it is also in Settings →
      Language models.
13. **`to_pandas` uses ibis's converter outside the lock** (`DuckDBPandasData.convert_table`, ibis 12).
    - The column loop mirrors `Backend.execute` line for line, so dtypes are unchanged; the probe found this on all 18
      Leavenworth tables.
    - Non-duckdb backends keep `expr.to_pandas()`.
    - The import path is semi-private, so a test pins equivalence with `expr.execute()`.

## Scope notes

- **In P1b:**
  - corral `to_pandas`;
  - `related.py`;
  - `locate_row`;
  - the network-route additions;
  - settings payload metadata;
  - the Settings dialog;
  - header rows;
  - basemap without reload;
  - focus and two-way linking;
  - FK navigation;
  - related tint and filter;
  - deleting the legacy apps;
  - docs.
- **Deferred:**
  - neighbour links, and self-referencing FKs (Open question 5);
  - composite FKs;
  - transit relations (P6). `component="transit"` keeps answering 501;
  - a settings search box;
  - setting and testing credentials in the keyring from the Settings dialog. The design §g lists it, but the
    Language-models panel already covers LLM keys, and general host credentials stay a follow-up;
  - `/locate` for lazy tables;
  - per-field help text: there are no `Field(description=…)` yet.
- **Unchanged:**
  - the Action union;
  - history;
  - `GET .../rows`;
  - the P1a wizard and jobs;
  - the CLI aliases.

## File structure

| Path | Responsibility |
|---|---|
| `packages/corral/corral/engines/ibis_engine.py` (modify) | `to_pandas` converts outside the backend lock (`_arrow_to_pandas`) |
| `packages/corral/tests/engines/test_ibis_engine.py` (modify) | Equivalence with `execute`, lock released during conversion |
| `packages/netstead/netstead/workbench/related.py` (new) | `ForeignKey`, `Match`, `Relation`, `RelationGraph`, `foreign_keys`, `primary_keys`, `relation_graph`, `relate`, `restrict`, `count`, `ids_of`, `row_vias` |
| `packages/netstead/netstead/viz/tables.py` (modify) | `locate_row` |
| `packages/netstead/netstead/workbench/routes/network.py` (modify) | Spec PKs and `foreign_keys` in schema; `POST rows`, `POST locate`, `POST related` |
| `packages/netstead/netstead/workbench/actions.py` (modify) | Public `is_secret_name` (shared by `SetSetting` and the payload) |
| `packages/netstead/netstead/workbench/session.py` (modify) | `refusal_reason`; `settings_payload` gains `readonly`, `restart`, `notes` |
| `packages/netstead/netstead/workbench/static/js/settingsform.js` (new) | Pure: schema → sections/fields, `parseInput`, `resetScope`, `scopeNote` |
| `packages/netstead/netstead/workbench/static/js/settings.js` (new) | The Settings dialog (DOM), `registerSection`, `openSettings` |
| `packages/netstead/netstead/workbench/static/js/linking.js` (new) | Pure: `sourcesFor`, `hasSources`, `rowsRequest`, `rowMarks`, `pageOffset`, `coerceId` |
| `packages/netstead/netstead/workbench/static/js/related.js` (new) | Fetch related summary; rail badges |
| `packages/netstead/netstead/workbench/static/js/{store,main,table,map,side,llm}.js` (modify) | Focus, scopes, related layers, basemap swap, settings wiring |
| `packages/netstead/netstead/workbench/static/index.html`, `app.css` (modify) | Two-row header, Settings dialog, scope menu, hops button, tints |
| `packages/netstead/tests/conftest.py` (modify) | `node_module` fixture |
| `packages/netstead/tests/test_workbench_related.py` (new) | FK graph and relation logic on Leavenworth |
| `packages/netstead/tests/test_workbench_related_routes.py` (new) | `/related`, POST rows/locate, schema FKs |
| `packages/netstead/tests/test_workbench_js.py` (new) | Node unit tests for `linking.js` and `settingsform.js`, plus the row-click regression |
| `packages/netstead/tests/{test_viz_tables,test_workbench_session,test_workbench_actions,test_workbench_server,test_workbench_static}.py` (modify) | Tests listed per task |
| Delete: `netstead/viz/server.py`, `netstead/viz/templates/`, `netstead/select/webapp.py`, `netstead/select/templates/`, `netstead/select/_geojson.py`, `tests/test_viz_server.py`, `tests/test_select_webapp.py`, `tests/test_select_geojson.py` | Legacy app factories |
| `packages/netstead/pyproject.toml` (modify) | Drop the two template globs from the wheel include list |
| `packages/netstead/docs/cookbook/workbench.md`, `docs/design/2026-10-02-netstead-workbench-design.md` (modify) | Docs |

---

### Task 0: Branch and baseline

**Files:** none.

- [ ] **Step 1: Cut the branch**

```bash
git fetch origin && git checkout -b feat/workbench-p1b origin/feat/nl-providers
```

- [ ] **Step 2: Record the baseline**

Run: `uv run --all-extras pytest packages -n auto -q`
Expected: all pass (record the count in the PR description; every later task adds tests only, except Task 15, which
deletes the three legacy test files).

No commit.

---

### Task 1: corral: `IbisEngine.to_pandas` converts outside the engine lock

`expr.to_pandas()` runs ibis's duckdb `Backend.execute`. That is a locked backend method, and it does the whole Arrow →
pandas conversion while holding the lock. A column that contains nulls goes through `to_pylist()`, a Python-object
path, so on a wide link table the conversion can take as long as the query itself. Meanwhile every other thread's query
waits on the lock: the map buffer, a grid page, an open job.

**Files:**
- Modify: `packages/corral/corral/engines/ibis_engine.py`
- Test: `packages/corral/tests/engines/test_ibis_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/corral/tests/engines/test_ibis_engine.py`:

```python
@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_to_pandas_matches_ibis_execute_for_every_leavenworth_table(engine: IbisEngine, fmt: str):
    import pandas as pd

    folder = leavenworth.csv_dir() if fmt == "csv" else leavenworth.parquet_dir()
    for path in sorted(folder.glob(f"*.{fmt}")):
        expr = engine.scan(path)
        pd.testing.assert_frame_equal(engine.to_pandas(expr), expr.execute().convert_dtypes(), obj=path.name)


def test_to_pandas_converts_after_releasing_the_backend_lock(engine: IbisEngine, link_parquet: Path, monkeypatch):
    from corral.engines import ibis_engine

    lock = backend_lock(engine.con)
    seen: list[bool] = []
    real = ibis_engine._arrow_to_pandas

    def probe(*args, **kwargs):
        def try_lock() -> None:  # another thread: can it take the lock right now?
            got = lock.acquire(blocking=False)
            if got:
                lock.release()
            seen.append(got)

        t = threading.Thread(target=try_lock)
        t.start()
        t.join()
        return real(*args, **kwargs)

    monkeypatch.setattr(ibis_engine, "_arrow_to_pandas", probe)
    engine.to_pandas(engine.scan(link_parquet))
    assert seen == [True]
```

- [ ] **Step 2: Run them to confirm the second one fails**

Run: `uv run --all-extras pytest packages/corral/tests/engines/test_ibis_engine.py -q -k to_pandas`
Expected: the equivalence tests pass already. `test_to_pandas_converts_after_releasing_the_backend_lock` fails with
`AttributeError: ... has no attribute '_arrow_to_pandas'`.

- [ ] **Step 3: Implement**

In `packages/corral/corral/engines/ibis_engine.py`, replace the body of `IbisEngine.to_pandas` from `try:` to the end
of the method:

```python
        try:
            df = expr.to_pandas()
        except ImportError as exc:  # pragma: no cover - pandas is an ibis dep
            raise EngineNotAvailableError(
                "pandas is required for IbisEngine.to_pandas; install with `pip install dbcorral[pandas]`"
            ) from exc
        return df.convert_dtypes()
```

with:

```python
        try:
            import pandas  # noqa: F401  (fail with the install hint, not deep inside ibis)
        except ImportError as exc:  # pragma: no cover - pandas is an ibis dep
            raise EngineNotAvailableError(
                "pandas is required for IbisEngine.to_pandas; install with `pip install dbcorral[pandas]`"
            ) from exc
        if _is_duckdb(expr):
            # Only the query holds the backend lock (``to_pyarrow`` is a locked backend method); the
            # Arrow -> pandas conversion, which can cost as much as the query, runs after it is released.
            df = _arrow_to_pandas(expr.to_pyarrow(), expr.as_table().schema())
        else:
            df = expr.to_pandas()
        return df.convert_dtypes()
```

Add one sentence to the end of the `to_pandas` docstring paragraph that starts "Per the :class:`~corral.engines.base.Engine` protocol":
`Only the query runs under the backend lock; the conversion runs after it is released (see the module docstring).`

In the module docstring's "Remaining gaps" list, add nothing. Instead, after the paragraph that ends "callers that step
outside them should take :func:`backend_lock`.", add:

```text
:meth:`IbisEngine.to_pandas` deliberately holds the lock only for the query: it fetches Arrow
(``to_pyarrow``) and converts to pandas after the lock is released, so a large conversion never
stalls other threads' queries.
```

Directly above `_LOCK_ATTR = "_corral_lock"`, add:

```python
def _is_duckdb(expr: Any) -> bool:
    """Whether ``expr`` is bound to a duckdb backend (the only one whose conversion we replicate)."""
    try:
        return expr._find_backend().name == "duckdb"
    except Exception:  # noqa: BLE001  (an unbound memtable has no backend: use ibis's own path)
        return False


def _arrow_to_pandas(table: pa.Table, schema: Any) -> pd.DataFrame:
    """Arrow -> pandas exactly as ibis 12's duckdb ``Backend.execute`` converts it, without its lock.

    Mirrors ``ibis.backends.duckdb.Backend.execute``: nested, dictionary and null-bearing columns go
    through ``to_pylist`` (Arrow's own ``to_pandas`` would turn null ints into floats), the rest
    through ``to_pandas``, then ibis's ``DuckDBPandasData`` coerces to the ibis schema.
    ``test_to_pandas_matches_ibis_execute_for_every_leavenworth_table`` pins the equivalence.
    """
    import pandas as pd
    import pyarrow.types as pat
    from ibis.backends.duckdb.converter import DuckDBPandasData

    columns = {
        name: (
            col.to_pylist()
            if pat.is_nested(col.type) or pat.is_dictionary(col.type) or col.null_count
            else col.to_pandas()
        )
        for name, col in zip(table.column_names, table.columns, strict=True)
    }
    return DuckDBPandasData.convert_table(pd.DataFrame(columns), schema)
```

`pa` and `pd` are already imported under `TYPE_CHECKING`; `from __future__ import annotations` keeps the annotations
lazy.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/corral/tests/engines -q`
Expected: all pass, including the 3 new tests (2 parametrised cases + 1).

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q`, then `uv run ruff check packages && uv run ruff format --check packages`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add packages/corral/corral/engines/ibis_engine.py packages/corral/tests/engines/test_ibis_engine.py
git commit -m "perf(corral): convert to pandas outside the engine lock"
```

---

### Task 2: The FK relation graph, read from the spec

**Files:**
- Create: `packages/netstead/netstead/workbench/related.py`
- Test: `packages/netstead/tests/test_workbench_related.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_related.py`:

```python
"""Tests for the workbench's foreign-key relations (read from the GMNS spec, never hard-coded)."""

import json

import pandas as pd
import pytest
from netstead.fixtures import leavenworth
from netstead.workbench import Session
from netstead.workbench.registry import as_pandas
from netstead.workbench.related import relation_graph


@pytest.fixture(scope="module")
def lw(tmp_path_factory):
    """The Leavenworth handle: link, node, lane, link_tod."""
    tmp = tmp_path_factory.mktemp("rel")
    src = str(leavenworth.parquet_dir())
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([src])}
    s = Session(project_dir=tmp, environ=env)
    s.dispatch({"type": "open_network", "source": src})
    return s.registry.get("leavenworth")


@pytest.fixture(scope="module")
def graph(lw):
    return relation_graph(lw.roadway.spec, lw.tables())


def test_foreign_keys_come_from_the_spec(graph):
    fks = {(f.table, f.column, f.ref_table, f.ref_column) for f in graph.fks}
    assert {
        ("link", "from_node_id", "node", "node_id"),
        ("link", "to_node_id", "node", "node_id"),
        ("lane", "link_id", "link", "link_id"),
        ("link_tod", "link_id", "link", "link_id"),
    } <= fks


def test_self_references_and_absent_tables_are_left_out(graph):
    assert all(f.table != f.ref_table for f in graph.fks)  # link.parent_link_id, node.parent_node_id
    assert not any(f.ref_table in {"geometry", "zone", "time_set_definitions"} for f in graph.fks)  # not browsable


def test_primary_keys_prefer_the_spec(graph):
    assert graph.pks == {"link": "link_id", "node": "node_id", "lane": "lane_id", "link_tod": "link_tod_id"}


def test_label_names_the_relation(graph):
    fk = next(f for f in graph.fks if f.table == "lane")
    assert fk.label == "lane.link_id → link"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'netstead.workbench.related'`.

- [ ] **Step 3: Create `packages/netstead/netstead/workbench/related.py`**

```python
"""Foreign-key relations between a network's tables, read from its spec (never hard-coded).

The related-records view tints rows that are a foreign key away from what is focused or
highlighted: the lanes of a link (``lane.link_id`` points *in*), the nodes a link references
(``link.from_node_id`` points *out*), and so on. Every relation is a predicate
``<column> IN (<values>)`` on the related table, with ``values`` bounded by the highlight: an
inbound key reuses the highlighted ids as they are; an outbound key costs one query for the
distinct key values of the highlighted rows. It is a read-only view: nothing here is recorded.

Only single-column keys between two *different* tables are followed: self-references
(``link.parent_link_id``) and composite keys are out of scope for now.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from netstead.viz.styling import json_scalar
from netstead.viz.tables import columns_of, primary_key, rowcount_of

__all__ = [
    "MAX_MAP_IDS",
    "MAX_SOURCE_IDS",
    "ForeignKey",
    "Match",
    "Relation",
    "RelationGraph",
    "foreign_keys",
    "primary_keys",
    "relation_graph",
]

#: Highlighted ids accepted per request (and the cap on a hop-2 frontier).
MAX_SOURCE_IDS = 10_000
#: Link/node ids returned for map tinting; beyond this the answer says ``truncated``.
MAX_MAP_IDS = 50_000


@dataclass(frozen=True)
class ForeignKey:
    """One single-column foreign key: ``table.column`` references ``ref_table.ref_column``."""

    table: str
    column: str
    ref_table: str
    ref_column: str

    @property
    def label(self) -> str:
        """How the UI names the relation, e.g. ``lane.link_id → link``."""
        return f"{self.table}.{self.column} → {self.ref_table}"


@dataclass(frozen=True)
class Match:
    """Rows whose ``column`` is in ``values``, reached through the relation named ``via``."""

    column: str
    values: tuple[Any, ...]
    via: str


@dataclass
class Relation:
    """Everything related in one table: the OR of its matches, at the hop it was first reached."""

    table: str
    hop: int
    matches: list[Match] = field(default_factory=list)
    partial: bool = False  # derived from a frontier capped at MAX_SOURCE_IDS

    @property
    def via(self) -> list[str]:
        """The distinct relation labels that reach this table, sorted."""
        return sorted({m.via for m in self.matches})


@dataclass(frozen=True)
class RelationGraph:
    """A network's browsable tables, their primary keys, and the foreign keys between them."""

    tables: Mapping[str, Any]
    fks: tuple[ForeignKey, ...]
    pks: Mapping[str, str | None]


def _single(fields: str | list[str]) -> str | None:
    if isinstance(fields, str):
        return fields
    return fields[0] if len(fields) == 1 else None


def _schemas(spec: Any) -> dict[str, Any]:
    return {
        r.name: r.table_schema
        for r in spec.resources
        if r.table_schema is not None and not isinstance(r.table_schema, str)
    }


def foreign_keys(spec: Any, columns: Mapping[str, Sequence[str]]) -> list[ForeignKey]:
    """Single-column FKs between two different tables in ``columns``, both columns present."""
    out: list[ForeignKey] = []
    for name, schema in _schemas(spec).items():
        if name not in columns:
            continue
        for fk in schema.foreign_keys:
            ref_table = fk.reference.resource or name
            column, ref_column = _single(fk.fields), _single(fk.reference.fields)
            if ref_table == name or ref_table not in columns or column is None or ref_column is None:
                continue
            if column in columns[name] and ref_column in columns[ref_table]:
                out.append(ForeignKey(name, column, ref_table, ref_column))
    return out


def primary_keys(spec: Any, columns: Mapping[str, Sequence[str]]) -> dict[str, str | None]:
    """Each table's key: the spec's single-column ``primaryKey`` when present, else the ``*_id`` heuristic."""
    declared = {name: schema.primary_key for name, schema in _schemas(spec).items()}
    out: dict[str, str | None] = {}
    for name, cols in columns.items():
        pk = declared.get(name)
        out[name] = pk if isinstance(pk, str) and pk in cols else primary_key(name, list(cols))
    return out


def relation_graph(spec: Any, tables: Mapping[str, Any]) -> RelationGraph:
    """The :class:`RelationGraph` over ``tables`` (pandas frames or lazy corral tables)."""
    columns = {name: columns_of(src) for name, src in tables.items()}
    return RelationGraph(tables=tables, fks=tuple(foreign_keys(spec, columns)), pks=primary_keys(spec, columns))
```

`relate`, `restrict`, `count`, `ids_of` and `row_vias` come in Task 3, which adds them to `__all__`. Listing them now
would trip ruff's F822 (undefined name in `__all__`).

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/related.py packages/netstead/tests/test_workbench_related.py
git commit -m "feat(workbench): FK relation graph read from the GMNS spec"
```

---

### Task 3: Relations: one hop (or more), counts, ids, filter, per-row "via"

**Files:**
- Modify: `packages/netstead/netstead/workbench/related.py`
- Test: `packages/netstead/tests/test_workbench_related.py`

- [ ] **Step 1: Append the failing tests**

Add `from netstead.workbench.related import Match, Relation, count, ids_of, relate, restrict, row_vias` to the imports, then append:

```python
def test_a_link_reaches_its_lanes_and_its_end_nodes(graph, lw):
    link = lw.links_df().iloc[0]
    rel = relate(graph, {"link": [int(link.link_id)]})
    assert rel["lane"].hop == 1 and rel["lane"].via == ["lane.link_id → link"]
    lanes = as_pandas(graph.tables["lane"])
    assert count(graph.tables["lane"], rel["lane"].matches) == int((lanes.link_id == link.link_id).sum())
    ids, more = ids_of(graph.tables["node"], rel["node"].matches, "node_id", limit=10)
    assert set(ids) == {int(link.from_node_id), int(link.to_node_id)} and not more
    assert rel["node"].via == ["link.from_node_id → node", "link.to_node_id → node"]
    assert "link" not in rel  # a source table is never "related" to itself


def test_a_node_reaches_links_both_ways_and_hop_two_reaches_their_lanes(graph, lw):
    links = lw.links_df()
    touching = set(links.loc[(links.from_node_id == 1) | (links.to_node_id == 1), "link_id"].astype(int))
    rel = relate(graph, {"node": [1]})
    ids, more = ids_of(graph.tables["link"], rel["link"].matches, "link_id", limit=1000)
    assert set(ids) == touching and not more
    assert "lane" not in rel  # one hop by default
    rel2 = relate(graph, {"node": [1]}, hops=2)
    lanes = as_pandas(graph.tables["lane"])
    assert rel2["lane"].hop == 2
    assert count(graph.tables["lane"], rel2["lane"].matches) == int(lanes.link_id.isin(touching).sum())


def test_empty_sources_relate_nothing(graph):
    assert relate(graph, {"link": []}) == {} and relate(graph, {}) == {}


def test_ids_of_truncates_in_key_order(graph):
    rel = relate(graph, {"node": [1, 2, 3, 4, 5]})
    ids, more = ids_of(graph.tables["link"], rel["link"].matches, "link_id", limit=2)
    assert len(ids) == 2 and ids == sorted(ids) and more


def test_restrict_works_on_frames_and_lazy_tables(graph):
    frame = pd.DataFrame({"a": [1, 2, 3], "b": [9, 9, 4]})
    assert restrict(frame, [Match("a", (1,), "x"), Match("b", (4,), "y")]).a.tolist() == [1, 3]
    assert restrict(frame, []).empty
    lazy = restrict(graph.tables["lane"], [Match("link_id", (1,), "x")])
    assert as_pandas(lazy).link_id.unique().tolist() == [1]
    assert count(graph.tables["lane"], []) == 0


def test_row_vias_names_the_matching_key_per_row():
    rel = Relation("movement", 1, [Match("ib_link_id", (1,), "movement.ib_link_id → link"),
                                   Match("ob_link_id", (2,), "movement.ob_link_id → link")])
    page = pd.DataFrame({"mvmt_id": [7, 8, 9], "ib_link_id": [1, 5, 5], "ob_link_id": [3, 2, 6]})
    assert row_vias(page, rel) == ["movement.ib_link_id → link", "movement.ob_link_id → link", None]
    assert row_vias(page, None) == [None, None, None]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related.py -q`
Expected: collection error, `ImportError: cannot import name 'count'`.

- [ ] **Step 3: Append the implementation to `related.py`**

Add `"count"`, `"ids_of"`, `"relate"`, `"restrict"` and `"row_vias"` to `__all__`, keeping it alphabetical. Then append:

```python
def restrict(src: Any, matches: Sequence[Match]) -> Any:
    """``src`` limited to rows any of ``matches`` selects; no matches selects nothing.

    ``src`` is a pandas frame (masked in memory) or a lazy corral table (an ibis ``IN`` predicate
    pushed to DuckDB; no SQL text).
    """
    if isinstance(src, pd.DataFrame):
        mask = pd.Series(False, index=src.index)
        for m in matches:
            mask |= src[m.column].isin(m.values)
        return src[mask]
    if not matches:
        return src.limit(0)

    def predicate(expr: Any) -> Any:
        return expr.filter(functools.reduce(operator.or_, [expr[m.column].isin(list(m.values)) for m in matches]))

    return src.filter(predicate)


def count(src: Any, matches: Sequence[Match]) -> int:
    """How many rows of ``src`` the matches select (one DuckDB ``COUNT`` for a lazy table)."""
    return rowcount_of(restrict(src, matches))


def ids_of(src: Any, matches: Sequence[Match], pk: str, *, limit: int) -> tuple[list[Any], bool]:
    """The first ``limit`` primary keys (in key order) of the matched rows, and whether there are more."""
    sub = restrict(src, matches)
    if isinstance(sub, pd.DataFrame):
        keys = sub[pk].dropna().sort_values().head(limit + 1).tolist()
    else:
        keys = sub.select(pk).order_by(pk).limit(limit + 1).to_pandas()[pk].dropna().tolist()
    return [json_scalar(k) for k in keys[:limit]], len(keys) > limit


def _distinct(src: Any, column: str, pk: str, ids: Sequence[Any]) -> tuple[Any, ...]:
    """Distinct non-null ``column`` values of the rows whose ``pk`` is in ``ids`` (bounded by ``ids``)."""
    sub = restrict(src, [Match(pk, tuple(ids), "")])
    frame = sub if isinstance(sub, pd.DataFrame) else sub.select(column).to_pandas()
    return tuple(json_scalar(v) for v in pd.unique(frame[column].dropna()))


def _hop(graph: RelationGraph, frontier: Mapping[str, tuple[Any, ...]], reached: set[str], hop: int) -> dict[str, Relation]:
    """Every table one key away from ``frontier`` that is not yet ``reached``."""
    found: dict[str, Relation] = {}
    for fk in graph.fks:
        ref_pk, own_pk = graph.pks.get(fk.ref_table), graph.pks.get(fk.table)
        if fk.ref_table in frontier and fk.table not in reached and ref_pk:  # inbound: fk.table points at the frontier
            ids = frontier[fk.ref_table]
            values = ids if fk.ref_column == ref_pk else _distinct(graph.tables[fk.ref_table], fk.ref_column, ref_pk, ids)
            found.setdefault(fk.table, Relation(fk.table, hop)).matches.append(Match(fk.column, values, fk.label))
        if fk.table in frontier and fk.ref_table not in reached and own_pk:  # outbound: the frontier points at it
            values = _distinct(graph.tables[fk.table], fk.column, own_pk, frontier[fk.table])
            found.setdefault(fk.ref_table, Relation(fk.ref_table, hop)).matches.append(
                Match(fk.ref_column, values, fk.label)
            )
    return {name: r for name, r in found.items() if any(m.values for m in r.matches)}


def relate(graph: RelationGraph, sources: Mapping[str, Sequence[Any]], *, hops: int = 1) -> dict[str, Relation]:
    """Tables related to ``sources`` (``{table: ids}``) within ``hops`` keys, each at the hop it is first reached.

    A table is never revisited, sources included, so hop 2 reaches *new* tables (link → lane →
    lane_tod), never back to the source table.
    """
    frontier = {t: tuple(ids) for t, ids in sources.items() if t in graph.tables and ids}
    reached, out, partial = set(frontier), {}, False
    for hop in range(1, hops + 1):
        found = _hop(graph, frontier, reached, hop)
        for relation in found.values():
            relation.partial = partial
        out.update(found)
        reached |= set(found)
        if hop == hops or not found:
            break
        frontier = {}
        for name, relation in found.items():
            pk = graph.pks.get(name)
            if pk:
                ids, more = ids_of(graph.tables[name], relation.matches, pk, limit=MAX_SOURCE_IDS)
                frontier[name], partial = tuple(ids), partial or more
    return out


def row_vias(page: pd.DataFrame, relation: Relation | None) -> list[str | None]:
    """Per row of ``page``: the first relation label that selects it, else ``None`` (pure pandas)."""
    out: list[str | None] = [None] * len(page)
    for m in relation.matches if relation else []:
        hits = page[m.column].isin(m.values).tolist() if m.column in page.columns else [False] * len(page)
        out = [prev or (m.via if hit else None) for prev, hit in zip(out, hits, strict=True)]
    return out
```

`rowcount_of` is already exported by `viz.tables` (it is used there). If ruff flags `_hop`'s line length, wrap the
`values = …` line.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair, then `uv run python scripts/lint_no_sql.py`.
Expected: all pass. `lint_no_sql` stays clean, because `isin` is an ibis expression, not SQL text.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/related.py packages/netstead/tests/test_workbench_related.py
git commit -m "feat(workbench): related records by FK, bounded by the highlight"
```

---

### Task 4: `locate_row`: where a row sits in a page's order

**Files:**
- Modify: `packages/netstead/netstead/viz/tables.py`
- Test: `packages/netstead/tests/test_viz_tables.py`

- [ ] **Step 1: Append the failing tests to `test_viz_tables.py`**

Add `locate_row` to the `from netstead.viz.tables import …` line, then append:

```python
def test_locate_row_follows_filter_and_sort():
    df = pd.DataFrame({"link_id": [5, 3, 9, 1], "name": ["a", "b", "a", "c"]})
    assert locate_row(df, 9, pk="link_id") == 2
    assert locate_row(df, 9, pk="link_id", sort="link_id", direction="desc") == 0
    assert locate_row(df, 9, pk="link_id", filter_spec=[{"col": "name", "op": "eq", "val": "a"}]) == 1
    assert locate_row(df, 3, pk="link_id", filter_spec=[{"col": "name", "op": "eq", "val": "a"}]) is None
    assert locate_row(df, 9, pk="link_id", ids=[1, 9]) == 1
    assert locate_row(df, 9, pk="link_id", ids=[]) is None  # an explicit empty id list holds no rows


def test_locate_row_is_none_for_lazy_tables_and_missing_keys(tmp_path):
    df = pd.DataFrame({"link_id": [1, 2]})
    assert locate_row(df, 1, pk="nope") is None
    path = tmp_path / "t.parquet"
    df.to_parquet(path)
    engine = IbisEngine()
    from corral.dataset.table import Table

    assert locate_row(Table(name="link", expr=engine.scan(path), engine=engine), 1, pk="link_id") is None
    engine.close()
```

This is the same keyword constructor the file's existing lazy fixture uses (`Table(name=…, expr=…, engine=…)`).

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_viz_tables.py -q`
Expected: `ImportError: cannot import name 'locate_row'`.

- [ ] **Step 3: Implement in `packages/netstead/netstead/viz/tables.py`**

Add `"locate_row",` to `__all__` (alphabetical, after `"FilterError",`). Add `import numpy as np` after
`import pandas as pd`. Then append after `page_table`:

```python
def locate_row(
    source: Any,
    key: Any,
    *,
    pk: str,
    sort: str | None = None,
    direction: str = "asc",
    filter_spec: list[dict] | None = None,
    ids: list | None = None,
) -> int | None:
    """Index of the row whose ``pk`` is ``key``, in the order :func:`page_table` pages ``source``.

    Only eager frames are located (the map-linked ``link``/``node`` tables); a lazy table, an
    unknown key column, or a row filtered out answers ``None``. Unlike ``page_table``, an empty
    ``ids`` list means "no rows".

    >>> locate_row(pd.DataFrame({"id": [4, 2, 7]}), 7, pk="id", sort="id", direction="desc")
    0
    """
    if not _is_frame(source) or pk not in source.columns:
        return None
    df = source
    if ids is not None:
        df = df[df[pk].isin(ids)]
    if filter_spec:
        df = _apply_filter(df, filter_spec)
    if sort:
        if sort not in df.columns:
            raise FilterError(f"unknown sort column {sort!r}")
        df = df.sort_values(sort, ascending=(direction != "desc"), kind="stable")
    hits = np.flatnonzero((df[pk] == key).to_numpy(dtype=bool, na_value=False))
    return int(hits[0]) if len(hits) else None
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_viz_tables.py --doctest-modules packages/netstead/netstead/viz/tables.py -q`
Expected: all pass, including 2 new tests and 1 doctest.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/viz/tables.py packages/netstead/tests/test_viz_tables.py
git commit -m "feat(viz): locate_row finds a row's index in the paged order"
```

---

### Task 5: Network routes: schema FKs, `POST rows`, `POST locate`, `POST related`

**Files:**
- Modify: `packages/netstead/netstead/workbench/routes/network.py`
- Test: `packages/netstead/tests/test_workbench_related_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_related_routes.py`:

```python
"""Tests for the related-records, POST rows and locate routes (read-only, never recorded)."""

import json

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.workbench import Session, build_app

BASE = "/api/n/leavenworth/roadway"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wb")
    src = str(leavenworth.parquet_dir())
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([src])}
    s = Session(project_dir=tmp, environ=env)
    s.dispatch({"type": "open_network", "source": src})
    return s


@pytest.fixture(scope="module")
def client(session):
    return TestClient(build_app(session))


@pytest.fixture(scope="module")
def link1(session):
    return session.registry.get("leavenworth").links_df().set_index("link_id").loc[1]


def test_schema_lists_spec_keys(client):
    j = client.get(f"{BASE}/table/link/schema").json()
    assert j["primary_key"] == "link_id"
    fks = {(f["column"], f["ref_table"], f["navigable"]) for f in j["foreign_keys"]}
    assert {("from_node_id", "node", True), ("to_node_id", "node", True)} <= fks
    lane = client.get(f"{BASE}/table/lane/schema").json()
    assert lane["primary_key"] == "lane_id" and [f["column"] for f in lane["foreign_keys"]] == ["link_id"]


def test_related_summary_and_map_ids(client, link1):
    j = client.post(f"{BASE}/related", json={"sources": {"link": [1]}}).json()
    by = {t["table"]: t for t in j["tables"]}
    assert by["lane"]["count"] >= 1 and by["lane"]["via"] == ["lane.link_id → link"] and by["lane"]["hop"] == 1
    assert set(j["map"]["node"]["ids"]) == {int(link1.from_node_id), int(link1.to_node_id)}
    assert j["map"]["node"]["truncated"] is False and "link" not in j["map"]


def test_related_is_read_only(client, session):
    before = len(session.history)
    assert client.post(f"{BASE}/related", json={"sources": {"node": [1]}, "hops": 2}).status_code == 200
    assert len(session.history) == before


def test_related_rejects_unknown_tables_and_too_many_ids(client):
    assert client.post(f"{BASE}/related", json={"sources": {"nope": [1]}}).status_code == 400
    r = client.post(f"{BASE}/related", json={"sources": {"link": list(range(10_001))}})
    assert r.status_code == 422 and "narrow the highlight" in r.text and "10000" not in r.text  # no echoed ids


def test_post_rows_matches_get(client):
    get = client.get(f"{BASE}/table/link/rows", params={"limit": 7, "sort": "link_id", "dir": "desc"}).json()
    post = client.post(f"{BASE}/table/link/rows", json={"limit": 7, "sort": "link_id", "dir": "desc"}).json()
    assert post == get


def test_post_rows_empty_ids_means_no_rows(client):
    assert client.post(f"{BASE}/table/link/rows", json={"ids": []}).json()["total"] == 0


def test_post_rows_tints_related_rows(client):
    j = client.post(f"{BASE}/table/lane/rows", json={"limit": 500, "related": {"sources": {"link": [1]}}}).json()
    link_col = j["columns"].index("link_id")
    tinted = [row[link_col] for row, via in zip(j["rows"], j["related"], strict=True) if via]
    assert tinted and set(tinted) == {1}
    assert set(v for v in j["related"] if v) == {"lane.link_id → link"}


def test_post_rows_filters_to_related_and_to_sources(client):
    body = {"related": {"sources": {"link": [1]}}, "related_mode": "filter"}
    lanes = client.post(f"{BASE}/table/lane/rows", json=body).json()
    assert lanes["total"] >= 1 and "related" not in lanes
    links = client.post(f"{BASE}/table/link/rows", json=body).json()
    assert links["total"] == 1  # the source table filters to the sources themselves


def test_locate_follows_the_page_order(client):
    rows = client.post(f"{BASE}/table/link/rows", json={"limit": 500, "sort": "link_id", "dir": "desc"}).json()
    ids = [r[rows["columns"].index("link_id")] for r in rows["rows"]]
    j = client.post(f"{BASE}/table/link/locate", json={"id": 5, "sort": "link_id", "dir": "desc"}).json()
    assert j["index"] == ids.index(5)
    assert client.post(f"{BASE}/table/lane/locate", json={"id": 1}).json() == {"index": None}  # lazy table


def test_post_routes_keep_the_origin_guard(client):
    r = client.post(f"{BASE}/related", json={"sources": {"link": [1]}}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related_routes.py -q`
Expected: failures. `foreign_keys` is missing from the schema, and the POST routes answer 405.

- [ ] **Step 3: Replace `packages/netstead/netstead/workbench/routes/network.py` with**

```python
"""Per-network data routes: ``/api/n/{net_id}/{component}/...``.

The ``component`` segment is ``roadway`` today; ``transit`` answers 501 until the
GTFS component lands (P6). Heavy payloads are cached on the handle per version.

Table rows can be read by ``GET`` (query string) or ``POST`` (JSON body). The POST form exists
because id lists (a box-selection, the related-records sources) outgrow a URL; it also carries
the related-records ``tint``/``filter``. ``/related`` and ``/locate`` are read-only views: like
every route here, nothing is recorded in the session history.
"""

from __future__ import annotations

import json as _json
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from netstead.viz.buffers import network_attrs, pack_network
from netstead.viz.styling import json_scalar, property_payload, styleable_columns
from netstead.viz.tables import FilterError, locate_row, page_table, parse_ids, table_list_entry, table_schema

from ..registry import NetworkHandle
from ..related import (
    MAX_MAP_IDS,
    MAX_SOURCE_IDS,
    Match,
    Relation,
    RelationGraph,
    count,
    ids_of,
    relate,
    relation_graph,
    restrict,
    row_vias,
)
from ..session import Session

__all__ = ["LocateQuery", "RelatedQuery", "RowsQuery", "network_router"]

ScalarId = int | str


class RelatedQuery(BaseModel):
    """What to find related records for: highlighted ids per table, and how many keys away."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    sources: dict[str, list[ScalarId]]
    hops: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def _bounded(self) -> RelatedQuery:
        if sum(len(ids) for ids in self.sources.values()) > MAX_SOURCE_IDS:
            raise ValueError(f"too many ids (over {MAX_SOURCE_IDS:,}); narrow the highlight")
        return self


class RowsQuery(BaseModel):
    """One page of a table: the GET parameters as JSON, plus an optional related ``tint``/``filter``."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1)
    sort: str | None = None
    dir: Literal["asc", "desc"] = "asc"
    filter: list[dict[str, Any]] | None = None
    ids: list[ScalarId] | None = Field(default=None, max_length=MAX_SOURCE_IDS)
    related: RelatedQuery | None = None
    related_mode: Literal["tint", "filter"] = "tint"


class LocateQuery(RowsQuery):
    """Where the row whose primary key is ``id`` sits, in a :class:`RowsQuery`'s order."""

    id: ScalarId


def _coerce_key(raw: str) -> Any:
    try:
        return int(raw)
    except ValueError:
        return raw


def network_router(session: Session) -> APIRouter:
    """Build the ``/api/n/{net_id}/{component}`` router bound to ``session``."""
    router = APIRouter(prefix="/api/n/{net_id}/{component}")

    def handle(net_id: str, component: str) -> NetworkHandle:
        if component == "transit":
            raise HTTPException(501, "the transit component is not supported yet (phase P6)")
        if component != "roadway":
            raise HTTPException(404, f"unknown component {component!r}")
        try:
            return session.registry.get(net_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc

    def table(h: NetworkHandle, name: str) -> Any:
        tables = h.tables()
        if name not in tables:
            raise HTTPException(404, f"unknown table {name!r}")
        return tables[name]

    def graph(h: NetworkHandle) -> RelationGraph:
        return h.cached("relations", lambda: relation_graph(h.roadway.spec, h.tables()))

    def related_of(g: RelationGraph, q: RelatedQuery) -> dict[str, Relation]:
        unknown = sorted(set(q.sources) - set(g.tables))
        if unknown:
            raise HTTPException(400, f"unknown table(s) {unknown}")
        return relate(g, q.sources, hops=q.hops)

    def prepared(h: NetworkHandle, name: str, q: RowsQuery) -> tuple[Any, str | None, Relation | None]:
        """The table (narrowed when ``related_mode`` is ``filter``), its key, and its relation (for the tint)."""
        g, src = graph(h), table(h, name)
        pk = g.pks.get(name)
        if q.ids is not None and not q.ids:  # an explicit empty id list is "no rows", not "all rows"
            src = restrict(src, [])
        if q.related is None:
            return src, pk, None
        relation = related_of(g, q.related).get(name)
        if q.related_mode == "filter":
            if relation is not None:
                src = restrict(src, relation.matches)
            elif name in q.related.sources and pk:  # the source table: its own highlighted rows
                src = restrict(src, [Match(pk, tuple(q.related.sources[name]), "")])
            else:
                src = restrict(src, [])
        return src, pk, relation

    def page(src: Any, pk: str | None, q: RowsQuery) -> dict[str, Any]:
        try:
            return page_table(
                src, offset=q.offset, limit=q.limit, sort=q.sort, direction=q.dir,
                filter_spec=q.filter, ids=q.ids or None, pk=pk,
            )
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/network.bin")
    def network_bin(net_id: str, component: str) -> Response:
        h = handle(net_id, component)
        data = h.cached("network.bin", lambda: pack_network(h.links_df(), h.nodes_df()))
        return Response(data, media_type="application/octet-stream")

    @router.get("/network.attrs.json")
    def network_attrs_json(net_id: str, component: str) -> dict[str, Any]:
        h = handle(net_id, component)
        return h.cached("network.attrs", lambda: network_attrs(h.links_df()))

    @router.get("/properties")
    def properties(net_id: str, component: str) -> dict[str, Any]:
        return {"properties": styleable_columns(handle(net_id, component).links_df())}

    @router.get("/property/{name}")
    def property_values(net_id: str, component: str, name: str) -> dict[str, Any]:
        payload = property_payload(handle(net_id, component).links_df(), name)
        if payload is None:
            raise HTTPException(404, f"unknown property {name!r}")
        return payload

    @router.get("/feature/{table_name}/{pk_value}")
    def feature(net_id: str, component: str, table_name: str, pk_value: str) -> dict[str, Any]:
        h = handle(net_id, component)
        src, pk = table(h, table_name), graph(h).pks.get(table_name)
        if pk is None:
            raise HTTPException(404, f"table {table_name!r} has no primary key")
        key = _coerce_key(pk_value)
        rows = page_table(src, limit=1, ids=[key], pk=pk)
        if not rows["rows"]:
            raise HTTPException(404, f"{table_name} {pk_value} not found")
        return {
            "table": table_name,
            "pk": pk,
            "id": json_scalar(key),
            "attributes": dict(zip(rows["columns"], rows["rows"][0], strict=True)),
        }

    @router.get("/tables")
    def tables_list(net_id: str, component: str) -> dict[str, Any]:
        return {"tables": [table_list_entry(n, src) for n, src in handle(net_id, component).tables().items()]}

    @router.get("/table/{table_name}/schema")
    def table_schema_ep(net_id: str, component: str, table_name: str) -> dict[str, Any]:
        h = handle(net_id, component)
        g = graph(h)
        payload = table_schema(table_name, table(h, table_name))
        payload["primary_key"] = g.pks.get(table_name)
        payload["foreign_keys"] = [
            {
                "column": fk.column,
                "ref_table": fk.ref_table,
                "ref_column": fk.ref_column,
                "navigable": fk.ref_column == g.pks.get(fk.ref_table),
            }
            for fk in g.fks
            if fk.table == table_name
        ]
        return payload

    @router.get("/table/{table_name}/rows")
    def table_rows(
        net_id: str,
        component: str,
        table_name: str,
        offset: int = 0,
        limit: int = 100,
        sort: str | None = None,
        dir: str = "asc",
        filter: str | None = None,
        ids: str | None = None,
    ) -> dict[str, Any]:
        h = handle(net_id, component)
        try:
            spec = _json.loads(filter) if filter else None
        except _json.JSONDecodeError as exc:
            raise HTTPException(400, f"bad filter json: {exc}") from exc
        try:
            payload = page_table(
                table(h, table_name), offset=offset, limit=limit, sort=sort, direction=dir,
                filter_spec=spec, ids=parse_ids(ids), pk=graph(h).pks.get(table_name),
            )
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"name": table_name, **payload}

    @router.post("/table/{table_name}/rows")
    def table_rows_post(net_id: str, component: str, table_name: str, q: RowsQuery) -> dict[str, Any]:
        """One page; with ``related`` in ``tint`` mode, ``related[i]`` names why row ``i`` is related (or null)."""
        src, pk, relation = prepared(handle(net_id, component), table_name, q)
        payload = page(src, pk, q)
        if q.related is not None and q.related_mode == "tint":
            payload["related"] = row_vias(pd.DataFrame(payload["rows"], columns=payload["columns"]), relation)
        return {"name": table_name, **payload}

    @router.post("/table/{table_name}/locate")
    def locate(net_id: str, component: str, table_name: str, q: LocateQuery) -> dict[str, Any]:
        """``{"index": i}``: the row's position in ``q``'s order (``None``: lazy table, or not in the view)."""
        src, pk, _ = prepared(handle(net_id, component), table_name, q)
        if pk is None:
            return {"index": None}
        try:
            index = locate_row(src, q.id, pk=pk, sort=q.sort, direction=q.dir, filter_spec=q.filter, ids=q.ids)
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"index": index}

    @router.post("/related")
    def related(net_id: str, component: str, q: RelatedQuery) -> dict[str, Any]:
        """Per related table: hop, relation labels and row count; plus link/node ids to tint on the map."""
        g = graph(handle(net_id, component))
        relations = related_of(g, q)
        tables = [
            {"table": name, "hop": r.hop, "via": r.via, "partial": r.partial, "count": count(g.tables[name], r.matches)}
            for name, r in relations.items()
        ]
        drawn: dict[str, Any] = {}
        for name in ("link", "node"):
            relation, pk = relations.get(name), g.pks.get(name)
            if relation is not None and pk:
                ids, more = ids_of(g.tables[name], relation.matches, pk, limit=MAX_MAP_IDS)
                drawn[name] = {"ids": ids, "truncated": more}
        return {"hops": q.hops, "tables": tables, "map": drawn}

    return router
```

Notes:
- `columns_of` and `primary_key` are no longer imported here. Ruff will flag them if left in.
- The GET `rows` route keeps its old semantics, but it now uses the spec keys (Decision 2).
- The custom `RequestValidationError` handler in `server.py` returns only `type/loc/msg`, so the 422 never echoes the
  ids. That is the point of the last assertion in `test_related_rejects_unknown_tables_and_too_many_ids`.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related_routes.py packages/netstead/tests/test_workbench_network_routes.py -q`
Expected: all pass. That is the 11 new tests, plus the 8 existing network-route tests unchanged.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/routes/network.py packages/netstead/tests/test_workbench_related_routes.py
git commit -m "feat(workbench): related, locate and POST rows routes; spec keys in table schema"
```

---

### Task 6: Settings payload: read-only keys, restart keys, section notes

**Files:**
- Modify: `packages/netstead/netstead/workbench/actions.py`
- Modify: `packages/netstead/netstead/workbench/session.py`
- Test: `packages/netstead/tests/test_workbench_actions.py`, `packages/netstead/tests/test_workbench_session.py`

- [ ] **Step 1: Append the failing tests**

To `test_workbench_actions.py` (add `is_secret_name` to its `from netstead.workbench.actions import …` line):

```python
@pytest.mark.parametrize(
    ("name", "expected"),
    [("api_key", True), ("token", True), ("password", True), ("credentials", False), ("key_env", False),
     ("basemap", False), ("keyring_hosts", False)],
)
def test_is_secret_name(name, expected):
    assert is_secret_name(name) is expected
```

To `test_workbench_session.py` (add `from netstead.workbench.session import refusal_reason`):

```python
def test_settings_payload_marks_readonly_restart_and_unused_sections(opened):
    p = opened.settings_payload()
    assert set(p["readonly"]) == {"io.allowed_roots"}
    assert "config files" in p["readonly"]["io.allowed_roots"]
    assert set(p["restart"]) == {"app.host", "app.port", "app.console"}
    assert set(p["notes"]) == {"engine", "validation", "credentials"}


@pytest.mark.parametrize(
    ("key", "refused"),
    [("io.allowed_roots", True), ("io", True), ("IO.Allowed_Roots", True), ("io.spec_version", False),
     ("llm.openai.api_key", True), ("credentials.keyring_hosts", False), ("viz.basemap", False)],
)
def test_refusal_reason(key, refused):
    assert (refusal_reason(key) is not None) is refused
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_actions.py packages/netstead/tests/test_workbench_session.py -q`
Expected: `ImportError: cannot import name 'is_secret_name'`.

- [ ] **Step 3: Implement**

In `actions.py`:
- Add `"is_secret_name",` to `__all__` (after `"action_json_schema",`).
- In `SetSetting._no_secrets`, replace
  `if any(_SECRET_NAME.search(n) and n.lower() not in _SECRET_NAME_ALLOWED for n in names):`
  with `if any(is_secret_name(n) for n in names):`.
- After the `_SECRET_NAME_ALLOWED = …` definition, add:

```python
def is_secret_name(name: str) -> bool:
    """Whether a setting named ``name`` could only hold a credential (``api_key``, ``token``, ...).

    >>> is_secret_name("api_key"), is_secret_name("credentials"), is_secret_name("basemap")
    (True, False, False)
    """
    return bool(_SECRET_NAME.search(name)) and name.lower() not in _SECRET_NAME_ALLOWED
```

In `session.py`:
- Add `is_secret_name` to the `from .actions import (...)` list.
- In `_do_set_setting`, keep the first line (`key = action.key.strip().lower()`; it is used again further down).
  Replace only the second line:

```python
        if any(key == k or k.startswith(f"{key}.") for k in _CONFIG_ONLY_KEYS):  # "io" would replace it too
```

  with:

```python
        if _is_config_only(key):  # "io" would replace io.allowed_roots too
```

  The error message that follows is unchanged.
- Replace `settings_payload`:

```python
    def settings_payload(self) -> dict[str, Any]:
        """Settings values, per-key sources, JSON schema, and file paths, for the Settings UI.

        ``readonly`` maps each key the action API refuses to the reason shown beside it; ``restart``
        lists keys read only at launch; ``notes`` explains sections nothing reads yet.
        """
        sources = dict(self.loaded.sources)
        return {
            "values": self.settings.model_dump(mode="json"),
            "sources": sources,
            "schema": Settings.model_json_schema(),
            "paths": {"user": str(self.loaded.user_path), "project": str(self.loaded.project_path)},
            "readonly": {k: reason for k in sources if (reason := refusal_reason(k))},
            "restart": list(_RESTART_KEYS),
            "notes": dict(_SECTION_NOTES),
        }
```

- After `_CONFIG_ONLY_KEYS = ("io.allowed_roots",)`, add:

```python
#: Shown beside a key :func:`refusal_reason` refuses as config-only.
_CONFIG_ONLY_REASON = (
    "The folders the app may read and write. Set them in a config file, a NETSTEAD_IO__ALLOWED_ROOTS "
    "env var, or on the command line: if an action could widen them, the sandbox would protect nothing."
)
#: Shown beside a secret-named key (none exist today; keys live in the OS keyring).
_SECRET_REASON = "Credentials are never settings. Set API keys in Settings → Language models."
#: Keys the server reads only at launch: a change applies the next time `netstead app` starts.
_RESTART_KEYS = ("app.host", "app.port", "app.console")
#: Sections in the schema that nothing reads yet (see the P1b plan, open question 8).
_SECTION_NOTES = {
    "engine": "Not used by the workbench yet: networks open with DuckDB's own defaults.",
    "validation": "Not used by the workbench yet: validation in the app arrives in phase P2.",
    "credentials": "Not used by the workbench yet: credential sources are shown by the URL check only.",
}


def _is_config_only(key: str) -> bool:
    k = key.strip().lower()
    return any(k == c or c.startswith(f"{k}.") or k.startswith(f"{c}.") for c in _CONFIG_ONLY_KEYS)


def refusal_reason(key: str) -> str | None:
    """Why the action API refuses to change setting ``key`` at every scope, or ``None`` if it may.

    >>> refusal_reason("io.allowed_roots") is not None, refusal_reason("viz.basemap")
    (True, None)
    """
    if _is_config_only(key):
        return _CONFIG_ONLY_REASON
    if is_secret_name(key.strip().rsplit(".", 1)[-1]):
        return _SECRET_REASON
    return None
```

Add `"refusal_reason"` to `session.py`'s `__all__`. If `session.py` has no `__all__`, the import in the test is
enough.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_actions.py packages/netstead/tests/test_workbench_session.py packages/netstead/tests/test_workbench_server.py --doctest-modules packages/netstead/netstead/workbench/actions.py packages/netstead/netstead/workbench/session.py -q`
Expected: all pass. The existing `test_set_setting_cannot_widen_the_sandbox` still matches its message, because the
error text is unchanged.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/actions.py packages/netstead/netstead/workbench/session.py packages/netstead/tests/test_workbench_actions.py packages/netstead/tests/test_workbench_session.py
git commit -m "feat(workbench): settings payload marks read-only, restart and unused keys"
```

---

### Task 7: Node unit-test harness and `settingsform.js`

**Files:**
- Modify: `packages/netstead/tests/conftest.py`
- Create: `packages/netstead/netstead/workbench/static/js/settingsform.js`
- Test: `packages/netstead/tests/test_workbench_js.py`

- [ ] **Step 1: Add the `node_module` fixture to `conftest.py`**

Append after `run_node`:

```python
#: The workbench's ES modules (resolved without importing the FastAPI app).
WORKBENCH_JS = Path(netstead.fixtures.__file__).resolve().parent.parent / "workbench" / "static" / "js"


@pytest.fixture
def node_module(tmp_path: Path, run_node):
    """Evaluate a JS expression against a pure workbench module under node; return its JSON value.

    ``node_module("linking.js", ["pageOffset"], "pageOffset(250, 100)")`` -> ``200``. Only
    import-free modules qualify (they are copied alone, as ``.mjs``, so node treats them as ES
    modules without a package.json); importing one with ``import`` statements fails loudly.
    """
    counter = iter(range(1_000_000))

    def run(module: str, names: list[str], expr: str) -> Any:
        source = (WORKBENCH_JS / module).read_text(encoding="utf-8")
        assert not re.search(r"^\s*import\s", source, re.M), f"{module} must stay import-free to be unit-tested"
        n = next(counter)
        (tmp_path / f"m{n}.mjs").write_text(source, encoding="utf-8")
        script = tmp_path / f"probe{n}.mjs"
        script.write_text(f'import {{ {", ".join(names)} }} from "./m{n}.mjs";\nconsole.log(JSON.stringify({expr}));\n')
        proc = run_node([str(script)])
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run
```

Add `import re` to the conftest imports (alphabetical, after `import json`).

- [ ] **Step 2: Write the failing tests**

Create `packages/netstead/tests/test_workbench_js.py`:

```python
"""Unit tests for the workbench's pure (import-free) front-end modules, run under node."""

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SCHEMA = {
    "properties": {
        "viz": {"$ref": "#/$defs/VizSettings"},
        "app": {"$ref": "#/$defs/AppSettings"},
        "llm": {"$ref": "#/$defs/LLMSettings"},
        "validation": {"$ref": "#/$defs/ValidationSettings"},
        "io": {"$ref": "#/$defs/IOSettings"},
    },
    "$defs": {
        "VizSettings": {"description": "Map rendering.", "properties": {
            "basemap": {"default": "positron", "enum": ["positron", "esri"], "title": "Basemap", "type": "string"}}},
        "AppSettings": {"properties": {
            "port": {"default": 8850, "title": "Port", "type": "integer"},
            "approve_above_s": {"default": 90.0, "minimum": 0, "title": "Approve Above S", "type": "number"},
            "console": {"default": False, "title": "Console", "type": "boolean"}}},
        "LLMSettings": {"properties": {}},
        "ValidationSettings": {"properties": {"rules": {"additionalProperties": {}, "title": "Rules", "type": "object"}}},
        "IOSettings": {"properties": {
            "allowed_roots": {"items": {"type": "string"}, "title": "Allowed Roots", "type": "array"},
            "spec_version": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Spec"}}},
    },
}
PAYLOAD = {
    "schema": SCHEMA,
    "values": {"viz": {"basemap": "esri"}, "app": {"port": 9000, "approve_above_s": 90.0, "console": False},
               "llm": {}, "validation": {"rules": {"r1": {"enabled": False}}},
               "io": {"allowed_roots": ["/data"], "spec_version": None}},
    "sources": {"viz.basemap": "project", "app.port": "env", "app.approve_above_s": "default", "app.console": "default",
                "validation.rules.r1.enabled": "user", "io.allowed_roots": "env", "io.spec_version": "default"},
    "readonly": {"io.allowed_roots": "config only"},
    "restart": ["app.port", "app.console"],
    "notes": {"validation": "not used yet"},
}


def test_sections_skip_folded_llm_and_describe_each_field(node_module):
    secs = node_module("settingsform.js", ["sectionsFrom"], f"sectionsFrom({__import__('json').dumps(PAYLOAD)})")
    assert [s["name"] for s in secs] == ["viz", "app", "validation", "io"]
    viz, app, validation, io = secs
    assert viz["title"] == "Map" and viz["description"] == "Map rendering."
    assert viz["fields"][0].items() >= {"key": "viz.basemap", "kind": "choice", "value": "esri", "source": "project"}.items()
    port, approve, console = app["fields"]
    assert (port["kind"], port["restart"], approve["kind"], approve["min"], console["kind"]) == ("int", True, "float", 0, "bool")
    assert validation["note"] == "not used yet" and validation["fields"][0]["kind"] == "json"
    assert validation["fields"][0]["source"] == "user"  # the highest layer among its nested keys
    roots, spec = io["fields"]
    assert (roots["kind"], roots["readonly"]) == ("list", "config only")
    assert (spec["kind"], spec["nullable"]) == ("text", True)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ({"kind": "int", "label": "Port"}, "9100", {"ok": True, "value": 9100}),
        ({"kind": "int", "label": "Port"}, "9.5", {"ok": False, "error": "Port: enter a whole number"}),
        ({"kind": "float", "label": "S"}, "", {"ok": True, "value": None}),
        ({"kind": "bool", "label": "C"}, False, {"ok": True, "value": False}),
        ({"kind": "list", "label": "T"}, " a, b ,,c ", {"ok": True, "value": ["a", "b", "c"]}),
        ({"kind": "json", "label": "R"}, '{"x": 1}', {"ok": True, "value": {"x": 1}}),
        ({"kind": "json", "label": "R"}, "{x", {"ok": False, "error": "R: not valid JSON"}),
        ({"kind": "choice", "label": "B"}, "esri", {"ok": True, "value": "esri"}),
    ],
)
def test_parse_input(node_module, field, raw, expected):
    import json

    assert node_module("settingsform.js", ["parseInput"], f"parseInput({json.dumps(field)}, {json.dumps(raw)})") == expected


def test_reset_targets_the_layer_the_value_comes_from(node_module):
    got = node_module(
        "settingsform.js", ["resetScope"],
        '["default","user","project","env","session"].map(source => resetScope({ source }))',
    )
    assert got == [None, "user", "project", None, "session"]


def test_scope_note_warns_when_a_higher_layer_wins(node_module):
    got = node_module(
        "settingsform.js", ["scopeNote"],
        '[scopeNote({ source: "env" }, "user"), scopeNote({ source: "project" }, "session"),'
        ' scopeNote({ source: "user" }, "project"), scopeNote({ source: "default", restart: true }, "session")]',
    )
    assert "env" in got[0] and got[1] is None and got[2] is None and "launch" in got[3]
```

- [ ] **Step 3: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_js.py -q`
Expected: failures, because `settingsform.js` does not exist (`FileNotFoundError`).

- [ ] **Step 4: Create `packages/netstead/netstead/workbench/static/js/settingsform.js`**

```js
// Settings form model: /api/settings (JSON schema + values + sources + readonly/restart/notes) -> field
// descriptors, and input parsing. Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).

// Sections another Settings section owns (the Language models panel), hidden from the generated form.
export const FOLDED = new Set(["llm", "select"]);

const TITLES = {
  io: "Input & output", engine: "Engine", osm: "OpenStreetMap", overture: "Overture", build: "Build defaults",
  validation: "Validation", viz: "Map", app: "App server", credentials: "Credentials",
};
// Lowest to highest precedence (config.py: defaults < user < project < env < session).
const RANKS = ["default", "user", "project", "env", "session"];
const rank = source => RANKS.indexOf(source);

const resolve = (schema, node) => (node && node.$ref ? schema.$defs[node.$ref.split("/").pop()] : node);

export function fieldKind(prop) {
  const options = prop.anyOf ? prop.anyOf.filter(o => o.type !== "null") : [prop];
  const nullable = Boolean(prop.anyOf && prop.anyOf.some(o => o.type === "null"));
  const p = options.length === 1 ? options[0] : null;
  if (!p) return { kind: "json", nullable };
  if (p.enum) return { kind: "choice", options: p.enum, nullable };
  if (p.type === "boolean") return { kind: "bool", nullable };
  if (p.type === "integer" || p.type === "number") {
    return { kind: p.type === "integer" ? "int" : "float", nullable,
      min: p.minimum ?? p.exclusiveMinimum ?? null, max: p.maximum ?? p.exclusiveMaximum ?? null };
  }
  if (p.type === "string") return { kind: "text", nullable };
  if (p.type === "array" && p.items && p.items.type === "string") return { kind: "list", nullable };
  return { kind: "json", nullable };
}

// A key's source; for a JSON field (e.g. validation.rules), the highest layer among its nested keys.
export function sourceOf(sources, key) {
  if (sources[key]) return sources[key];
  const nested = Object.entries(sources).filter(([k]) => k.startsWith(`${key}.`)).map(([, s]) => s);
  return nested.length ? nested.reduce((a, b) => (rank(b) > rank(a) ? b : a)) : "default";
}

export function sectionsFrom(payload) {
  const { schema, values, sources, readonly = {}, restart = [], notes = {} } = payload;
  return Object.entries(schema.properties).filter(([name]) => !FOLDED.has(name)).map(([name, ref]) => {
    const def = resolve(schema, ref);
    const fields = Object.entries(def.properties || {}).map(([field, prop]) => {
      const key = `${name}.${field}`;
      return { key, label: prop.title || field, ...fieldKind(prop), value: (values[name] || {})[field],
        default: prop.default ?? null, source: sourceOf(sources, key), readonly: readonly[key] || null,
        restart: restart.includes(key) };
    });
    return { name, title: TITLES[name] || name[0].toUpperCase() + name.slice(1), description: def.description || "",
      note: notes[name] || null, fields };
  });
}

// An input's raw value -> {ok, value} or {ok: false, error}. Empty means "reset": null removes the key from that layer.
export function parseInput(field, raw) {
  if (field.kind === "bool") return { ok: true, value: Boolean(raw) };
  if (raw === "" || raw === null || raw === undefined) return { ok: true, value: null };
  if (field.kind === "choice" || field.kind === "text") return { ok: true, value: String(raw) };
  if (field.kind === "int" || field.kind === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n) || (field.kind === "int" && !Number.isInteger(n))) {
      return { ok: false, error: `${field.label}: enter a ${field.kind === "int" ? "whole " : ""}number` };
    }
    return { ok: true, value: n };
  }
  if (field.kind === "list") return { ok: true, value: String(raw).split(",").map(s => s.trim()).filter(Boolean) };
  try { return { ok: true, value: JSON.parse(raw) }; } catch (e) { return { ok: false, error: `${field.label}: not valid JSON` }; }
}

// Reset removes the value from the layer it comes from; env and defaults can't be reset from the app.
export function resetScope(field) {
  return ["user", "project", "session"].includes(field.source) ? field.source : null;
}

// Why saving `field` at `scope` would not take effect (null when it would).
export function scopeNote(field, scope) {
  if (field.restart && scope === "session") return "Read at launch: a session value has no effect. Save to User or This project.";
  if (rank(field.source) > rank(scope)) {
    const by = field.source === "env" ? "a NETSTEAD_* environment variable" : `the ${field.source} layer`;
    return `Currently set by ${by}; a ${scope} value is saved but won't take effect while that is set.`;
  }
  return null;
}
```

- [ ] **Step 5: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass. `test_every_module_is_served_as_javascript` and `test_js_syntax` pick up the new module.

- [ ] **Step 6: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/tests/conftest.py packages/netstead/tests/test_workbench_js.py packages/netstead/netstead/workbench/static/js/settingsform.js
git commit -m "feat(workbench): settings form model from the JSON schema, node-tested"
```

---

### Task 8: The Settings dialog, with the Language-models panel folded in

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/settings.js`
- Modify: `packages/netstead/netstead/workbench/static/index.html`, `app.css`, `js/llm.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Append the failing static tests to `test_workbench_static.py`**

```python
def test_settings_dialog_hosts_the_language_models_section():
    html = (STATIC_DIR / "index.html").read_text()
    start, end = html.index('id="settings"'), html.index("<!-- /settings -->")
    dialog = html[start:end]
    for element_id in ("set-nav", "set-scope", "set-form", "set-close", "llm-panel", "llm-providers", "llm-quality"):
        assert f'id="{element_id}"' in dialog
    assert 'id="settings-btn"' in html and 'id="llm-close"' not in html


def test_settings_module_is_wired_from_main():
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./settings.js"' in main and "registerSection(" in main and "wireSettings()" in main
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`
Expected: 2 failures (`ValueError: substring not found`; `settings.js` not imported).

- [ ] **Step 3: Markup in `index.html`**

1. In the header, insert after the `jobs-btn` button:

```html
    <button id="settings-btn" class="mini ghost">Settings…</button>
```

2. Change the map gear's label only (Decision 11). Replace
   `data-tip="Map settings" aria-label="Map settings"` with `data-tip="Map display" aria-label="Map display"`, and
   `<h4>Map settings</h4>` with `<h4>Map display</h4>`.

3. Delete the whole `<div class="panel" id="llm-panel" …> … </div>` block, from
   `<div class="panel" id="llm-panel" role="dialog" aria-label="Language models">` to its closing `</div>`, after the
   `llm-close` row. In its place, directly before `<div id="toast" role="status"></div>`, add:

```html
<div id="settings" class="modal" role="dialog" aria-modal="true" aria-labelledby="set-title" hidden>
  <div class="modal-card set-card">
    <div class="modal-head">
      <h3 id="set-title">Settings</h3>
      <label for="set-scope" class="set-scope-lbl">Save to</label>
      <select id="set-scope">
        <option value="user">User (all projects)</option>
        <option value="project">This project</option>
        <option value="session">This session only</option>
      </select>
      <button class="mini ghost" id="set-close" aria-label="Close">&#10005;</button>
    </div>
    <div class="set-layout">
      <nav id="set-nav" aria-label="Settings sections"></nav>
      <div class="modal-body">
        <p class="llm-note" id="set-paths"></p>
        <div id="set-form"></div>
        <section id="llm-panel" aria-label="Language models" hidden>
          <p class="llm-note" id="llm-privacy" aria-live="polite"></p>
          <p class="llm-note" id="llm-storage"></p>
          <table id="llm-providers"></table>
          <h4>Quality &amp; context</h4>
          <div id="llm-quality"></div>
          <h4>Ollama server</h4>
          <div class="row"><input id="llm-ollama-url" class="grow" aria-label="Ollama URL" spellcheck="false" placeholder="http://localhost:11434" />
            <button class="mini" id="llm-ollama-save">Save</button></div>
          <h4>Model catalog</h4>
          <div class="row"><select id="llm-catalog-provider" aria-label="Catalog provider"></select></div>
          <table id="llm-catalog"></table>
          <p class="llm-note" id="llm-catalog-hint"></p>
        </section>
      </div>
    </div>
  </div>
</div>
<!-- /settings -->
```

- [ ] **Step 4: Styles in `app.css`**

Replace the `#llm-panel { position:fixed; … z-index:6; }` rule (two lines) with:

```css
  #llm-panel h4 { margin:14px 0 8px; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
```

In the `@media (max-width: 640px)` block, delete `#llm-panel { top:auto; bottom:44px; }`. Then append:

```css
  /* ---- settings dialog ---- */
  .set-card { width:min(920px, calc(100vw - 32px)); }
  .set-scope-lbl { color:var(--muted); font-size:12px; }
  .set-layout { display:grid; grid-template-columns:170px 1fr; min-height:0; flex:1; overflow:hidden; }
  #set-nav { display:flex; flex-direction:column; gap:2px; padding:10px 8px; border-right:1px solid var(--edge); overflow:auto; }
  #set-nav button { text-align:left; background:none; border:0; color:var(--muted); padding:7px 10px; border-radius:6px; cursor:pointer; }
  #set-nav button.on { background:#20242e; color:var(--ink); }
  .set-field { display:grid; grid-template-columns:minmax(140px, 220px) 1fr auto auto; gap:6px 10px; align-items:center;
               padding:8px 0; border-bottom:1px solid var(--edge); }
  .set-field input:not([type=checkbox]), .set-field select, .set-field textarea { background:#0c0e12; color:var(--ink);
               border:1px solid var(--edge); border-radius:6px; padding:5px 8px; min-width:0; font:inherit; }
  .set-field textarea { font-family:ui-monospace,monospace; font-size:12px; }
  .set-why { grid-column:1 / -1; color:var(--muted); font-size:12px; }
  .set-note { color:var(--accent); font-size:12px; margin:0 0 8px; }
  .src { font-size:10.5px; padding:1px 7px; border-radius:999px; background:#20242e; color:var(--muted); }
  .src-user, .src-project { color:var(--hl); }
  .src-env, .src-session { color:var(--accent); }
  @media (max-width: 640px) {
    .set-layout { grid-template-columns:1fr; }
    #set-nav { flex-direction:row; flex-wrap:wrap; border-right:0; border-bottom:1px solid var(--edge); }
    .set-field { grid-template-columns:1fr auto; }
  }
```

- [ ] **Step 5: Create `packages/netstead/netstead/workbench/static/js/settings.js`**

```js
// Settings dialog: a form generated from the Settings JSON schema; every change is a set_setting action.
// Other modules add their own sections with registerSection (the Language models panel is one).
import { dispatch, getJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { parseInput, resetScope, scopeNote, sectionsFrom } from "./settingsform.js";

const extra = new Map(); // id -> {label, element, onShow}
let payload = null, sections = [], current = null;

export function registerSection(id, label, element, onShow) { extra.set(id, { label, element, onShow }); }

export const settingsOpen = () => !$("settings").hidden;
export function closeSettings() { $("settings").hidden = true; }

export async function openSettings(section) {
  $("settings").hidden = false;
  await refreshSettings();
  showSection(section || current || (sections[0] && sections[0].name));
}

export async function refreshSettings() {
  payload = await getJSON("/api/settings");
  sections = sectionsFrom(payload);
  $("set-paths").textContent = `User file: ${payload.paths.user} · Project file: ${payload.paths.project}`;
  renderNav();
  if (current && !extra.has(current)) renderForm();
}

function renderNav() {
  const items = sections.map(s => [s.name, s.title]).concat([...extra].map(([id, e]) => [id, e.label]));
  $("set-nav").innerHTML = items
    .map(([id, label]) => `<button data-sec="${esc(id)}"${id === current ? ' class="on"' : ""}>${esc(label)}</button>`)
    .join("");
}

function showSection(id) {
  current = id;
  for (const b of $("set-nav").querySelectorAll("button")) b.classList.toggle("on", b.dataset.sec === id);
  const ext = extra.get(id);
  $("set-form").hidden = Boolean(ext);
  $("set-scope").hidden = Boolean(ext); // a registered section saves on its own terms (Language models: user)
  for (const [key, e] of extra) $(e.element).hidden = key !== id;
  if (ext) Promise.resolve(ext.onShow()).catch(e => toast(e.message));
  else renderForm();
}

function inputHTML(f) {
  const attrs = `id="set-${esc(f.key.replace(/\./g, "-"))}" data-key="${esc(f.key)}"${f.readonly ? " disabled" : ""}`;
  if (f.kind === "bool") return `<input type="checkbox" ${attrs}${f.value ? " checked" : ""}>`;
  if (f.kind === "choice") {
    const opts = (f.nullable ? [""] : []).concat(f.options);
    return `<select ${attrs}>${opts.map(o => `<option value="${esc(o)}"${o === (f.value ?? "") ? " selected" : ""}>` +
      `${esc(o === "" ? "(default)" : o)}</option>`).join("")}</select>`;
  }
  if (f.kind === "int" || f.kind === "float") {
    const bounds = (f.min != null ? ` min="${f.min}"` : "") + (f.max != null ? ` max="${f.max}"` : "");
    return `<input type="number" ${attrs} step="${f.kind === "int" ? 1 : "any"}"${bounds} value="${esc(f.value ?? "")}" ` +
      `placeholder="${esc(f.default ?? "default")}">`;
  }
  if (f.kind === "json") return `<textarea ${attrs} rows="3" spellcheck="false">${esc(JSON.stringify(f.value ?? null, null, 1))}</textarea>`;
  const text = f.kind === "list" ? (f.value || []).join(", ") : f.value ?? "";
  const hint = f.kind === "list" ? "comma-separated" : f.default ?? "default";
  return `<input ${attrs} value="${esc(text)}" placeholder="${esc(hint)}" spellcheck="false">`;
}

function fieldHTML(f, scope) {
  const reset = !f.readonly && resetScope(f);
  const note = !f.readonly && scopeNote(f, scope);
  return `<div class="set-field"><label for="set-${esc(f.key.replace(/\./g, "-"))}">${esc(f.label)}` +
    `${f.restart ? ' <span class="tag">applies on next launch</span>' : ""}</label>${inputHTML(f)}` +
    `<span class="src src-${esc(f.source)}" title="Where the current value comes from">${esc(f.source)}</span>` +
    (reset ? `<button class="mini ghost" data-reset="${esc(f.key)}" data-scope="${reset}" ` +
      `title="Remove it from the ${reset} layer">Reset</button>` : "<span></span>") +
    (f.readonly ? `<div class="set-why">${esc(f.readonly)}</div>` : "") +
    (note ? `<div class="set-why">${esc(note)}</div>` : "") + "</div>";
}

function renderForm() {
  const sec = sections.find(s => s.name === current);
  if (!sec) return;
  const scope = $("set-scope").value;
  $("set-form").innerHTML = (sec.description ? `<p class="llm-note">${esc(sec.description)}</p>` : "") +
    (sec.note ? `<p class="set-note">${esc(sec.note)}</p>` : "") + sec.fields.map(f => fieldHTML(f, scope)).join("");
}

async function save(key, value, scope) {
  try {
    await dispatch({ type: "set_setting", key, value, scope });
  } catch (e) {
    toast(e.message);
    await refreshSettings().catch(() => {}); // put the control back to the real value (a 422 is not in history)
  }
}

// Any set_setting, from this dialog, Python, another tab or the assistant, refreshes an open dialog.
export function onSettingsHistory(entry) {
  if (entry.action.type === "set_setting" && settingsOpen()) refreshSettings().catch(e => toast(e.message));
}

export function wireSettings() {
  $("settings-btn").onclick = () => openSettings().catch(e => toast(e.message));
  $("set-close").onclick = closeSettings;
  $("set-nav").onclick = e => { const b = e.target.closest("button[data-sec]"); if (b) showSection(b.dataset.sec); };
  $("set-scope").onchange = () => renderForm();
  $("set-form").onchange = e => {
    const el = e.target.closest("[data-key]");
    if (!el) return;
    const field = sections.flatMap(s => s.fields).find(f => f.key === el.dataset.key);
    const parsed = parseInput(field, el.type === "checkbox" ? el.checked : el.value);
    if (parsed.ok) save(field.key, parsed.value, $("set-scope").value);
    else toast(parsed.error);
  };
  $("set-form").onclick = e => {
    const b = e.target.closest("button[data-reset]");
    if (b) save(b.dataset.reset, null, b.dataset.scope);
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape" && settingsOpen()) closeSettings(); });
}
```

- [ ] **Step 6: Fold the Language-models panel in (`llm.js`)**

- Line 4 comment: replace `// P1b's Settings workspace can mount #llm-panel as a section; until then it floats from "Models…".`
  with `// The panel is a section of the Settings dialog (settings.js); "Models…" opens Settings there.`
- Replace `const panelOpen = () => $("llm-panel").classList.contains("open");` with
  `const panelOpen = () => !$("settings").hidden && !$("llm-panel").hidden;`
- Rename `async function renderPanel()` to `export async function renderLLMPanel()`, and rename its three callers
  (`renderPanel()` → `renderLLMPanel()`).
- In `wireLLM()`, replace:

```js
  $("nl-manage").onclick = () => {
    if ($("llm-panel").classList.toggle("open")) renderPanel().catch(report);
  };
  $("llm-close").onclick = () => $("llm-panel").classList.remove("open");
```

  with:

```js
  $("nl-manage").onclick = () => openSettings("llm").catch(report);
```

  and add `import { openSettings } from "./settings.js";` to the imports. `settings.js` does not import `llm.js`, so
  there is no import cycle.

- [ ] **Step 7: Wire it in `main.js`**

- Add the imports:
  `import { onSettingsHistory, registerSection, wireSettings } from "./settings.js";`
  and add `renderLLMPanel` to the `./llm.js` import list.
- In `boot()`, append `wireSettings();` to the wire list, then add the line
  `registerSection("llm", "Language models", "llm-panel", () => renderLLMPanel());`.
- In the `history:` SSE handler, add `onSettingsHistory(e.entry);`.

- [ ] **Step 8: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass. The ids/import consistency tests cover the new module and the moved panel.

- [ ] **Step 9: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 10: Commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): Settings dialog from the schema; Language models folded in"
```

---

### Task 9: The header in two rows (crowding at narrow widths)

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/index.html`, `app.css`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Append the failing test**

```python
def test_header_puts_the_utterance_and_picker_on_their_own_row():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index('id="nl-row"') : html.index("</header>")]
    for element_id in ("utterance", "nl-picker", "go"):
        assert f'id="{element_id}"' in row
    main_row = html[html.index("<header>") : html.index('id="nl-row"')]
    for element_id in ("net-select", "open-wizard", "recent", "viewmode", "jobs-btn", "settings-btn"):
        assert f'id="{element_id}"' in main_row
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k header`
Expected: 1 failure (`substring not found`).

- [ ] **Step 3: Restructure the header markup**

Replace everything between `<header>` and `</header>` with:

```html
    <div class="hrow">
      <h1>Netstead Workbench</h1>
      <select id="net-select" aria-label="Active network"></select>
      <button id="open-wizard" class="mini">Open / Import…</button>
      <select id="recent" aria-label="Recent networks"></select>
      <div id="viewmode">
        <button data-mode="map" class="on">Map</button>
        <button data-mode="split">Split</button>
        <button data-mode="table">Table</button>
      </div>
      <span id="count"></span>
      <span class="spacer"></span>
      <button id="jobs-btn" class="mini ghost" aria-label="Background jobs">Jobs <span id="jobs-count" class="pcount">0</span></button>
      <button id="settings-btn" class="mini ghost">Settings…</button>
    </div>
    <div class="hrow" id="nl-row">
      <input id="utterance" placeholder='e.g. "I-40 EB between South Miami Boulevard and Airport Boulevard"' aria-label="Selection utterance" />
      <span id="nl-picker">
        <span id="nl-dot" class="dot off" aria-hidden="true"></span>
        <select id="nl-provider" aria-label="Language model provider (this session)"></select>
        <select id="nl-model" aria-label="Language model (this session)"></select>
        <button id="nl-default" class="mini ghost" title="Save this provider and model as your default" disabled>Make default</button>
        <button id="nl-manage" class="mini ghost" aria-label="Manage language models">Models…</button>
      </span>
      <button id="go">Select</button>
    </div>
```

The `settings-btn` that Task 8 inserted moves here, so make sure it appears only once.

- [ ] **Step 4: CSS**

Replace the header rule (lines 7–8):

```css
  header { grid-column:1 / -1; display:flex; gap:10px; align-items:center; padding:12px 16px;
           background:var(--panel); border-bottom:1px solid var(--edge); }
```

with:

```css
  header { grid-column:1 / -1; display:flex; flex-direction:column; gap:8px; padding:10px 16px;
           background:var(--panel); border-bottom:1px solid var(--edge); }
  .hrow { display:flex; gap:10px; align-items:center; flex-wrap:wrap; min-width:0; }
  .hrow .spacer { flex:1; }
```

Replace the trailing block:

```css
  /* The header is crowded: it wraps to a second row rather than overflow or squeeze the utterance box. */
  header { flex-wrap:wrap; row-gap:8px; }
  #utterance { flex:1 1 280px; min-width:0; }
  @media (max-width: 640px) {
    #nl-picker { flex-wrap:wrap; }
    #nl-picker select { max-width:120px; }
  }
```

with:

```css
  /* Two header rows: network controls, then the selection bar (utterance + model picker). */
  #nl-row #utterance { flex:1 1 240px; min-width:0; }
  @media (max-width: 800px) {
    header h1, #count { display:none; }
  }
  @media (max-width: 640px) {
    #nl-picker { flex-wrap:wrap; }
    #nl-picker select { max-width:110px; }
    #nl-default { display:none; } /* also in Settings → Language models */
  }
```

`trackHeaderHeight()` in `llm.js` still sets `--header-h`, which `#jobs-panel` uses. Keep it.

- [ ] **Step 5: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass.

- [ ] **Step 6: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "fix(workbench): two-row header so nothing is pushed off-screen when narrow"
```

---

### Task 10: The basemap changes without a reload

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/map.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_server.py`, `test_workbench_static.py`

- [ ] **Step 1: Append the failing tests**

To `test_workbench_server.py`:

```python
def test_config_follows_a_basemap_change_without_a_restart(client):
    before = client.get("/api/config").json()["style"]
    r = client.post("/api/actions", json={"type": "set_setting", "key": "viz.basemap", "value": "esri"})
    assert r.status_code == 200
    after = client.get("/api/config").json()["style"]
    assert after != before and after["sources"]["basemap"]["type"] == "raster"
```

To `test_workbench_static.py`:

```python
def test_basemap_swaps_in_place_on_a_viz_setting():
    assert "export function setBasemap(" in (JS_DIR / "map.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert "setBasemap(" in main and "viz" in main
```

- [ ] **Step 2: Run them**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_server.py packages/netstead/tests/test_workbench_static.py -q -k "basemap"`
Expected: the server test passes already (the route reads current settings; it is a guard). The static test fails.

- [ ] **Step 3: `map.js`**

Append:

```js
// Swap the basemap in place. The deck.gl overlay is a non-interleaved control and survives setStyle;
// label visibility belongs to the old style's layers, so it is re-applied once the new style loads.
export function setBasemap(style) {
  if (!map) return;
  map.once("style.load", () => { labelsShown = true; render(); });
  map.setStyle(style);
}
```

- [ ] **Step 4: `main.js`**

Add `setBasemap` to the `./map.js` import. Add:

```js
// A viz.* setting (from the Settings dialog, Python, or the assistant) may change the basemap: swap it in place.
async function onSettingChanged(entry) {
  const a = entry.action;
  if (!entry.ok || a.type !== "set_setting" || !/^viz(\.|$)/.test(a.key)) return;
  const cfg = await getJSON("/api/config");
  if (JSON.stringify(cfg.style) === JSON.stringify(store.get().basemap)) return;
  store.set({ basemap: cfg.style });
  setBasemap(cfg.style);
}
```

In the `history:` SSE handler, add `onSettingChanged(e.entry).catch(err => toast(err.message));`.

- [ ] **Step 5: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_server.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass.

- [ ] **Step 6: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/netstead/workbench/static/js packages/netstead/tests/test_workbench_server.py packages/netstead/tests/test_workbench_static.py
git commit -m "fix(workbench): basemap setting applies without a page reload"
```

---

### Task 11: `linking.js`: the linking rules, and the row-click regression

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/linking.js`
- Test: `packages/netstead/tests/test_workbench_js.py`

- [ ] **Step 1: Append the failing tests to `test_workbench_js.py`**

```python
def test_sources_merge_focus_and_highlights_without_duplicates(node_module):
    got = node_module(
        "linking.js", ["sourcesFor"],
        '[sourcesFor({ table: "node", id: 7 }, new Set([1, 2])), sourcesFor({ table: "link", id: 2 }, new Set([1, 2])),'
        " sourcesFor(null, new Set())]",
    )
    assert got == [{"link": [1, 2], "node": [7]}, {"link": [1, 2]}, {}]


def test_rows_request_per_scope(node_module):
    expr = """(() => {
      const base = { selection: { link_ids: [5, 6] }, highlights: new Set([9]), focus: { table: "node", id: 3 }, hops: 1 };
      return {
        all_link: rowsRequest({ ...base, scope: "all", table: "link" }),
        sel_link: rowsRequest({ ...base, scope: "selection", table: "link" }),
        sel_node: rowsRequest({ ...base, scope: "selection", table: "node" }),
        hl_lane: rowsRequest({ ...base, scope: "highlighted", table: "lane" }),
        rel_lane: rowsRequest({ ...base, scope: "related", table: "lane" }),
        empty_sel: rowsRequest({ ...base, selection: null, scope: "selection", table: "link" }),
        nothing: rowsRequest({ scope: "related", table: "lane", selection: null, highlights: new Set(), focus: null }),
      };
    })()"""
    got = node_module("linking.js", ["rowsRequest"], expr)
    tint = {"related": {"sources": {"link": [9], "node": [3]}, "hops": 1}, "related_mode": "tint"}
    assert got["all_link"] == tint
    assert got["sel_link"] == {"ids": [5, 6], **tint}
    assert got["sel_node"] == {"related": {"sources": {"link": [5, 6]}, "hops": 1}, "related_mode": "filter"}
    assert got["hl_lane"] == {"related": {"sources": {"link": [9]}, "hops": 1}, "related_mode": "filter"}
    assert got["rel_lane"] == {**tint, "related_mode": "filter"}
    assert got["empty_sel"] == {"ids": [], **tint}  # nothing selected shows no rows, not every row
    assert got["nothing"] == {"ids": []}


def test_row_marks(node_module):
    expr = """[
      rowMarks({ table: "link", id: 5, selection: { link_ids: [5] }, highlights: new Set([5]), focus: { table: "link", id: 5 }, via: null }),
      rowMarks({ table: "lane", id: 5, selection: { link_ids: [5] }, highlights: new Set([5]), focus: { table: "link", id: 5 }, via: "lane.link_id → link" }),
    ]"""
    assert node_module("linking.js", ["rowMarks"], expr) == [["sel", "hl", "focus"], ["rel"]]


def test_page_offset_and_id_coercion(node_module):
    got = node_module("linking.js", ["coerceId", "pageOffset"], '[pageOffset(250, 100), pageOffset(0, 100), coerceId("12"), coerceId("A-1"), coerceId("")]')
    assert got == [200, 0, 12, "A-1", ""]


def test_a_row_click_never_changes_the_recorded_selection():
    """Carried P1a bug: in filter-to-selection mode a row click collapsed the selection to that row."""
    import re

    from netstead.workbench.server import STATIC_DIR

    table = (STATIC_DIR / "js" / "table.js").read_text()
    body = re.search(r"function rowClick\(pkVal\) \{.*?\n\}", table, re.S)
    assert body, "table.js must define rowClick(pkVal)"
    assert "dispatch(" not in body.group(0) and "focus" in body.group(0)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_js.py -q`
Expected: the `linking.js` tests fail with `FileNotFoundError`. The `rowClick` test fails too, because table.js still
has `tableRowClick` dispatching `select`. Task 12 makes that one pass.

- [ ] **Step 3: Create `packages/netstead/netstead/workbench/static/js/linking.js`**

```js
// Map <-> table linking rules. Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).
//
// Three sets drive the views: the recorded server `selection` (link ids, from NL or "Set as selection"),
// the per-tab `highlights` (link ids, clicked or box-selected in Highlight mode), and the per-tab `focus`
// (the one record last clicked on the map or in the table: {table, id, from}). Focus and highlights are
// view state and are never recorded; only an action changes the selection.

export const TABLE_SCOPES = ["all", "selection", "highlighted", "related"];

// The related-records sources: the highlights plus the focused record, as {table: [ids]}.
export function sourcesFor(focus, highlights) {
  const out = {};
  const add = (table, id) => { const ids = (out[table] ||= []); if (!ids.includes(id)) ids.push(id); };
  for (const id of highlights) add("link", id);
  if (focus) add(focus.table, focus.id);
  return out;
}

export const hasSources = sources => Object.values(sources).some(ids => ids.length > 0);

// The POST rows body fields for a table scope, plus the related tint when anything is focused or highlighted.
export function rowsRequest({ scope, table, selection, highlights, focus, hops = 1 }) {
  const sources = sourcesFor(focus, highlights);
  const tint = hasSources(sources) ? { related: { sources, hops }, related_mode: "tint" } : {};
  if (scope === "selection" || scope === "highlighted") {
    const ids = scope === "selection" ? (selection ? [...selection.link_ids] : []) : [...highlights];
    if (table === "link") return { ids, ...tint };
    return { related: { sources: { link: ids }, hops: 1 }, related_mode: "filter" };
  }
  if (scope === "related") return hasSources(sources) ? { related: { sources, hops }, related_mode: "filter" } : { ids: [] };
  return tint;
}

// CSS classes for one grid row.
export function rowMarks({ table, id, selection, highlights, focus, via }) {
  const marks = [];
  if (table === "link" && selection && selection.link_ids.includes(id)) marks.push("sel");
  if (table === "link" && highlights.has(id)) marks.push("hl");
  if (focus && focus.table === table && focus.id === id) marks.push("focus");
  if (via) marks.push("rel");
  return marks;
}

export const pageOffset = (index, limit) => Math.floor(index / limit) * limit;

// A primary key as the grid shows it (data-pk is a string): numeric keys back to numbers.
export function coerceId(raw) {
  const n = Number(raw);
  return raw !== "" && !Number.isNaN(n) ? n : raw;
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass except `test_a_row_click_never_changes_the_recorded_selection`.

- [ ] **Step 5: Commit, with the regression test marked as expected to fail until Task 12**

Mark the regression test `@pytest.mark.xfail(strict=True, reason="fixed in Task 12 (rowClick sets focus)")`. Task 12
removes the mark, and `strict` makes a forgotten mark fail.

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

```bash
git add packages/netstead/netstead/workbench/static/js/linking.js packages/netstead/tests/test_workbench_js.py
git commit -m "feat(workbench): linking rules (focus, scopes, related tint) as a pure module"
```

---

### Task 12: Two-way linking: focus, node picking, row scroll, scopes, box-select

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/store.js`, `table.js` (rewrite), `map.js`, `side.js`, `main.js`
- Modify: `packages/netstead/netstead/workbench/static/index.html`, `app.css`
- Test: `packages/netstead/tests/test_workbench_js.py` (remove the xfail), `test_workbench_static.py`

- [ ] **Step 1: Static test for the new controls**

Append to `test_workbench_static.py`:

```python
def test_table_bar_has_a_scope_menu_not_the_old_checkbox():
    html = (STATIC_DIR / "index.html").read_text()
    assert 'id="tbl-scope"' in html and 'id="tbl-hint"' in html and 'id="tbl-tosel"' not in html
    for scope in ("all", "selection", "highlighted", "related"):
        assert f'<option value="{scope}"' in html
```

Remove the `xfail` mark from `test_a_row_click_never_changes_the_recorded_selection`.

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: 2 failures.

- [ ] **Step 2: `store.js`**

Replace the `highlightMode: false, …` line in the initial store with:

```js
  highlightMode: false, highlights: new Set(), marker: null,
  focus: null,                // {table, id, from: "map" | "table"}: the one record last clicked (per tab, never recorded)
  related: null,              // last /related answer for focus + highlights (related.js)
  tableScope: "all",          // all | selection | highlighted | related
  relHops: 1,                 // related-records depth ("Expand a hop" toggles 1 <-> 2)
```

- [ ] **Step 3: Table bar markup and row styles**

In `index.html`, replace:

```html
        <div id="tbl-bar"><span class="tname" id="tbl-name">—</span><span id="tbl-total"></span>
          <label class="sw" style="margin-left:auto"><input type="checkbox" id="tbl-tosel"><span></span></label>
          <span>Filter to map selection</span></div>
```

with:

```html
        <div id="tbl-bar"><span class="tname" id="tbl-name">—</span><span id="tbl-total"></span>
          <span id="tbl-hint" class="empty"></span>
          <label for="tbl-scope" style="margin-left:auto">Show</label>
          <select id="tbl-scope" aria-label="Which rows to show">
            <option value="all">All rows</option>
            <option value="selection">Selection</option>
            <option value="highlighted">Highlighted</option>
            <option value="related">Related</option>
          </select>
          <button class="mini ghost" id="tbl-hops" title="Follow keys one more step">Expand a hop</button></div>
```

Replace `<div class="label">Link details</div>` with `<div class="label">Details</div>`, and replace
`<div id="details"><span class="empty">Click a link on the map.</span></div>` with
`<div id="details"><span class="empty">Click a link or node on the map, or a row in the table.</span></div>`.

Append to `app.css`:

```css
  /* ---- linked rows ---- */
  #tbl-grid tr.hl td { background:rgba(45,210,230,.16); }
  #tbl-grid tr.rel td { background:rgba(45,210,230,.07); }
  #tbl-grid tr.focus td { box-shadow:inset 0 1px 0 var(--accent), inset 0 -1px 0 var(--accent); }
  #tbl-grid a.fk { color:var(--hl); text-decoration:none; border-bottom:1px dotted var(--hl); }
  .tbl-item .rel-badge { font-size:10.5px; color:var(--hl); margin-left:6px; }
  #tbl-hint { margin-left:10px; font-size:12px; }
```

(`tr.sel` keeps its existing rule.)

- [ ] **Step 4: Rewrite `packages/netstead/netstead/workbench/static/js/table.js`**

```js
// Data-table view: a server-paged/sorted/filtered grid linked both ways to the map. A row click focuses
// the record (never a recorded selection: see linking.js); the scope menu filters to the selection, the
// highlights, or the records related to them; related rows are tinted; FK cells jump to their target.
import { getJSON, netPath, postJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { coerceId, pageOffset, rowMarks, rowsRequest } from "./linking.js";
import { resizeSoon } from "./map.js";
import { activeSelection, store } from "./store.js";

const TBL = { loaded: false, name: null, schema: null, offset: 0, limit: 100, sort: null, dir: "asc", filters: {},
  total: 0, seq: 0 };
const VIEW_KEY = "netstead.viewmode";
let filterTimer = null, located = null;

const activeId = () => { const s = store.get().server; return s && s.active; };
const fail = e => toast(e.message);
const tablePath = rest => netPath(activeId(), `table/${encodeURIComponent(TBL.name)}/${rest}`);

export const tableVisible = () => $("stage").dataset.mode !== "map";
export const tableShowing = name => tableVisible() && TBL.name === name;

export function setViewMode(mode) {
  $("stage").dataset.mode = mode;
  for (const b of document.querySelectorAll("#viewmode button")) b.classList.toggle("on", b.dataset.mode === mode);
  try { localStorage.setItem(VIEW_KEY, mode); } catch (e) { /* storage unavailable: mode just isn't remembered */ }
  if (mode !== "map" && !TBL.loaded) loadTables().catch(fail);
  resizeSoon();
}

export function onNetworkChanged() {
  Object.assign(TBL, { loaded: false, name: null, schema: null });
  $("tbl-rail").innerHTML = ""; $("tbl-grid").innerHTML = ""; $("tbl-name").textContent = "—";
  if (tableVisible()) loadTables().catch(fail);
}

// Anything a page depends on changed (selection, highlights, focus, scope, hops, related): reload from the first page
// when the set of rows may differ, else in place.
export function refreshRows({ restart = false } = {}) {
  if (!TBL.schema) return;
  if (restart) TBL.offset = 0;
  loadRows().catch(fail);
}

async function loadTables() {
  const id = activeId();
  if (!id) return;
  TBL.loaded = true;
  const j = await getJSON(netPath(id, "tables"));
  const rail = $("tbl-rail");
  rail.innerHTML = "";
  for (const t of j.tables) {
    const el = document.createElement("div");
    el.className = "tbl-item"; el.dataset.name = t.name;
    el.innerHTML = `<span>${esc(t.name)}</span><span class="rc">${t.rows.toLocaleString()}</span><span class="rel-badge"></span>`;
    el.onclick = () => selectTable(t.name).catch(fail);
    rail.appendChild(el);
  }
  if (j.tables.length) await selectTable(TBL.name || j.tables[0].name);
}

export async function selectTable(name) {
  Object.assign(TBL, { name, offset: 0, sort: null, dir: "asc", filters: {} });
  for (const el of document.querySelectorAll(".tbl-item")) el.classList.toggle("on", el.dataset.name === name);
  TBL.schema = await getJSON(netPath(activeId(), `table/${encodeURIComponent(name)}/schema`));
  $("tbl-name").textContent = name;
  buildGridHeader();
  await loadRows();
}

const gridColumns = () => TBL.schema.columns.filter(c => c.kind !== "geom");

function buildGridHeader() {
  const arrow = c => (TBL.sort === c ? `<span class="ar">${TBL.dir === "asc" ? "▲" : "▼"}</span>` : "");
  const th = gridColumns().map(c => `<th><span class="cn" data-col="${esc(c.name)}">${esc(c.name)}${arrow(c.name)}</span>` +
    `<input data-fcol="${esc(c.name)}" placeholder="filter" value="${esc(TBL.filters[c.name] || "")}"></th>`).join("");
  $("tbl-grid").innerHTML = `<thead><tr>${th}</tr></thead><tbody></tbody>`;
  for (const el of document.querySelectorAll("#tbl-grid .cn")) el.onclick = () => toggleSort(el.dataset.col);
  for (const el of document.querySelectorAll("#tbl-grid input[data-fcol]")) el.oninput = () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      const v = el.value.trim();
      if (v) TBL.filters[el.dataset.fcol] = v; else delete TBL.filters[el.dataset.fcol];
      refreshRows({ restart: true });
    }, 250);
  };
}

function toggleSort(col) {
  if (TBL.sort === col) TBL.dir = TBL.dir === "asc" ? "desc" : "asc"; else { TBL.sort = col; TBL.dir = "asc"; }
  buildGridHeader(); refreshRows({ restart: true });
}

function query() {
  const s = store.get();
  const filter = Object.entries(TBL.filters).map(([col, val]) => ({ col, op: "contains", val }));
  return { offset: TBL.offset, limit: TBL.limit, sort: TBL.sort, dir: TBL.dir, filter: filter.length ? filter : null,
    ...rowsRequest({ scope: s.tableScope, table: TBL.name, selection: activeSelection(s), highlights: s.highlights,
      focus: s.focus, hops: s.relHops }) };
}

function scopeHint(total) {
  const s = store.get();
  if (total) return "";
  if (s.tableScope === "selection") return "Nothing selected.";
  if (s.tableScope === "highlighted") return "Nothing highlighted: turn on Highlight links, then click or shift-drag.";
  if (s.tableScope === "related") return "Nothing related: click a link or node, or highlight links.";
  return "";
}

async function loadRows() {
  const seq = ++TBL.seq;
  const j = await postJSON(tablePath("rows"), query());
  if (seq !== TBL.seq) return; // superseded by a newer request
  TBL.total = j.total;
  renderRows(j.columns, j.rows, j.related || []);
  const to = Math.min(TBL.offset + TBL.limit, j.total);
  $("tbl-total").textContent = `· ${j.total.toLocaleString()} row(s)`;
  $("tbl-range").textContent = j.total ? `${TBL.offset + 1}–${to} of ${j.total.toLocaleString()}` : "0";
  $("tbl-hint").textContent = scopeHint(j.total);
  $("tbl-prev").disabled = TBL.offset <= 0;
  $("tbl-next").disabled = to >= j.total;
  await revealFocus();
}

function cellHTML(value, fk) {
  if (value === null) return '<span class="empty">·</span>';
  if (fk && fk.navigable) {
    return `<a class="fk" href="#" data-ref="${esc(fk.ref_table)}" data-id="${esc(value)}" ` +
      `title="Go to ${esc(fk.ref_table)} ${esc(value)}">${esc(value)}</a>`;
  }
  return esc(value);
}

function renderRows(cols, rows, vias) {
  const s = store.get(), pk = TBL.schema.primary_key, pkIdx = cols.indexOf(pk);
  const fks = new Map((TBL.schema.foreign_keys || []).map(f => [f.column, f]));
  const selection = activeSelection(s);
  const body = $("tbl-grid").tBodies[0];
  body.innerHTML = rows.map((r, i) => {
    const pv = pkIdx >= 0 ? r[pkIdx] : null;
    const marks = rowMarks({ table: TBL.name, id: pv, selection, highlights: s.highlights, focus: s.focus, via: vias[i] });
    const title = vias[i] ? ` title="related via ${esc(vias[i])}"` : "";
    const tds = r.map((v, c) => `<td>${cellHTML(v, fks.get(cols[c]))}</td>`).join("");
    return `<tr class="data ${marks.join(" ")}" data-pk="${pv == null ? "" : esc(pv)}"${title}>${tds}</tr>`;
  }).join("");
  for (const tr of body.querySelectorAll("tr.data")) tr.onclick = () => rowClick(tr.dataset.pk);
  for (const a of body.querySelectorAll("a.fk")) a.onclick = e => {
    e.preventDefault(); e.stopPropagation();
    jumpTo(a.dataset.ref, coerceId(a.dataset.id)).catch(fail);
  };
}

// A row click focuses that record. It never dispatches an action, so the recorded selection (and a
// "Selection" scope built on it) is never collapsed to one row.
function rowClick(pkVal) {
  if (pkVal === "" || !TBL.schema || !TBL.schema.primary_key) return;
  store.set({ focus: { table: TBL.name, id: coerceId(pkVal), from: "table" } });
}

// FK navigation: open the referenced table and focus the referenced row (its map feature flies into view).
export async function jumpTo(table, id) {
  if (!document.querySelector(`.tbl-item[data-name="${CSS.escape(table)}"]`)) { toast(`${table} is not in this network`); return; }
  if (TBL.name !== table) await selectTable(table);
  store.set({ focus: { table, id, from: "table" } });
}

const rowFor = id => [...$("tbl-grid").tBodies[0].querySelectorAll("tr.data")].find(tr => coerceId(tr.dataset.pk) === id);

export function onFocusChanged() { located = null; revealFocus().catch(fail); }

// Bring the focused row into view: mark it on this page, or locate its page once and load it.
async function revealFocus() {
  const f = store.get().focus;
  for (const tr of $("tbl-grid").querySelectorAll("tr.focus")) tr.classList.remove("focus");
  if (!f || !tableShowing(f.table) || !TBL.schema) return;
  const tr = rowFor(f.id);
  if (tr) { tr.classList.add("focus"); tr.scrollIntoView({ block: "nearest" }); return; }
  const key = `${f.table}:${f.id}`;
  if (located === key) return; // already moved to its page once: it is filtered out of this view
  located = key;
  const { index } = await postJSON(tablePath("locate"), { ...query(), id: f.id });
  if (index == null) { $("tbl-hint").textContent = `${f.table} ${f.id} is not in this view.`; return; }
  TBL.offset = pageOffset(index, TBL.limit);
  await loadRows();
}

export function wireTable() {
  for (const b of document.querySelectorAll("#viewmode button")) b.onclick = () => setViewMode(b.dataset.mode);
  $("tbl-prev").onclick = () => { if (TBL.offset > 0) { TBL.offset = Math.max(0, TBL.offset - TBL.limit); refreshRows(); } };
  $("tbl-next").onclick = () => { if (TBL.offset + TBL.limit < TBL.total) { TBL.offset += TBL.limit; refreshRows(); } };
  $("tbl-scope").onchange = e => store.set({ tableScope: e.target.value });
  $("tbl-hops").onclick = () => store.set({ relHops: store.get().relHops === 1 ? 2 : 1 });
}

export function syncScopeControls(s) {
  $("tbl-scope").value = s.tableScope;
  $("tbl-hops").textContent = s.relHops === 1 ? "Expand a hop" : "One hop";
  $("tbl-hops").classList.toggle("on", s.relHops > 1);
}

export function restoreViewMode() {
  try { const m = localStorage.getItem(VIEW_KEY); if (m) setViewMode(m); } catch (e) { /* storage unavailable */ }
}
```

`jumpTo` is used inside this module and also exported, for the Issues panel in P2. `onSelectionChanged` is gone:
`main.js` now calls `refreshRows`.

- [ ] **Step 5: `map.js`: node picking, focus outline, box-select hook**

- `let map = null, overlay = null, onLinkClick = () => {};` → `let map = null, overlay = null, handlers = { onLinkClick() {}, onNodeClick() {}, onBoxSelect() {} };`
- In `initMap`, replace `onLinkClick = handlers.onLinkClick;` with `handlers = { ...handlers, ...hooks };`. Rename the
  parameter `handlers` → `hooks` in the signature (`export function initMap(style, hooks)`), and use
  `map.on("load", hooks.onReady);`.
- In `baseLayers`, the link layer's `onClick` becomes
  `onClick: info => { if (info && info.index >= 0) handlers.onLinkClick(store.get().attrs.link_id[info.index]); }`.
- In `baseLayers`, move the nodes block **after** the links block, so nodes draw above links (Decision 8). Make it
  pickable:

```js
  if (style.show.nodes) layers.push(new deck.ScatterplotLayer({ id: "nodes",
    data: { length: net.N.count, attributes: { getPosition: { value: net.nodePositions, size: 2 } } },
    getRadius: 1.8, radiusUnits: "pixels", radiusMinPixels: 2,
    getFillColor: [...style.colors.nodes, 150], pickable: true, autoHighlight: true, highlightColor: [255, 140, 59, 235],
    onClick: info => { if (info && info.index >= 0) handlers.onNodeClick(net.nodeIds[info.index]); } }));
```

  Leave the arrow `IconLayer` after the links, as it is now.
- Add the focus outline. In `render()`, after the highlights layer line, add:

```js
  if (s.focus && s.focus.table === "link") { const l = idPathLayer(s.net, "focus", [s.focus.id], FOCUS_COLOR, 4); if (l) layers.push(l); }
```

  and add `const FOCUS_COLOR = [255, 255, 255, 235];` next to `HIGHLIGHT_COLOR`.
- Replace `flyToNode` with a version that can mark without flying:

```js
export function flyToNode(nodeId, { fly = true } = {}) {
  const net = store.get().net, i = net && net.nodeId2idx.get(nodeId);
  if (i == null) return;
  const lon = net.nodePositions[i * 2], lat = net.nodePositions[i * 2 + 1];
  store.set({ marker: { lon, lat } });
  if (fly) map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 15), duration: 500 });
}
```

- In `wireBoxSelect`'s `finish`, after `store.set({ highlights });`, add `handlers.onBoxSelect();`.

- [ ] **Step 6: `side.js`: generic details**

Replace `showLinkDetails` with:

```js
export async function showDetails(table, id) {
  const el = $("details"), head = `<div class="lid">${esc(table)} ${esc(id)}</div>`;
  el.innerHTML = `${head}<span class="empty">loading…</span>`;
  try {
    const j = await getJSON(netPath(store.get().server.active, `feature/${encodeURIComponent(table)}/${encodeURIComponent(id)}`));
    const rows = Object.entries(j.attributes).filter(([k, v]) => k !== j.pk && v !== null && v !== "")
      .map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join("");
    el.innerHTML = `${head}<table>${rows}</table>`;
  } catch (e) {
    el.innerHTML = `${head}<span class="empty">error: ${esc(e.message)}</span>`;
  }
}
```

and change `DETAILS_HINT` to `'<span class="empty">Click a link or node on the map, or a row in the table.</span>'`.
`HINT` (the status line) becomes `'<span class="empty">Click a feature or row for details; type an utterance to select.</span>'`.

- [ ] **Step 7: `main.js`: wire focus, scopes and box-select**

- Imports: replace `showLinkDetails` with `showDetails` in the `./side.js` import. Replace the `./table.js` import
  with `import { onFocusChanged, onNetworkChanged, refreshRows, restoreViewMode, syncScopeControls, tableVisible, wireTable } from "./table.js";`.
  Add `flyToNode` to the `./map.js` import.
- Replace `onLinkClick` with:

```js
function onLinkClick(linkId) {
  const s = store.get();
  if (!s.highlightMode) { store.set({ focus: { table: "link", id: linkId, from: "map" } }); return; }
  const highlights = new Set(s.highlights);
  if (highlights.has(linkId)) highlights.delete(linkId); else highlights.add(linkId);
  store.set({ highlights });
}

function onNodeClick(nodeId) {
  if (!store.get().highlightMode) store.set({ focus: { table: "node", id: nodeId, from: "map" } });
}

// A box-select fills the highlights; with the table visible, it also becomes the table's filter.
function onBoxSelect() { if (tableVisible()) store.set({ tableScope: "highlighted" }); }

// The focused record: details, its map feature (fly only when the click came from the table), its row.
function onFocus(s) {
  const f = s.focus;
  store.set({ marker: null });
  if (f) {
    showDetails(f.table, f.id);
    if (f.table === "link" && f.from === "table") fitLinks([f.id]);
    if (f.table === "node") flyToNode(f.id, { fly: f.from === "table" });
  } else {
    clearDetails();
  }
  onFocusChanged();
}
```

- In `wireStore()`:
  - change the first subscription's keys to `["server", "net", "prop", "highlights", "marker", "focus", "related"]`;
  - in the `["server"]` subscription, replace `onSelectionChanged();` with `refreshRows();`;
  - replace the `["netKey"]` subscription with
    `store.subscribe(["netKey"], () => { onNetworkChanged(); store.set({ highlights: new Set(), focus: null, related: null }); clearDetails(); });`;
  - add:

```js
  store.subscribe(["focus"], s => onFocus(s));
  store.subscribe(["highlights", "focus"], () => refreshRows());
  store.subscribe(["tableScope", "relHops"], s => { syncScopeControls(s); refreshRows({ restart: true }); });
```

- In the `initMap(cfg.style, {…})` call, add `onNodeClick, onBoxSelect,` next to `onLinkClick,`.

Selection changes from the server repaint with `refreshRows()`, which keeps the page. A scope or hop change restarts
at page 1.

- [ ] **Step 8: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass. That includes the un-xfailed row-click regression, and the import/id consistency tests catch any
stale `showLinkDetails`, `onSelectionChanged` or `tbl-tosel`.

- [ ] **Step 9: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 10: Commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py
git commit -m "feat(workbench): two-way map/table linking by focus; scope menu; row click keeps the selection"
```

---

### Task 13: FK navigation in the grid

Task 12 already renders navigable FK cells (`cellHTML`) and implements `jumpTo`. This task pins the behaviour and
covers the edge cases.

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/table.js` (only if a check fails)
- Test: `packages/netstead/tests/test_workbench_related_routes.py`, `test_workbench_static.py`

- [ ] **Step 1: Tests**

Append to `test_workbench_related_routes.py`:

```python
def test_every_navigable_fk_targets_a_located_table(client):
    """A navigable FK cell must be able to land on its row: the target's key is its located primary key."""
    for name in [t["name"] for t in client.get(f"{BASE}/tables").json()["tables"]]:
        for fk in client.get(f"{BASE}/table/{name}/schema").json()["foreign_keys"]:
            target = client.get(f"{BASE}/table/{fk['ref_table']}/schema").json()
            assert fk["navigable"] is (fk["ref_column"] == target["primary_key"])


def test_jump_from_a_link_to_its_from_node_lands_on_that_row(client, link1):
    node = int(link1.from_node_id)
    j = client.post(f"{BASE}/table/node/locate", json={"id": node}).json()
    page = client.post(f"{BASE}/table/node/rows", json={"offset": (j["index"] // 100) * 100, "limit": 100}).json()
    assert node in [r[page["columns"].index("node_id")] for r in page["rows"]]
```

Append to `test_workbench_static.py`:

```python
def test_fk_cells_link_to_their_target():
    table = (JS_DIR / "table.js").read_text()
    assert 'class="fk"' in table and "jumpTo(" in table and "stopPropagation" in table  # an FK click is not a row click
```

- [ ] **Step 2: Run them**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_related_routes.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass. If `test_every_navigable_fk_targets_a_located_table` fails, the schema route's `navigable` and
the target's `primary_key` disagree. Fix the route (both must come from `graph(h).pks`), not the test.

- [ ] **Step 3: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 4: Commit**

```bash
git add packages/netstead/tests/test_workbench_related_routes.py packages/netstead/tests/test_workbench_static.py packages/netstead/netstead/workbench/static/js/table.js
git commit -m "test(workbench): FK navigation lands on the referenced row"
```

---

### Task 14: Related-records highlighting: map tint, rail badges, row tint, hops

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/related.js`
- Modify: `packages/netstead/netstead/workbench/static/js/map.js`, `main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Failing static test**

```python
def test_related_module_feeds_the_map_and_the_rail():
    related = (JS_DIR / "related.js").read_text()
    assert '"related"' in related and "rel-badge" in related
    assert "relatedLayers(" in (JS_DIR / "map.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./related.js"' in main and "scheduleRelated" in main
```

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k related`
Expected: 1 failure (`FileNotFoundError: related.js`).

- [ ] **Step 2: Create `packages/netstead/netstead/workbench/static/js/related.js`**

```js
// Related records: what is a foreign key away from the focus and the highlights. A read-only view: the
// summary comes from POST /related and nothing is recorded. The map tints the link/node ids it returns;
// the table rail shows a count per related table; grid rows are tinted by the rows request itself.
import { netPath, postJSON } from "./api.js";
import { toast } from "./dom.js";
import { hasSources, sourcesFor } from "./linking.js";
import { store } from "./store.js";

let seq = 0, timer = null;

// Coalesce bursts (a box-select, quick clicks) into one request.
export function scheduleRelated() {
  clearTimeout(timer);
  timer = setTimeout(() => refreshRelated().catch(e => toast(e.message)), 150);
}

async function refreshRelated() {
  const s = store.get(), mine = ++seq;
  const sources = sourcesFor(s.focus, s.highlights);
  if (!s.server || !s.server.active || !hasSources(sources)) { store.set({ related: null }); return; }
  const related = await postJSON(netPath(s.server.active, "related"), { sources, hops: s.relHops });
  if (mine === seq) store.set({ related });
}

export function renderRelatedBadges(related) {
  const byTable = new Map((related ? related.tables : []).filter(t => t.count > 0).map(t => [t.table, t]));
  for (const el of document.querySelectorAll(".tbl-item")) {
    const t = byTable.get(el.dataset.name), badge = el.querySelector(".rel-badge");
    if (!badge) continue;
    badge.textContent = t ? `${t.count.toLocaleString()} related${t.partial ? "+" : ""}` : "";
    badge.title = t ? `via ${t.via.join(", ")}${t.hop > 1 ? ` (hop ${t.hop})` : ""}` : "";
  }
}
```

- [ ] **Step 3: `map.js`: the related layers**

Next to `HIGHLIGHT_COLOR`, add `const RELATED_COLOR = [45, 210, 230, 110]; // the highlight hue, lighter`. Add:

```js
// Records a foreign key away from the focus/highlights: lighter links, and rings on nodes.
function relatedLayers(net, related) {
  const layers = [], links = related.map.link, nodes = related.map.node;
  if (links && links.ids.length) {
    const l = idPathLayer(net, "related-links", links.ids, RELATED_COLOR, 1.5);
    if (l) layers.push(l);
  }
  if (nodes && nodes.ids.length) {
    const idx = nodes.ids.map(id => net.nodeId2idx.get(id)).filter(i => i != null);
    layers.push(new deck.ScatterplotLayer({ id: "related-nodes", data: idx,
      getPosition: i => [net.nodePositions[i * 2], net.nodePositions[i * 2 + 1]], getRadius: 5, radiusUnits: "pixels",
      stroked: true, filled: false, getLineColor: RELATED_COLOR, lineWidthMinPixels: 2, parameters: { depthTest: false } }));
  }
  return layers;
}
```

In `render()`, before the highlights line, add `if (s.related) layers.push(...relatedLayers(s.net, s.related));`, so
related records draw under the highlight and focus layers.

- [ ] **Step 4: `main.js`: schedule it and feed the rail**

- Import `import { renderRelatedBadges, scheduleRelated } from "./related.js";`.
- In `wireStore()`, add:

```js
  store.subscribe(["focus", "highlights", "relHops"], () => scheduleRelated());
  store.subscribe(["related"], s => renderRelatedBadges(s.related));
```

The rail is rebuilt when a network loads. `loadTables` creates empty `.rel-badge` spans, and the next `related` update
fills them. To also fill them right after a table switch, add `renderRelatedBadges(store.get().related);` at the end of
`loadTables()` in `table.js`, with `import { renderRelatedBadges } from "./related.js";`. There is no cycle:
`related.js` does not import `table.js`.

- [ ] **Step 5: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py packages/netstead/tests/test_workbench_related_routes.py -q`
Expected: all pass.

- [ ] **Step 6: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q` and the ruff pair.

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): related records tinted on the map, counted in the rail, tinted in rows"
```

---

### Task 15: Retire the legacy app factories

The CLI already routes `netstead viz` and `netstead select-serve` to the Workbench (`cli/commands/viz.py`,
`cli/commands/select.py` → `run_workbench`), and `test_cli_workbench.py` (`test_viz_is_an_alias`,
`test_select_serve_is_an_alias`, `test_alias_passes_url_source_unmangled`) covers them. No module imports
`netstead.viz.server` or `netstead.select.webapp` except their own tests. `select/_geojson.py` is imported only by
`webapp.py` and `test_select_geojson.py`.

Each legacy route test already has a Workbench counterpart:

| `test_viz_server.py` | Workbench test |
|---|---|
| index / config / esri | `test_workbench_server.py::test_index_served`, `test_config_is_keyless_basemap`; `test_viz_styling.py` (esri) |
| `network.bin`, attrs, link detail, properties | `test_workbench_network_routes.py` |
| tables / schema / rows / sort / filter / ids / 404 / 400 | `test_workbench_network_routes.py`, `test_viz_tables.py`, and Task 5's POST tests |
| fragment from picked ids / query form / empty / unknown | `test_workbench_session.py` and `test_workbench_actions.py` (`Select(link_ids=…)` results carry `fragment`) |
| select returns link ids and anchors | `test_workbench_server.py::test_post_action_ok_returns_result_and_entry` |

**Files:**
- Delete: `packages/netstead/netstead/viz/server.py`, `packages/netstead/netstead/viz/templates/index.html`,
  `packages/netstead/netstead/select/webapp.py`, `packages/netstead/netstead/select/templates/index.html`,
  `packages/netstead/netstead/select/_geojson.py`, `packages/netstead/tests/test_viz_server.py`,
  `packages/netstead/tests/test_select_webapp.py`, `packages/netstead/tests/test_select_geojson.py`
- Modify: `packages/netstead/pyproject.toml`, `packages/netstead/netstead/viz/__init__.py`,
  `packages/netstead/netstead/cli/commands/workbench.py` (docstring only)

- [ ] **Step 1: Confirm nothing else imports them**

Run: `git grep -nE "viz\.server|viz import server|select\.webapp|select import webapp|select\._geojson|_geojson import" -- packages ':!docs/design'`
Expected: hits only in the files being deleted.

- [ ] **Step 2: Delete**

```bash
git rm packages/netstead/netstead/viz/server.py packages/netstead/netstead/viz/templates/index.html \
  packages/netstead/netstead/select/webapp.py packages/netstead/netstead/select/templates/index.html \
  packages/netstead/netstead/select/_geojson.py packages/netstead/tests/test_viz_server.py \
  packages/netstead/tests/test_select_webapp.py packages/netstead/tests/test_select_geojson.py
```

- [ ] **Step 3: Packaging and docstrings**

- In `packages/netstead/pyproject.toml`, delete the two lines `"netstead/select/templates/*.html",` and
  `"netstead/viz/templates/*.html",`. Change the comment above them from
  `# GMNS selection profile schema (netstead.select.emit) + web-app template.` to
  `# GMNS selection profile schema (netstead.select.emit).`
- `packages/netstead/netstead/viz/__init__.py`: replace the docstring with
  `"""Map and table helpers the Netstead Workbench serves: binary network buffers, styling, paged tables."""`
- `cli/commands/workbench.py`: wherever the module docstring says `viz` / `select-serve` are aliases, add
  "(the old standalone apps were removed in P1b)".

- [ ] **Step 4: Run the affected tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_cli_workbench.py packages/netstead/tests/test_viz_tables.py packages/netstead/tests/test_viz_styling.py packages/netstead/tests/test_viz_buffers.py -q`
Expected: all pass.

- [ ] **Step 5: Before commit**

Run: `uv run --all-extras pytest packages -n auto -q`, the ruff pair, and `uv run lint-imports`.
Expected: all pass. The count drops by the deleted tests (22 viz + 4 webapp + 3 geojson). `lint-imports` gives
`2 kept`.

- [ ] **Step 6: Commit**

```bash
git add -A packages/netstead
git commit -m "refactor(netstead): delete the legacy viz and select-serve app factories"
```

---

### Task 16: Docs, the full suite, lint, and the browser walk-through

**Files:**
- Modify: `packages/netstead/docs/cookbook/workbench.md`
- Modify: `docs/design/2026-10-02-netstead-workbench-design.md`

- [ ] **Step 1: Cookbook**

In `packages/netstead/docs/cookbook/workbench.md`, append to the end of the `## Settings` section, after its TOML
example:

```markdown
### The Settings dialog

**Settings…** in the header opens a form built from the same settings model. Each field shows where its
current value comes from (`default`, `user`, `project`, `env` or `session`).

- **Save to** chooses the layer a change is written to: your user file, this project's `netstead.toml`, or
  this session only. Every change is a `SetSetting` action, so it appears in the history and replays from Python.
- **Reset** removes the value from the layer it comes from. An empty field does the same.
- A value set by a `NETSTEAD_*` environment variable outranks both files. The form says so when a saved
  value would not take effect.
- `io.allowed_roots` is shown read-only: the folders the app may read and write can only be widened in a
  config file, an environment variable, or on the command line.
- **Language models** is a section of the same dialog (also opened by **Models…**).
```

After `## Language models …` and before `## netstead`, add a new section:

```markdown
## Linked map and table

- **Click** a link or node on the map to focus it: its details show, and when the table shows that table its
  row scrolls into view. **Click** a row to focus it and fly the map to it. Focus is a view: it is not recorded,
  and it never changes the selection.
- **Show** in the table bar filters rows to **All**, the **Selection**, the **Highlighted** links (a shift-drag
  box in Highlight mode switches to this), or the records **Related** to the focus and highlights.
- A key column such as `from_node_id` is a link: it opens the `node` table at that row and marks the node.
- **Related records.** Rows a foreign key away from what is focused or highlighted are tinted lighter in
  every table and on the map. Examples are the lanes of a link, a link's end nodes, and the links at a node.
  - The keys come from the GMNS spec.
  - The table list shows how many records are related in each table.
  - **Expand a hop** follows keys one more step.
```

- [ ] **Step 2: Design doc**

In `docs/design/2026-10-02-netstead-workbench-design.md`:
- In the Context table, replace the `select-serve` row's "Superseded prototype" with "Removed in P1b".
- Below the phasing table, add:
  `P1b plan: [2026-10-05-workbench-p1b-plan.md](2026-10-05-workbench-p1b-plan.md); decisions on click = focus, POST for id lists, and one-hop relations are recorded there.`

- [ ] **Step 3: The full tier, before merge**

Run: `uv run --all-extras pytest packages -n auto -q -m ""`
Expected: all pass, with `slow` and `perf` included.

Run: `uv run ruff check packages && uv run ruff format --check packages && uv run lint-imports && uv run python scripts/lint_no_sql.py`
Expected: clean. `lint-imports` gives `2 kept`.

- [ ] **Step 4: Browser walk-through**

Start `netstead app <leavenworth parquet dir>` through `preview_start`, with `.claude/launch.json` running
`uv run netstead app packages/netstead/netstead/fixtures/leavenworth/parquet --port 8851`. Then check each item:

1. **Linking.**
   - Split view, link table: click a link on the map. Its row is outlined and scrolled into view, details show
     `link <id>`, and the map does not move.
   - Click another row: the map flies to it.
   - Click a node on the map. With the node table showing, its row is focused, and the marker shows without a fly.
2. **Carried bug.**
   - Select by utterance (stub), then set Show → **Selection**.
   - Click a row: the table still lists every selected link, and the history has no new `Select`.
3. **Box-select.** Highlight mode, then shift-drag over several links. Show switches to **Highlighted**, and the link
   table lists exactly those links.
4. **FK navigation.** In the link table, click a `from_node_id` cell. The `node` table opens on that row, and the map
   marks and flies to the node.
5. **Related.**
   - Focus a link: the `lane` rail badge shows `N related`, and the map rings its two end nodes.
   - In the lane table, related rows are tinted, and hovering one shows "related via lane.link_id → link".
   - Show → **Related** lists only those lanes.
   - Focus a node, then **Expand a hop**: lanes of the node's links appear (hop 2).
6. **Settings.**
   - Settings… → Map → Basemap `esri`. The map swaps to the raster basemap **without a reload**, the source badge
     says `user`, and `~/.config/netstead/config.toml` (or `NETSTEAD_CONFIG_DIR`) has `basemap = "esri"`.
   - **Reset** goes back to `default`.
   - `Input & output → Allowed Roots` is disabled, with the reason shown.
   - App server → Port shows "applies on next launch".
   - **Language models** shows the providers table. **Models…** in the header opens the same section.
7. **Header.** At 800 px wide (`resize_window`), the header is two rows and **Models…** and **Settings…** are visible.
   At the mobile preset, nothing overflows horizontally.
8. **Network.** Nothing goes to a non-local host except the basemap tiles and the CDN.

Fix anything that fails before opening the PR. Reset the viewport (`preset: "desktop"`) afterwards.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/docs/cookbook/workbench.md docs/design/2026-10-02-netstead-workbench-design.md
git commit -m "docs(workbench): settings dialog, linked map/table and related records"
```

---

## Self-review (against the design)

**Spec coverage:**

| Spec item (design §b, §g, phasing P1b; carried follow-ups) | Where |
|---|---|
| Map click selects and scrolls to the row | Task 12 (`onLinkClick`/`onNodeClick` → focus; `revealFocus` + `/locate`, Task 4–5). "Selects" is focus per Open question 1 |
| Row click selects and flies the map | Task 12 (`rowClick` → focus; `onFocus` → `fitLinks` / `flyToNode`) |
| Box-select fills the table filter "to selection" | Task 12 (`onBoxSelect` → scope **Highlighted**; Open question 2) |
| FK navigation: `from_node_id` → node row and marker | Task 5 (`foreign_keys` in schema), Task 12 (`cellHTML`, `jumpTo`), Task 13 (tests) |
| Related records: other tables and map tinted lighter | Tasks 2–3 (`related.py`), 5 (`/related`, POST rows tint), 14 (map, rail, rows) |
| Related: FKs from the spec, not hard-coded | Task 2 (`foreign_keys(spec, …)`, test `test_foreign_keys_come_from_the_spec`) |
| Related: per-table counts, page-only ids, not recorded | Task 5 (`count` per table; `row_vias` on the page only; `test_related_is_read_only`) |
| Related: one DuckDB `IN` per table, one hop, "expand a hop" | Task 3 (`restrict` via ibis `isin`, `relate(hops=…)`), Task 14 (`#tbl-hops`) |
| Related: count badges, "Filter to related", "via <fk>" | Task 14 (badges), Task 12 (Show → Related), Task 12 (`title="related via …"`) |
| Settings form from the JSON schema | Task 7 (`settingsform.js`), Task 8 (`settings.js`) |
| Each value shows its source | Task 7 (`sourceOf`, incl. nested JSON fields), Task 8 (badge) |
| Save to user or project scope through `SetSetting` | Task 8 (`save` → `set_setting`, Save-to menu; session too, per Open question 6) |
| Refused keys read-only, with explanation | Task 6 (`refusal_reason`, `readonly`), Task 8 (disabled field + reason); Open question 7 |
| Reuse the Language-models panel | Task 8 (`#llm-panel` moved; `registerSection`; `llm.*`/`select.*` hidden via `FOLDED`) |
| Credentials set and tested in keyring | Deferred (Scope notes); LLM keys already covered by the folded panel |
| Header crowding | Task 9 |
| `to_pandas` outside the engine lock | Task 1 |
| Basemap without reload | Task 10 |
| Retire `viz/server.py` `build_app` and `select/webapp.py`; check CLI routing | Task 15 (routing verified, coverage map) |
| Transit designed in | Unchanged: routes stay `/{component}/`, `transit` → 501; `related.py` is component-agnostic |
| No new deps, no build step, ES modules | All front-end files are native modules; Python uses existing pandas/ibis/pydantic |

**Placeholder scan:**
- No "TBD" or "similar to Task N".
- New files appear in full: `related.py`, `settingsform.js`, `settings.js`, `linking.js`, `related.js`, the rewritten
  `table.js` and `network.py`.
- Edits name the exact text to replace.
- No conditional instructions remain. The lazy `Table(name=…, expr=…, engine=…)` constructor in Task 4 matches the
  existing fixture in `test_viz_tables.py`.

**Name consistency:**
- `relate(graph, sources, *, hops)`, `restrict(src, matches)`, `count(src, matches)`, `ids_of(src, matches, pk, *, limit)`
  and `row_vias(page, relation)` are used the same way in Tasks 3, 5 and 13.
- The `RowsQuery` / `RelatedQuery` / `LocateQuery` field names match what `linking.rowsRequest` builds:
  `ids`, `related.sources`, `related.hops`, `related_mode`.
- Store keys (`focus`, `related`, `tableScope`, `relHops`) are the same across `store.js`, `table.js`, `main.js` and
  `related.js`.
- Ids added to `index.html`: `settings`, `set-*`, `settings-btn`, `nl-row`, `tbl-scope`, `tbl-hint`, `tbl-hops`. Ids
  removed: `tbl-tosel`, `llm-close`. The removed ids are no longer referenced, and the static tests enforce both
  directions.

**Risks:**
- `ibis.backends.duckdb.converter.DuckDBPandasData` is a semi-private import. An ibis upgrade could move it; the
  equivalence test fails loudly if so.
- An outbound relation from a very large highlight (10 000 links) builds an `IN` list of up to 20 000 node ids. DuckDB
  handles this, but if it is slow, switch `restrict` to a semi-join against an `ibis.memtable` of the values. That is
  local to `related.py`.
- Making nodes pickable can steal clicks from links at intersections, since nodes are 2 px. If the walk-through finds
  it awkward, pick nodes only when the node table is showing.
