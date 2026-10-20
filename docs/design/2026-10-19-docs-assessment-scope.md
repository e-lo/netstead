# Documentation assessment and refresh

Status: **implemented** (the refresh in this record's PR); the recommendations below are open · Date: 2026-10-19 · Owner: Elizabeth Sall

- **Kind:** scope. An audit of both documentation sites and the READMEs against the code on `main`
  (through #227), the refresh that followed, and recommendations for three audiences.
- **Method.** Four read-only audits (netstead core pages, Workbench pages, corral pages and READMEs,
  shipped-features-to-docs coverage from the git log and these design records) that ran every runnable
  snippet against the Leavenworth fixture; then four fix passes on disjoint page sets, each verifying every
  replacement against the code. Verification after: the three docs contract suites pass (246 checks, 56
  executed Python blocks); a strict `mkdocs build` of each site reports only pre-existing noise (four griffe
  annotation warnings in `network.py` / `graph/build.py`, git-revision and rate-limit notices).

## What was wrong

The library pages were sound where they mattered most (load, validate, query). Three clusters of drift
had accumulated:

1. **Decisions that changed the code but not the docs.** The DuckDB-only engine (#195) left "switch the
   engine to pandas or polars" on nine pages and the corral README. The `[graph]` extra split left
   "igraph is in `[clean]`" on ten pages, so the documented install crashed `netstead quality` and scope
   from nodes. WKB-in-memory (#201) left `wkt.loads` in the visual tour. The `netstead.reports` →
   `netstead.map` move left deprecated imports in three recipes and the Colab notebook.
2. **Pages written ahead of, or instead of, the code.** The HTTP-server recipe documented an auth model
   (argon2 hashes, token lists, per-package engines) that never existed; the documented YAML failed at
   startup. The custom quality-rule recipe used a contract the runner rejects. Four of seven listed rules
   did not exist. The MCP recipe listed tool inputs that no tool takes. The corral architecture page listed
   CLI commands, MCP tools and files that do not exist.
3. **Fixture facts.** Table, node and finding counts on a dozen pages were from an older fixture (25
   tables, 75 nodes, 214 links, "a handful" of findings) against today's 9 tables, 121 nodes, 339 links and
   271 findings. Every screenshot is the same "pending" placeholder.

On top of that, the landing page and READMEs never mentioned the Workbench, the OSM/Overture builders or
natural-language selection, which are the largest things shipped since beta.1.

## What changed (54 files)

- **Corrected** every finding above across both sites, the three READMEs, BETA-adjacent text and the
  quickstart notebook. Each correction was verified by running the snippet or reading the symbol.
- **Added pages:** `workbench-and-other-tools.md` (GeoLibre, QGIS, ArcGIS, Wrangler, model engines),
  `roadmap.md` (shipped / next / designed), `reference/settings.md` (every `netstead.toml` key, generated
  from the pydantic model), `reference/workbench-actions.md` (the 20 core Actions, from Python).
- **Expanded:** the API reference now covers `netstead.osm`, `overture`, `select`, `map`, `config` and
  `workbench`; the build recipe covers Overture and local OSM files; the bench recipe covers
  `bench-suite`; the corral architecture page gained a Workbench section.
- **Reorganised** the netstead nav: cookbook grouped by task (Validate and fix, Build and scope, Query and
  edit, Workbench, Maps and reports, Serve and automate); MCP tools and the data model moved under
  Reference; the spec-version pages added to the nav.
- **Housekeeping:** the style guide now describes the real docs tree and the real code-block harness;
  the design index and the Workbench records mark P3b as merged (#227); the netstead changelog gained the
  missing entries for #222, #223, #224 and #225.

Code bugs found by the audit are not fixed here; they are flagged as separate tasks (the stale-reference and
numpy-id fixes have since merged as #230 and #233): numpy ids leaking from
scope into MCP/CLI JSON; `doctor` probing igraph; `clean simplify-geometry` not assembling geometry; stale
`NETSTEAD_AUTO_APPROVE` docstrings; explicit `credentials=` possibly not reaching remote table reads.

## How the docs serve each audience, and what to do next

### The curious prospective user

**Now:** the landing page, umbrella page and READMEs describe what exists (library, builders, NL
selection, Workbench); the roadmap page answers "is it ready" and "what is next"; the other-tools page
answers "do I need this if I have QGIS".

**Gaps.** Nothing shows the product. All nine screenshots are placeholders, and there is no GIF or short
video of the Workbench. The umbrella page is a router with no picture of either package.

**Recommend:**
1. Capture real screenshots of the Workbench on the Leavenworth fixture for the quickstart, visual tour and
   Workbench pages, and one 60-second screen recording for the landing page. This is the single highest
   value item for this audience and can be scripted against `netstead app` with a headless browser.
2. Add a two-minute path that needs no project: a "Try it" box on the landing page with the shortest
   verified install-and-open sequence for the Workbench (verify whether `uvx --from 'netstead[server]'
   netstead app` works before publishing it).
3. Put one sentence of "who this is not for" on the landing page (no model runs, no cartography) and link
   the other-tools page, so visitors self-select quickly.

### The entry-level user getting started

**Now:** the quickstart installs the right extras and its expected outputs are true; the visual tour runs
end to end; abbreviation tooltips and the glossary are accurate.

**Gaps.** The quickstart is library-first. A beginner's easiest success is the Workbench (no code), yet
the first Workbench mention is six pages in. The visual tour's blocks are all `doctest: skip`, so it can
drift again silently. Install failures (missing extra, zsh globbing, graph extra) are explained on three
different pages.

**Recommend:**
1. Add a second quickstart, "Quickstart (Workbench)": install, `netstead app`, open the fixture, run
   validation, fix one issue, save a copy, copy the session as Python. Put both quickstarts side by side on
   the landing page as "Explore in a browser" and "Use from Python".
2. Convert the visual tour to a notebook that CI executes (the Colab notebook already exists; make it the
   source and render it with `mkdocs-jupyter`), or remove the skip markers now that the blocks run.
3. One "Install and troubleshoot" page that owns extras, Python version, zsh quoting, `netstead doctor`
   and the "requires the [graph] extra" messages; other pages link to it instead of repeating it.

### The experienced user looking for one specific thing

**Now:** the API reference covers every public module; Settings and Workbench Actions have reference pages;
the cookbook nav is grouped by task; the MCP tool shapes are real; `llms.txt` and `api-index.json` give
agents the same surface.

**Gaps.** There is no CLI reference page (every command and flag in one place; today it is spread across
recipes and `--help`). The Workbench's HTTP API (`/api/...`) and the plugin front-end `wb` object are
documented inside long how-to pages rather than as reference. The changelog is not on the site. Search
is the only cross-cutting entry point.

**Recommend:**
1. Generate a CLI reference page at build time from the typer app (the repo already generates
   `api-index.json` at build; a `cli-index` step in the same `main.py` hook can emit a page per command).
2. Add a Workbench HTTP API reference (routes, methods, bodies, SSE events) generated from the FastAPI
   OpenAPI document, and move the `Host` and `wb` tables out of the plugin how-to into reference pages the
   how-to links to.
3. Add the changelog to the site nav (mkdocs can include `../CHANGELOG.md` via the snippets extension) and
   link each roadmap item to its design record.

### Keeping it true

The contract tests are the reason the library pages stayed accurate; the pages that drifted were the ones
they do not reach. Extend them:

1. Execute indented (accordion and tab) Python blocks after dedent, and compare printed output with the
   following `Expected:` block for CLI `--json` examples.
2. Make strict `mkdocs build` pass and gate CI on it: add the four missing annotations in `network.py` and
   `graph/build.py`, set `enable_git_follow: false` for the revision plugin, and skip the rate-limited
   link checks for github.com.
3. Add a "docs" checkbox to the PR template for any change that touches `cli/`, `config.py`, `pyproject`
   extras or `workbench/actions.py`, since those were the sources of every cluster above.
