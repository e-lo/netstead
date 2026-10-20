# Development

## Basic Setup

This is a **uv workspace** with two packages — `corral` (generic Frictionless engine) and `netstead` (GMNS toolkit on top). Install both packages with **all extras**, editable, in a single command from the repo root:

```bash
uv sync --all-packages --all-extras
```

That's what CI runs. It creates `.venv/` at the workspace root with both packages installed editable, plus every optional extra (`polars`, `pandas`, `s3`, `gcs`, `azure`, `keyring`, `mcp`, `clean`, `server`, `notebook`) so the full test suite + `--doctest-modules` sweep can collect every file.

Run things via `uv run`:

```bash
uv run netstead --help
uv run corral --help
uv run pytest packages -n auto    # fast tier; see "Running tests" below
```

> **zsh users:** `[` and `]` are glob characters on zsh (the default shell on macOS). If you want to install **just one** extra ad-hoc, quote the brackets: `uv add 'netstead[clean]'` (not `uv add netstead[clean]`, which gives `zsh: no matches found`). The workspace-level `uv sync --all-packages --all-extras` doesn't hit this — no brackets in the command.

### One package at a time (rare)

If you want to install only one of the two packages (e.g. to mimic what a downstream user sees):

```bash
uv pip install -e 'packages/corral[polars,s3]'    # quoted for zsh
uv pip install -e 'packages/netstead[clean,server]'    # transitively gets corral
```

### Older / non-uv setup (not recommended)

```bash
pip install -r dev-requirements.txt
pip install -e packages/corral
pip install -e packages/netstead
```

## General Process

1. [Create an issue](#issues)
2. [Discuss approach](#consensus)
3. [Complete contribution](#coding) in a fork or branch.
4. [Submit a pull-request](#pull-requests)
5. [Respond to reviews](#review-and-approval-process)

Periodically the `develop` branch will be merged with master and a tagged release will be made and distributed to PyPI.

By making any contribution to the projects, contributors self-certify to the [Contributor Agreement](#contributor-agreement).

## Issues

- Search existing issues to see if your issue has already been brought up.
- Create new issues to start discussion on a new topic, feature requests, and bugs; linking to any other relevant issues.
- Fill out issue template to the best of your ability.
- Indiciate urgency and if you are willing to work on it either using tags or in issue text.

*Issues which do not have a clear user story may not be addressed*

### Consensus

If issue is not super obvious/straightforward, please discuss approach with the maintainers/owner and reach a consensus.

*Contributions which do not have consensus with the owner may not be approved*

## Coding

Get assigned. If you are working on issue, please tag yourself as the assignee (or ask to be tagged if you do not have privledges).

Generally:

- Support Python 3.11+.
- DuckDB (via ibis) is the only compute engine; pandas / polars / Arrow are I/O formats at the edges. No raw SQL outside `corral.engines.ibis_engine` (`scripts/lint_no_sql.py`).
- Lint and format with `ruff` (`uv run ruff check`, `uv run ruff format`); `pre-commit` runs both.
- Use Google-style docstrings for public classes and functions
- Use logging
- All code should have an associated test
- [Documentation](#documentation) lives in each package's `docs/` folder (`packages/*/docs`) and is built with `mkdocs`
- Public API is documented from docstrings in each package's `docs/reference/api.md`
- Architecture lives in [`packages/corral/docs/architecture.md`](https://github.com/e-lo/netstead/blob/main/packages/corral/docs/architecture.md). Decisions, PRDs, designs and implementation plans go in [`docs/design/`](https://github.com/e-lo/netstead/blob/main/docs/design/README.md) — see its README for the conventions and the index of every record.
- Right now, this repo prioritizes Legibility/Simplicity >> Efficiency. That might change later.

*Contributions which do not meet these requirements may not be approved*

### First-party plugins

First-party plugins live in `plugins/<name>`, each its own workspace package that may import only `netstead.workbench.plugins`' public API. Give each one an import-linter contract (see the `project_cards` one in `pyproject.toml`), a test step with its own coverage gate in `.github/workflows/tests.yml`, and an entry in `scripts/lint_no_sql.py`. The first is [`plugins/project-cards`](https://github.com/e-lo/netstead/tree/main/plugins/project-cards).

## Running tests

Tests live in `packages/corral/tests`, `packages/netstead/tests` and `plugins/project-cards/tests` and use `pytest`. Run them through `uv run --all-extras` from the repo root.

**While iterating, run your domain. Before committing a cross-cutting change, run the default. Run the full suite before merge.**

| Tier | Command | Time* |
|---|---|---|
| Domain | `uv run --all-extras pytest <paths below>` | 2–12s |
| Default (fast) | `uv run --all-extras pytest packages -n auto` | ~28s (~50s without `-n auto`) |
| Full | `uv run --all-extras pytest packages -n auto -m ""` | ~31s |

\* Measured on an 8-core Apple-silicon laptop.

- **Default** skips tests marked `slow` (end-to-end runs, like executing every documented Python block or running the bench pipeline) and `perf` (performance-regression bounds). `pyproject.toml` sets this with `-m "not slow and not perf"` in `addopts`.
- **Full** adds `-m ""`, which overrides that filter. Use `-m slow` or `-m perf` to run only one of those groups.
- **`-n auto`** runs tests in parallel with `pytest-xdist` (a dev dependency). It's opt-in. Leave it off when you use `--pdb` or a single test. `addopts` pins `--dist=loadfile` so each test file stays on one worker.
- **Naming a slow file is not enough.** `pytest packages/netstead/tests/test_cli_bench.py` deselects everything in it unless you add `-m ""`.
- **`live_llm` tests** call real provider APIs and skip unless you opt in. See the docstring of `packages/netstead/tests/test_llm_live.py`.

### Per-domain commands

Paths are relative to the repo root. `T=packages/netstead/tests`, `D=packages/corral/tests`.

| You touched | Run |
|---|---|
| `corral/engines`, `corral/io` | `$D/engines $D/io` |
| `corral/validation`, `corral/quality` | `$D/validation $D/quality` |
| anything else in corral | `packages/corral` (~15s; ~10s with `-n auto`) |
| `netstead/workbench` | `$T/test_workbench_*.py $T/test_cli_workbench.py` |
| `netstead/llm` | `$T/test_llm_*.py $T/test_cli_llm.py $T/test_workbench_llm_routes.py $T/test_workbench_ollama_pull.py` |
| `netstead/select` | `$T/test_select_*.py` |
| `netstead/osm` | `$T/test_osm_*.py` |
| `netstead/overture` | `$T/test_overture_*.py` |
| `netstead/map`, `netstead/viz` | `$T/test_map_*.py $T/test_viz_*.py` |
| `netstead/graph`, `scope`, `semantics`, `indexes` | `$T/test_graph*.py $T/test_scope.py $T/test_semantics.py $T/test_indexes.py $T/test_network_scope_accessor.py` |
| `netstead/network.py`, `quality`, `spec`, `clean`, geometry | `$T/test_network*.py $T/test_quality.py $T/test_spec.py $T/test_geom*.py $T/test_wkt.py $T/test_clean.py` |
| `netstead/cli` | `$T/test_cli*.py` |
| docs (`*.md` with Python blocks) | `$T/test_documented_*.py -m ""` |
| `netstead/bench` | `$T/bench $T/test_bench_*.py $T/test_cli_bench*.py -m ""` |
| `plugins/project-cards` (the project cards plugin) | `plugins/project-cards/tests` |

A domain run skips the doctests in the source modules. The default run includes them.

The three docs contract suites (every documented Python block runs and its `Expected:` output is compared, every documented CLI flag exists and every documented `--json` example prints the JSON the page shows, every documented `netstead.X` / `corral.X` symbol resolves) are marked `slow`, so run them by name with `-m ""`:

```bash
uv run --all-extras pytest packages/netstead/tests/test_documented_python_contract.py packages/netstead/tests/test_documented_cli_contract.py packages/netstead/tests/test_documented_api_contract.py -m "" -q
```

### Checking NL selection accuracy after prompt changes

The tests check the parser's plumbing, not how well a real model reads requests. After you change the selection prompt, the tool schema, or the assistant guide (`netstead/llm/context`), run the eval script by hand against a local model:

```bash
uv run --all-extras python scripts/eval_nl_selection.py --provider ollama --model qwen2.5:7b --runs 3
```

It parses each utterance in `scripts/data/nl_eval_set.toml` `--runs` times and prints per-utterance and total accuracy: `correct` (every field right), `first-try` (right without a repair) and `resolves` (right, or only a street name and route number swapped, which the resolver's fallback recovers). Options you leave out follow your `llm.quality` settings. Compare against the numbers from before your change, and try `--no-guide` and `--temperature default` (Ollama's own temperature), where small models make most of their mistakes. Add an utterance to the data file when you find a phrasing that a model gets wrong.

It never runs in CI. It calls whichever provider you name, so `--provider anthropic`, `openai` or `gemini` spends real tokens; the script says so before it starts.

### CI

`.github/workflows/tests.yml` always runs the **full** suite (`-m "" -n auto`), including `slow` and `perf`, on every push and PR across the Python matrix, plus the per-package coverage gates. `bench.yml` also runs the `perf` tests on their own, serially, on PRs that touch performance-critical paths.

## Documentation

Documentation uses `mkdocs`: one small umbrella site at the repo root plus a site per package.

1. Install the documentation dependency group

```bash
uv sync --all-packages --all-extras --group docs
```

2. Serve a site locally (umbrella, or a package's own site)

```bash
uv run mkdocs serve                                  # umbrella landing page
uv run mkdocs serve -f packages/netstead/mkdocs.yml  # netstead docs
uv run mkdocs serve -f packages/corral/mkdocs.yml    # corral docs
```

General settings for documentation can be found in `mkdocs.yml`

Each package site's mkdocs-macros hooks (spec tables, live nav) are in `packages/<pkg>/main.py`

`.github/workflows/documentation.yml` builds the umbrella and both package sites and deploys them to GitHub Pages.

## Pull Requests

Use the following guidance in creating and responding to pull requests

- Submit PRs to `main` (the trunk). Branch as `<type>/<slug>`, e.g. `feat/scope-zones`.
- Keep pull requests small and focused. One issue is best.
- Link Pull Requests to Issues as appropriate.
- Complete the pull request template as best you can.
- PRs which don't pass the automatic checks should either address issues causing them to fail or comment as to why they aren't.
- Tag an available reviewer to review your PR.

*Pull Requests which do not meet these requirements may not be approved*

### Review and Approval Process

- PRs which don't pass the automatic checks should either address issues causing them to fail or comment as to why they aren't.
- Tag an available reviewer to review your PR.
- Respond to conversation and requests

*Pull Requests which do not respond to reviews may not be approved*

## Contributor Agreement

By making any contribution to the projects, contributors self-certify to the following Contributor Agreement:

By making a contribution to this project, I certify that:
>  
> a. The contribution was created in whole or in part by me and I have the right to submit it under the open source license indicated in the file; or
>  
> b. The contribution is based upon previous work that, to the best of my knowledge, is covered under an appropriate open source license and I have the right under that license to submit that work with modifications, whether created in whole or in part by me, under the same open source license (unless I am permitted to submit under a different license), as indicated in the file; or
>  
> c. The contribution was provided directly to me by some other person who certified (a), (b) or (c) and I have not modified it.
>  
> d. I understand and agree that this project and the contribution are public and that a record of the contribution (including all personal information I submit with it, including my sign-off) is maintained indefinitely and may be redistributed consistent with this project or the open source license(s) involved.
>  
Attribution: This Contributor Agreement is adapted from the node.js project available here: <https://github.com/nodejs/node/blob/main/CONTRIBUTING.md>.
