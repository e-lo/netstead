# Cards plugin (ProjectCard authoring) Implementation Plan

Status: **proposed** · Date: 2026-10-19 · Owner: Elizabeth Sall

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author ProjectCards in the netstead Workbench: select facilities, pick a change type, fill a form, preview
the change on a derived network, apply it to a draft card, and commit the card to a schema-valid YAML file. It ships
as `netstead-cards`, a third workspace package that netstead discovers as the Workbench plugin `cards`.

**Architecture:** `packages/netstead-cards` (import package `netstead_cards`) holds a GMNS-native change model
(`types.py`), a maintained GMNS ↔ Wrangler mapping file, card I/O validated with the `projectcard` package, and a
lowering layer that turns each change into core edit plans (`host.plan_*`) applied with `host.mutate`. Six recorded
Actions (`cards.add_change`, `cards.undo_change`, `cards.commit`, `cards.import`, `cards.preview`,
`cards.close_preview`) own the plugin's nouns (one draft card per network). Two read-only routes feed a dock panel and
"New change ▸ …" commands built only from the `wb` front-end slots. The package imports nothing from netstead but
`netstead.workbench.plugins` (an import-linter contract enforces it).

**Tech Stack:** Python 3.11+, pydantic 2, pyyaml, `projectcard>=0.3.3,<0.4`, FastAPI (via `netstead[server]`),
pytest (+ xdist), native ES modules tested under node, uv workspace, import-linter, ruff.

**Spec:** [Cards plugin scope](2026-10-07-cards-plugin-scope.md) (accepted 2026-10-19). Code reused and adapted from
Tasks 4, 5 and 8 of the first P2 draft (`git show eeaa01b:docs/design/2026-10-07-workbench-p2-plan.md`).

---

## Delivery: three PRs

The plan is large (18 tasks), so it lands in three reviewable PRs. Each one leaves `main` green and useful.

| PR | Tasks | What it delivers |
|---|---|---|
| **A. Package and change model** | 0–8 | Host API 1.5 (exports `ActionError` / `NotSupportedYet`), the workspace package with CI, lint contract and a plugin that loads; the mapping file, the change model, `projectcard` validation, card YAML in and out (provenance, offline edit-log import), and lowering every roadway change onto `host.plan_*` + `host.mutate`. Tested as a library. |
| **B. Actions** | 9–12 | Draft state, `cards.add_change` / `cards.undo_change` (selection, saved selection, `ignore_missing` rules), `cards.commit` / `cards.import`, `cards.preview` / `cards.close_preview`, and the assistant drafting every `cards.*` Action for approval. Usable from Python, HTTP and the generated palette forms. |
| **C. UI and docs** | 13–17 | The `/form` and `/commit-check` routes, the front end (`cardmodel.js`, `controller.js`, `main.js`), the cookbook page, the design index, changelogs, the coverage gate and a browser walk-through, plus the follow-up note on the offline report's writer. |

Branch per PR from `main` (`feat/cards-a`, `feat/cards-b`, `feat/cards-c`); B and C rebase on the previous one once it
merges. Follow CONTRIBUTING.md: run domain tests while iterating, the default suite before each commit that touches
shared config, the full suite before opening the PR.

**Dry run.** On 2026-10-19 every code block of this plan was materialized in a scratch worktree at `95c6d3d` and
run: the plugin's 124 tests (doctests included) pass, its coverage is 94.9 %, `ruff check`, `ruff format --check`,
`lint-imports` and `lint_no_sql` are clean, and the default netstead suite passes with the plugin installed (after
Task 16's documented-API change). Code blocks are the formatted, lint-clean versions. The dry run was discarded;
nothing but this plan was committed.

## Facts found while planning (they change the scope's assumptions)

Verified on 2026-10-19 against `projectcard` 0.3.3 (installed into the worktree's `.venv` through `uv sync`) and
netstead at `95c6d3d`.

1. **`projectcard.validate_card` fills defaults into the dict it is given.** With its default `parse_defaults=True`
   it adds a missing required `ignore_missing: true` before validating, so a card without the flag *passes* and is
   changed in place. `projectcard.read_card(path, validate=True)` validates that way too. The plugin validates a
   deep copy with `parse_defaults=False` (`netstead_cards.validate.card_problems`): the file must say what the
   schema requires. The error is jsonschema's best match, raised as `ProjectCardValidationError("Validation error for
   project …")` with the real `jsonschema.ValidationError` as `__cause__`; we report
   `"<json path>: <message>"`, one problem at a time. Validation takes 40–70 ms (it reloads the schema with
   `jsonref` each call): fine per Action, not per keystroke.
2. **The validator does not check property values.** `roadway_property_change.property_changes` is an `anyOf` whose
   alternatives `{"price": {...}}`, `{"lanes": {...}}` … are not JSON Schema keywords, so they match anything:
   `set: null`, `set: true` and `set: [1]` all validate. The "number or string, no empty, no list" rule (scope
   decision 4) is enforced only by the plugin's own model (`PropertyChange`). Its tests pin it.
3. **Importing `projectcard` 0.3.3 fails under this repo's pytest policy.** `projectcard/utils.py` has
   `re.sub("[\ ]+", …)`; compiling it emits `SyntaxWarning: invalid escape sequence '\ '`, and with
   `filterwarnings = ["error", …]` that becomes `SyntaxError` at import (uv does not pre-compile bytecode, so CI
   always compiles it). Task 2 adds a narrow `"ignore:invalid escape sequence:SyntaxWarning"` filter.
4. **`select_links` takes exactly one of `name` / `ref` / `osm_link_id` / `model_link_id` / `all`** (`oneOf`), and
   `modes` items must be `drive`, `walk`, `bike`, `transit` or `any`. A query selection naming both a street name and
   a route number is therefore not a valid card. `model_link_id`, `model_node_id`, `A` and `B` must be integers. The
   plugin checks every change with `projectcard` *before* applying it (`check_card_shape`), so such a selection is
   refused with the schema's own message instead of failing at commit.
5. **A deletion with `clean_nodes` (or with nodes its own links use) cannot be one `host.mutate`.**
   `host.plan_delete("node", …)` plans against the network as it is now and refuses a node a link still uses
   (verified: `node 1 is still used by link.from_node_id (link 1, 2, 3, 4); delete those links first`). The links
   must be deleted before the node plan is made. A deletion is therefore one mutation per table (links, then nodes),
   both inside one Action handler: a failure undoes both, and `cards.undo_change` undoes as many pending edits as the
   change made (the draft records `steps` per change). Property changes and additions stay one mutation each.
6. **Host API gap: plugins cannot raise a user-facing error.** `ActionError` / `NotSupportedYet` live in
   `netstead.workbench.errors`, which the plugin may not import. Any other exception from a plugin handler is
   recorded as `InternalError` (`"internal error: CardError: …"`) and logged with a traceback. Task 1 re-exports both
   from `netstead.workbench.plugins` and bumps `HOST_API` to `1.5`. (The cookbook's own editing example imports
   `ActionError` from `netstead.workbench`; Task 1 fixes it too, and adds the missing `1.4` row to its versioning
   table.)
7. **Host API gap: no lineage entry on commit.** The scope says a committed card's id "joins the network's
   lineage", but `Host` has no lineage method; only `host.mutate` appends (`"cards: <note>"`, one per change). v1
   records committed cards in plugin state (`plugins.cards.committed`) and in the file; adding the card id to the
   lineage needs a Host addition (follow-up, Task 17).
8. **Host API gap: no read-path check.** `host.writable(path)` is the only allowed-roots check; `cards.import` uses it
   to vet the path it reads. Harmless (it resolves and checks; it does not create anything), but the name is odd.
   Follow-up, Task 17.
9. **Workspace wiring.** A workspace member that depends on `netstead` needs `netstead = { workspace = true }` in the
   root `[tool.uv.sources]` (uv refuses to lock otherwise). `uv.lock` is tracked: lock with a current uv. uv 0.6.x
   rewrites the lock as `revision = 2` with `upload_time` keys, a 5,000-line diff; if `uv lock` changes lines that are
   not about `netstead-cards`, `projectcard`, `jsonref`, `tabulate` or `toml`, upgrade uv (`uv self update`) and
   lock again before committing.
10. **The assistant mechanism is generic.** A plugin's Actions become assistant tools (`cards__add_change`, …) when
    `assistant.plugins` lists `cards`; those with `mutates = True` are drafted for approval, the rest run at once
    and may not mutate. Every `cards.*` Action sets `mutates = True` (they change a network, register or close a
    preview network, or write a file). Task 12 pins the draft-then-approve path.
11. **`host.selection["projectcard"]` already holds card-form facilities.** For a pick: `picked: true` and
    `query == resolved == {"links": {"model_link_id": [...], "ignore_missing": true}}` (the intent's default). For
    `Select(utterance="Benton Street")`: `picked: false`, `query == {"links": {"name": ["Benton Street"],
    "ignore_missing": true}}`, `link_ids == [1, 5, 6, 8]`. The plugin writes a pick as ids with
    `ignore_missing: false` (scope decision 7) and a query as the query, with the ids as provenance.

## Conventions used below

- Paths are from the repo root. `C=packages/netstead-cards`. Tests: `uv run --all-extras pytest $C/tests/<file> -q`.
- Lint before every commit: `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run python scripts/lint_no_sql.py`.
- Stage files by path (`git add <paths>`); never `git add -A` / `git add .`; never bare `git stash`.
- Every commit message ends with the attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  (shown once here, implied in each Commit step).
- `NET = "leavenworth"`: the bundled fixture (`netstead.fixtures.leavenworth`). Facts the tests rely on: link 1 is
  Benton Street, node 1 → node 2, `free_speed` 40.0, `lanes` 1, `facility_type` residential, one lane row; link 2
  is West Street, node 1 → node 59, `free_speed` 40.23, `lanes` 1; node 1 is used by 8 links and has `ctrl_type`
  `stop_2_way`; Benton Street resolves to links `[1, 5, 6, 8]`; max `link_id` 339, max `node_id` 121.

## File structure

| File | Responsibility |
|---|---|
| `packages/netstead/netstead/workbench/plugins/__init__.py`, `spec.py` | (Task 1) export `ActionError`, `NotSupportedYet`; `HOST_API = "1.5"`. |
| `$C/pyproject.toml`, `README.md`, `LICENSE`, `CHANGELOG.md` | The distribution `netstead-cards` 0.1.0 and its entry point `cards = "netstead_cards:plugin"`. |
| `$C/netstead_cards/__init__.py` | Public names: `plugin`, the Action classes, the change model, `DraftCard`, `read_card_file`. |
| `$C/netstead_cards/_version.py` | `__version__`. |
| `$C/netstead_cards/errors.py` | `CardError`, `CardNotSupported` (the plugin's own; handlers map them to `ActionError` / `NotSupportedYet`). |
| `$C/netstead_cards/mappings/gmns_to_wrangler.yaml`, `mapping.py` | The maintained GMNS ↔ Wrangler names (`FieldMap`, `load_mapping`). |
| `$C/netstead_cards/types.py` | The change model: `PropertyChange`, `Selection`, the seven change types, `NetworkChange`, `incomplete`. |
| `$C/netstead_cards/validate.py` | `card_problems`: `projectcard`'s validator on a copy, `parse_defaults=False`. |
| `$C/netstead_cards/card.py` | `DraftCard` ↔ ProjectCard dict / YAML, provenance in `notes`, tolerant `read_card_file` (cards and the offline edit log). |
| `$C/netstead_cards/lower.py` | `apply_change(host, net_id, change)`: plans with `host.plan_*`, applies with `host.mutate`; `describe`, `rows_by_key`. |
| `$C/netstead_cards/state.py` | `CardsState`: one draft per network, committed cards, the open preview; `snapshot` for `/api/state`. |
| `$C/netstead_cards/flow.py` | Shared handler steps: target network, facility from a selection, card-shape check, error mapping. |
| `$C/netstead_cards/actions.py` | The six Action models. |
| `$C/netstead_cards/handlers.py` | `Handlers`: the Action handlers and their `ActionSpec`s. |
| `$C/netstead_cards/routes.py` | `GET /form`, `GET /commit-check`. |
| `$C/netstead_cards/plugin.py` | `plugin()`: the entry point factory. |
| `$C/netstead_cards/static/cardmodel.js` | Pure front-end rules (change from form values, badge). |
| `$C/netstead_cards/static/controller.js` | The panel's flow over `wb` (no DOM). |
| `$C/netstead_cards/static/main.js` | `activate(wb)`: the panel's DOM, the commands, subscriptions. |
| `$C/tests/conftest.py` | `isolated_env`, `make_session`, `session`, `host`, `run_js`. |
| `$C/tests/test_*.py` | One file per task (named in each task). |
| Root `pyproject.toml`, `uv.lock`, `.github/workflows/tests.yml`, `scripts/lint_no_sql.py` | Workspace source, import contract, pytest / coverage paths, warning filter, CI step. |
| `packages/netstead/docs/cookbook/workbench-cards.md` (+ index, `mkdocs.yml`), `CONTRIBUTING.md`, `docs/design/README.md`, changelogs | Docs (Task 16). |

---
## PR A: package and change model

### Task 0: Branch and baseline

- [ ] **Step 1: Branch from an up-to-date `main`**

```bash
git switch main && git pull --ff-only && git switch -c feat/cards-a
```

- [ ] **Step 2: Sync and run the baseline**

```bash
uv sync --all-packages --all-extras
uv run --all-extras pytest packages -n auto -q
```

Expected: all pass. Note the counts in the PR description, so a later failure is clearly new.

---

### Task 1: Host API 1.5: plugins can raise `ActionError` and `NotSupportedYet`

A plugin refusal must reach the user as the refusal ("node 7 is not in the network"), recorded as a failed Action,
not as `internal error: CardError: …` with a traceback in the log.

**Files:**
- Modify: `packages/netstead/netstead/workbench/plugins/__init__.py`
- Modify: `packages/netstead/netstead/workbench/plugins/spec.py:20-25`
- Modify: `packages/netstead/tests/test_workbench_plugins.py:1105-1110`, `packages/netstead/tests/test_workbench_plugin_routes.py:219-220`
- Modify: `packages/netstead/docs/cookbook/workbench-plugins.md`, `packages/netstead/CHANGELOG.md`
- Test: `packages/netstead/tests/test_workbench_plugins.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/netstead/tests/test_workbench_plugins.py`:

```python
# ---------------------------------------------------------------------------- Host API 1.5: user-facing refusals


class Refuse(BaseAction):
    type: Literal["hello.refuse"] = "hello.refuse"
    why: str = "now"


def test_a_plugin_refusal_is_a_user_facing_error_not_an_internal_one(make_session):
    from netstead.workbench.plugins import ActionError, NotSupportedYet

    def refuse(host, action: Refuse) -> None:
        raise (NotSupportedYet if action.why == "later" else ActionError)("no lanes on a footway")

    session = make_session(make_hello(actions=(ActionSpec(Refuse, refuse),)))
    entry = session.dispatch_recorded(Refuse())
    assert not entry.ok and entry.error == "no lanes on a footway" and entry.error_type == "ActionError"
    assert session.dispatch_recorded(Refuse(why="later")).error_type == "NotSupportedYet"
```

Change the two version pins:
- in `test_a_plugin_built_for_api_1_0_installs_on_this_host`: the docstring's "on 1.4" becomes "on 1.5" and
  `assert HOST_API == "1.4"` becomes `assert HOST_API == "1.5"`;
- in `packages/netstead/tests/test_workbench_plugin_routes.py`, `test_plugins_report_host_api_1_2`:
  `== "1.4"` becomes `== "1.5"`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_plugins.py packages/netstead/tests/test_workbench_plugin_routes.py -q`
Expected: FAIL: `ImportError: cannot import name 'ActionError' from 'netstead.workbench.plugins'`, and the two
`"1.5"` pins.

- [ ] **Step 3: Implement**

Replace `packages/netstead/netstead/workbench/plugins/__init__.py` with:

```python
"""Workbench plugins: separately installed packages that add Actions, routes, settings, state and UI.

A plugin package exposes a zero-argument factory returning a :class:`WorkbenchPlugin` under the
``netstead.workbench.plugins`` entry-point group (the entry-point name must equal the plugin id).
Plugins import only what this package exports. See the cookbook page "Write a Workbench plugin".
``EditPlan`` / ``EditRefused`` come from core's edit planner (``host.plan_*``). A handler raises ``ActionError``
(or ``NotSupportedYet``) to refuse with a message the user sees; any other exception is an internal error.
"""

from ..actions import BaseAction
from ..editing import EditPlan, EditRefused
from ..errors import ActionError, NotSupportedYet
from .discovery import ENTRY_POINT_GROUP, PluginStatus, discover
from .host import Host
from .spec import HOST_API, ActionSpec, WorkbenchPlugin

__all__ = [
    "ENTRY_POINT_GROUP",
    "HOST_API",
    "ActionError",
    "ActionSpec",
    "BaseAction",
    "EditPlan",
    "EditRefused",
    "Host",
    "NotSupportedYet",
    "PluginStatus",
    "WorkbenchPlugin",
    "discover",
]
```

In `packages/netstead/netstead/workbench/plugins/spec.py`, replace the `HOST_API` comment block and value with:

```python
#: The plugin API this netstead provides: ``major.minor``. A minor bump only adds; a major bump breaks.
#: Provisional until netstead v1.0 (it may change without a major bump before then).
#: 1.1: the browser host object ``wb`` (workspaces, dock panels, commands, map layers, schema forms).
#: 1.2: Host.undo, Host.edit_warnings and Host.plan_update/plan_delete/plan_add; every mutation is a pending edit.
#: 1.3: Host.saved_selections and Host.saved_selection (named selections, P3).
#: 1.4: BaseAction.assistant (keep an Action out of the NL assistant).
#: 1.5: ActionError and NotSupportedYet exported, so a plugin handler can refuse with a user-facing message.
HOST_API = "1.5"
```

In `packages/netstead/docs/cookbook/workbench-plugins.md`:
- in "Actions", after the sentence ending "…the recorded history `entry`.", add:
  `Raise netstead.workbench.plugins.ActionError("…") to refuse: the message is shown to the user and the Action is recorded as failed (NotSupportedYet for "not in this version"). Any other exception is recorded as an internal error, and its message is redacted.`
- in "Editing networks (API 1.2)", change the example's imports to
  `from netstead.workbench.plugins import ActionError, EditRefused` (one line, replacing both import lines);
- in the `wb` reference, `("1.3")` becomes `("1.5")`;
- in the Versioning table, add two rows after `1.3`:

```markdown
| `1.4` | `BaseAction.assistant = False` keeps an Action out of the natural-language assistant's tools. |
| `1.5` | `ActionError` and `NotSupportedYet` are exported from `netstead.workbench.plugins`: a handler raises them to refuse with a message the user sees. |
```

In `packages/netstead/CHANGELOG.md`, under `## [Unreleased]` → `### Added`, add as the first bullet:

```markdown
- **Plugin Host API 1.5:** `ActionError` and `NotSupportedYet` are exported from
  `netstead.workbench.plugins`, so a plugin handler can refuse with a message the
  user sees (anything else it raises is still an internal error).
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_*.py packages/netstead/tests/test_cli_workbench.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run lint-imports
git add packages/netstead/netstead/workbench/plugins/__init__.py packages/netstead/netstead/workbench/plugins/spec.py \
  packages/netstead/tests/test_workbench_plugins.py packages/netstead/tests/test_workbench_plugin_routes.py \
  packages/netstead/docs/cookbook/workbench-plugins.md packages/netstead/CHANGELOG.md
git commit -m "feat(plugins): Host API 1.5 exports ActionError and NotSupportedYet

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `netstead-cards` workspace package, CI and the import contract

**Files:**
- Create: `packages/netstead-cards/pyproject.toml`, `README.md`, `CHANGELOG.md`, `LICENSE` (copy of the root one)
- Create: `packages/netstead-cards/netstead_cards/__init__.py`, `_version.py`, `plugin.py`
- Create: `packages/netstead-cards/tests/conftest.py`, `test_plugin_loads.py` (no `tests/__init__.py`: see Step 2)
- Modify: `pyproject.toml` (root), `uv.lock`, `.github/workflows/tests.yml`, `scripts/lint_no_sql.py`

- [ ] **Step 1: Write the package metadata**

```bash
mkdir -p packages/netstead-cards/netstead_cards packages/netstead-cards/tests
cp LICENSE packages/netstead-cards/LICENSE
```

Create `packages/netstead-cards/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "netstead-cards"
version = "0.1.0"
description = "ProjectCard authoring in the netstead Workbench: select, change, preview, and commit network_wrangler project cards."
readme = "README.md"
license = "Apache-2.0"
# A copy of the workspace-root LICENSE (hatchling skips license files outside the package directory).
license-files = ["LICENSE"]
requires-python = ">=3.11"
authors = [{ name = "Elizabeth Sall" }]
keywords = ["gmns", "projectcard", "network_wrangler", "transportation", "workbench"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
dependencies = [
    # The Workbench and its plugin API (Host API 1.5). [server] brings FastAPI for the plugin's routes.
    "netstead[server]>=1.0.0b2,<2",
    # Cards are validated with the projectcard package's own schema and validator. 0.3.x: its schema is the one
    # this plugin's change types and tests pin.
    "projectcard>=0.3.3,<0.4",
    "pyyaml>=6",
    "pydantic>=2",
]

[project.entry-points."netstead.workbench.plugins"]
cards = "netstead_cards:plugin"

[tool.hatch.build.targets.wheel]
packages = ["netstead_cards"]
```

Create `packages/netstead-cards/README.md`:

```markdown
# netstead-cards

ProjectCard authoring for the [netstead](../netstead) Workbench, as the Workbench plugin `cards`.

Select links in `netstead app`, right-click **New change ▸ Property change** (or Deletion, Addition), fill the form,
preview the change on a copy of the network, add it to the draft card, and commit the card as a
[ProjectCard](https://github.com/network-wrangler/projectcard) YAML file that `projectcard` validates.

    uv sync --all-packages        # in the netstead workspace: installs it next to netstead
    uv run netstead app

See the cookbook page "Author ProjectCards in the Workbench" in the netstead docs.
```

Create `packages/netstead-cards/CHANGELOG.md`:

```markdown
# Changelog — netstead-cards

All notable changes to the `netstead-cards` package (the Workbench plugin `cards`). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

## [Unreleased]
```

- [ ] **Step 2: Write the failing test**

Create `packages/netstead-cards/tests/conftest.py`. Do **not** add a `tests/__init__.py`: netstead's tests are the
package `tests` already, and a second `tests.conftest` makes pytest abort ("Plugin already registered under a
different name") when both packages run in one session, as `pytest packages` does.

```python
"""Fixtures: a Workbench session with the cards plugin installed on the Leavenworth fixture, and a node runner."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import OpenNetwork
from netstead_cards import plugin

SRC = str(leavenworth.parquet_dir())
NET = "leavenworth"


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """User config under ``tmp_path``; ``io.allowed_roots`` is ``tmp_path`` plus the read-only fixture."""
    roots = [str(tmp_path.resolve()), str(Path(SRC).resolve().parent)]
    return {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps(roots)}


@pytest.fixture
def make_session(tmp_path: Path, isolated_env: dict[str, str]) -> Callable[..., Session]:
    """``make_session(*extra_plugins, provider=None, overrides=None, open_network=True)``: cards always installed."""

    def build(
        *extra: Any, provider: Any = None, overrides: dict[str, Any] | None = None, open_network: bool = True
    ) -> Session:
        session = Session(
            project_dir=tmp_path,
            environ=isolated_env,
            parser=StubParser(),
            plugins=[plugin(), *extra],
            assistant_provider=provider,
            overrides=overrides or {},
        )
        if open_network:
            session.dispatch(OpenNetwork(source=SRC))
        return session

    return build


@pytest.fixture
def session(make_session: Callable[..., Session]) -> Session:
    return make_session()


@pytest.fixture
def host(session: Session) -> Any:
    """The cards plugin's Host in ``session`` (what its handlers receive)."""
    return session._hosts["cards"]


@pytest.fixture
def run_js(tmp_path: Path) -> Callable[[dict[str, Path], str], Any]:
    """``run_js({"a.js": path, ...}, harness)``: copy the files, run ``harness`` as an ES module, return its JSON.

    The harness prints one JSON value with ``console.log(JSON.stringify(...))``. Launched with an absolute path and
    ``close_fds=False`` so :mod:`subprocess` uses ``posix_spawn`` (forking a busy xdist worker can crash on macOS).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    counter = iter(range(1_000_000))

    def run(files: dict[str, Path], harness: str) -> Any:
        root = tmp_path / f"js{next(counter)}"
        root.mkdir()
        for name, source in files.items():
            shutil.copy(source, root / name)
        (root / "harness.mjs").write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(root / "harness.mjs")], stdin=subprocess.DEVNULL, capture_output=True, text=True, close_fds=False
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run
```

Create `packages/netstead-cards/tests/test_plugin_loads.py`:

```python
"""The package installs as the Workbench plugin ``cards`` and loads on this netstead."""

from importlib.metadata import entry_points

import netstead_cards
from netstead.workbench.plugins import ENTRY_POINT_GROUP, HOST_API
from netstead.workbench.plugins.spec import api_compatible
from netstead_cards import plugin


def test_the_entry_point_names_the_factory():
    (ep,) = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == "cards"]
    assert ep.value == "netstead_cards:plugin" and ep.load() is plugin


def test_the_plugin_needs_host_api_1_5_and_runs_on_this_one():
    built = plugin()
    assert (built.id, built.requires_api, built.version) == ("cards", "1.5", netstead_cards.__version__)
    assert api_compatible(built.requires_api, HOST_API)


def test_it_loads_in_a_session(make_session):
    session = make_session(open_network=False)
    (status,) = [s for s in session.plugin_status if s.id == "cards"]
    assert status.state == "loaded", status.error


def test_projectcard_imports_under_the_warnings_policy():
    import projectcard  # its utils.py has an invalid escape: see the SyntaxWarning filter in pyproject.toml

    assert projectcard.__version__.startswith("0.3.")
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'netstead_cards'` (the package is not installed yet).

- [ ] **Step 4: Implement the package**

Create `packages/netstead-cards/netstead_cards/_version.py`:

```python
"""The package version (kept in step with ``pyproject.toml``)."""

__version__ = "0.1.0"
```

Create `packages/netstead-cards/netstead_cards/plugin.py`:

```python
"""The cards plugin's entry point: ``cards = "netstead_cards:plugin"`` in the ``netstead.workbench.plugins`` group.

Selection → verb → form → preview → commit (the plugins design's UX principle 2). Every network change goes through
core's edit planner and ``host.mutate``, so core's history, Edits panel, live warnings and dirty badge see it. The
package imports nothing from netstead except ``netstead.workbench.plugins`` (an import-linter contract).
"""

from __future__ import annotations

from netstead.workbench.plugins import WorkbenchPlugin

from ._version import __version__

__all__ = ["REQUIRES_API", "plugin"]

#: The plugin API this plugin is written against (1.5: ActionError and NotSupportedYet).
REQUIRES_API = "1.5"


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin for one Workbench session."""
    return WorkbenchPlugin(id="cards", name="ProjectCards", version=__version__, requires_api=REQUIRES_API)
```

Create `packages/netstead-cards/netstead_cards/__init__.py`:

```python
"""ProjectCard authoring in the netstead Workbench, as the Workbench plugin ``cards``."""

from ._version import __version__
from .plugin import plugin

__all__ = ["__version__", "plugin"]
```

- [ ] **Step 5: Wire the workspace**

In the root `pyproject.toml`:

1. Top comment: replace the two lines listing the packages with

```toml
# Distributable packages live in packages/ — see:
#   packages/corral/pyproject.toml          (generic engine)
#   packages/netstead/pyproject.toml        (GMNS-specific)
#   packages/netstead-cards/pyproject.toml  (Workbench plugin: ProjectCard authoring)
```

2. `[tool.uv.sources]` becomes:

```toml
[tool.uv.sources]
dbcorral = { workspace = true }
netstead = { workspace = true }
```

3. `[tool.pyright]`: `include` gains `"packages/netstead-cards/netstead_cards"`, and add after the netstead
   execution environment:

```toml
[[tool.pyright.executionEnvironments]]
root = "packages/netstead-cards/netstead_cards"
extraPaths = ["packages/netstead-cards", "packages/netstead", "packages/corral"]
```

4. `[tool.importlinter]`: `root_packages = ["corral", "netstead", "netstead_cards"]`, and add after the last contract:

```toml
[[tool.importlinter.contracts]]
# The cards plugin is written against the public plugin API only (Host, BaseAction, ActionSpec, ...), so it keeps
# working as netstead's internals move. netstead.workbench.plugins itself imports netstead internals: indirect
# imports through it are allowed; direct imports of anything else in netstead (or corral) are not.
name = "netstead-cards uses only the public plugin API of netstead"
type = "forbidden"
source_modules = ["netstead_cards"]
forbidden_modules = ["netstead", "corral"]
ignore_imports = ["netstead_cards.** -> netstead.workbench.plugins"]
allow_indirect_imports = true
```

5. `[tool.pytest.ini_options]`: `testpaths = ["packages/corral/tests", "packages/netstead/tests", "packages/netstead-cards/tests"]`,
   and append to `filterwarnings`:

```toml
    # projectcard 0.3.3's utils.py has re.sub("[\ ]+", ...): compiling it warns, and "error" would turn that into
    # a SyntaxError at import (uv installs without bytecode, so CI always compiles it). Narrow: this message only.
    # TODO(deps): drop when projectcard ships a raw string there.
    "ignore:invalid escape sequence:SyntaxWarning",
```

6. `[tool.coverage.run]`: `source = ["packages/corral/corral", "packages/netstead/netstead", "packages/netstead-cards/netstead_cards"]`.

In `scripts/lint_no_sql.py`, add a third entry to `SCAN_ROOTS`:

```python
    (
        REPO_ROOT / "packages" / "netstead-cards" / "netstead_cards",
        set(),  # the plugin has no SQL at all: it edits through the Host API
    ),
```

and change the module docstring's list of scanned trees to name `packages/netstead-cards/netstead_cards/` too.

In `.github/workflows/tests.yml`, after the "Test netstead" step add:

```yaml
      - name: Test netstead-cards
        run: >-
          uv run pytest -m "" -n auto packages/netstead-cards/tests
          --doctest-modules packages/netstead-cards/netstead_cards
          ${{ matrix.coverage && '--cov=netstead_cards --cov-fail-under=85 --cov-report=term --cov-report=xml:coverage-cards.xml' || '' }}
```

and in "Upload coverage" set `files: coverage-corral.xml,coverage-netstead.xml,coverage-cards.xml`. (Task 17 raises
the gate to one point below the measured coverage.)

Lock and install:

```bash
uv --version            # needs a uv that writes `revision = 3` (Facts, item 9)
uv lock
git diff --stat uv.lock # only netstead-cards, projectcard, jsonref, tabulate, toml (and the members list)
uv sync --all-packages --all-extras
```

- [ ] **Step 6: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: 4 passed.

Remove the filter line from `pyproject.toml` once, run `find .venv -path '*projectcard*' -name '*.pyc' -delete` and
the last test again: it fails with `SyntaxError: invalid escape sequence '\ '`. Put the line back. (This checks the
filter is what makes the import work.)

- [ ] **Step 7: The netstead suite still discovers its plugins**

`uv sync --all-packages` installs `netstead-cards`, so netstead's `@pytest.mark.plugin_discovery` tests now also
discover `cards`. Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_plugins.py -q -m ""`
Expected: all pass (`cards` loads beside `hello` and the fake plugin).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run python scripts/lint_no_sql.py
git add packages/netstead-cards/pyproject.toml packages/netstead-cards/README.md packages/netstead-cards/CHANGELOG.md \
  packages/netstead-cards/LICENSE packages/netstead-cards/netstead_cards/__init__.py \
  packages/netstead-cards/netstead_cards/_version.py packages/netstead-cards/netstead_cards/plugin.py \
  packages/netstead-cards/tests/conftest.py \
  packages/netstead-cards/tests/test_plugin_loads.py pyproject.toml uv.lock .github/workflows/tests.yml \
  scripts/lint_no_sql.py
git commit -m "feat(cards): netstead-cards workspace package, CI step and import contract

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

`lint-imports` must report the new contract as KEPT. To see it bite, add `from netstead.workbench.session import
Session` to `plugin.py`: the contract is BROKEN (`netstead_cards.plugin -> netstead.workbench.session`). Remove it.

---
### Task 3: The maintained GMNS ↔ Wrangler mapping file

From the first P2 draft's Task 4, moved into the plugin (pyyaml is a hard dependency here, so no lazy import).

**Files:**
- Create: `packages/netstead-cards/netstead_cards/mappings/gmns_to_wrangler.yaml`
- Create: `packages/netstead-cards/netstead_cards/mapping.py`
- Test: `packages/netstead-cards/tests/test_mapping.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_mapping.py`:

```python
"""The GMNS <-> Wrangler mapping is data (mappings/gmns_to_wrangler.yaml), read once per path."""

import pytest
from netstead_cards.mapping import DEFAULT_MAPPING, load_mapping


@pytest.fixture(scope="module")
def m():
    return load_mapping()


def test_the_default_file_ships_inside_the_package():
    assert DEFAULT_MAPPING.is_file() and DEFAULT_MAPPING.parent.name == "mappings"


def test_selectors(m):
    assert m.selector("link") == ("link_id", "model_link_id")
    assert m.selector("node") == ("node_id", "model_node_id")


def test_field_names_round_trip(m):
    assert m.to_card("link", "from_node_id") == "A"
    assert m.to_card("link", "facility_type") == "roadway"
    assert m.to_card("link", "free_speed") == "free_speed"  # unlisted: keeps its GMNS name
    assert m.from_card("link", "B") == "to_node_id"
    assert m.from_card("node", "X") == "x_coord"
    assert m.from_card("link", "free_speed") == "free_speed"


def test_values_pass_through_unless_listed(m):
    assert m.card_value("roadway", "residential") == "residential"
    assert m.gmns_value("roadway", "residential") == "residential"


def test_derived_access_flags_come_from_allowed_uses(m):
    assert m.derive({"allowed_uses": "auto,truck,walk"}) == {"walk_access": 1, "bike_access": 0, "drive_access": 1}
    assert m.derive({}) == {}  # no source column: nothing invented (schema validation reports what is missing)


def test_a_custom_mapping_file_is_read(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("version: 1\nselectors: {link: {link_id: my_id}}\nfields: {}\nvalues: {}\nderived: {}\n")
    assert load_mapping(path).selector("link") == ("link_id", "my_id")


def test_an_unknown_mapping_version_is_refused(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("version: 2\n")
    with pytest.raises(ValueError, match="unsupported mapping version"):
        load_mapping(path)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_mapping.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.mapping'`.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/mappings/gmns_to_wrangler.yaml`:

```yaml
# GMNS (netstead) <-> network_wrangler / ProjectCard names. This is maintained data, not code: when Wrangler
# renames a field, change it here. A column not listed keeps its GMNS name in cards (ProjectCard allows
# any property name in property_changes).
version: 1
selectors:            # the key a card's facility uses to pick records by id
  link: {link_id: model_link_id}
  node: {node_id: model_node_id}
fields:               # property names (property changes) and record fields (roadway additions)
  link:
    link_id: model_link_id
    from_node_id: A
    to_node_id: B
    facility_type: roadway
  node:
    node_id: model_node_id
    x_coord: X
    y_coord: Y
values:               # per card field: GMNS value -> card value (an unlisted value passes through)
  roadway: {}
derived:              # link fields Wrangler requires that GMNS has no column for; written on additions only
  walk_access: {column: allowed_uses, any_of: [walk]}
  bike_access: {column: allowed_uses, any_of: [bike]}
  drive_access: {column: allowed_uses, any_of: [auto, drive, truck]}
```

Create `packages/netstead-cards/netstead_cards/mapping.py`:

```python
"""GMNS <-> network_wrangler / ProjectCard field names, from the maintained ``mappings/gmns_to_wrangler.yaml``.

Only card I/O uses this: applying a change works in GMNS names throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

__all__ = ["DEFAULT_MAPPING", "FieldMap", "load_mapping"]

#: The maintained mapping file shipped with the plugin.
DEFAULT_MAPPING = Path(str(resources.files("netstead_cards").joinpath("mappings/gmns_to_wrangler.yaml")))


@dataclass(frozen=True)
class FieldMap:
    """Name and value translations between GMNS tables and ProjectCard records."""

    selectors: dict[str, dict[str, str]]
    fields: dict[str, dict[str, str]]
    values: dict[str, dict[Any, Any]]
    derived: dict[str, dict[str, Any]]

    def selector(self, table: str) -> tuple[str, str]:
        """``(GMNS key column, card selector key)`` for ``table`` (``KeyError`` for an unmapped table)."""
        ((pk, key),) = self.selectors[table].items()
        return pk, key

    def to_card(self, table: str, column: str) -> str:
        """The card name for GMNS ``table.column`` (unchanged when not listed)."""
        return self.fields.get(table, {}).get(column, column)

    def from_card(self, table: str, name: str) -> str:
        """The GMNS column for card field ``name`` on ``table`` (unchanged when not listed)."""
        reverse = {v: k for k, v in self.fields.get(table, {}).items()}
        return reverse.get(name, name)

    def card_value(self, field: str, value: Any) -> Any:
        """``value`` as the card writes it for card ``field``."""
        return self.values.get(field, {}).get(value, value)

    def gmns_value(self, field: str, value: Any) -> Any:
        """A card ``value`` for ``field``, back as the GMNS value."""
        reverse = {v: k for k, v in self.values.get(field, {}).items()}
        return reverse.get(value, value)

    def derive(self, record: dict[str, Any]) -> dict[str, int]:
        """Derived card fields (``1``/``0``) for a GMNS link record; a rule whose column is absent yields nothing."""
        out: dict[str, int] = {}
        for name, rule in self.derived.items():
            raw = record.get(rule["column"])
            if raw is None:
                continue
            uses = {u.strip().lower() for u in str(raw).split(",")}
            out[name] = int(bool(uses & {str(v).lower() for v in rule["any_of"]}))
        return out


@lru_cache(maxsize=8)
def load_mapping(path: str | Path | None = None) -> FieldMap:
    """Read a mapping file (default: :data:`DEFAULT_MAPPING`); cached per path."""
    data = yaml.safe_load(Path(path or DEFAULT_MAPPING).read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"unsupported mapping version {data.get('version')!r} (expected 1)")
    return FieldMap(
        selectors=dict(data.get("selectors") or {}),
        fields=dict(data.get("fields") or {}),
        values={k: dict(v or {}) for k, v in (data.get("values") or {}).items()},
        derived=dict(data.get("derived") or {}),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_mapping.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/netstead-cards && uv run ruff format --check packages/netstead-cards
git add packages/netstead-cards/netstead_cards/mappings/gmns_to_wrangler.yaml \
  packages/netstead-cards/netstead_cards/mapping.py packages/netstead-cards/tests/test_mapping.py
git commit -m "feat(cards): maintained GMNS <-> Wrangler field mapping"
```

---

### Task 4: The change model

From the first P2 draft's Task 5, with four changes: values follow scope decision 4 (booleans to 1/0, empty and list
refused with the card rule's message); a pick carries `ignore_missing` (decision 7); a property change's facility and
a deletion's links may be left out for the Action to fill from a selection (`incomplete()` says what is still
missing); the change types are pinned against the installed `projectcard`.

**Files:**
- Create: `packages/netstead-cards/netstead_cards/errors.py`
- Create: `packages/netstead-cards/netstead_cards/types.py`
- Test: `packages/netstead-cards/tests/test_types.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_types.py`:

```python
"""The change model: ProjectCard change types, card-shaped values, selections."""

import json

import pytest
from netstead_cards.types import (
    CHANGE_TYPES,
    EMPTY_VALUE,
    ROADWAY_TYPES,
    TRANSIT_TYPES,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
    incomplete,
    parse_change,
)
from pydantic import ValidationError

QUERY = {"links": {"name": ["Benton Street"], "ignore_missing": True}}


def test_the_change_types_are_the_installed_projectcard_schema_s():
    from projectcard.projectcard import CHANGE_TYPES as PROJECTCARD_TYPES
    from projectcard.validate import PROJECTCARD_SCHEMA

    schema = json.loads(PROJECTCARD_SCHEMA.read_text(encoding="utf-8"))
    in_schema = set(schema["$defs"]["change"]["properties"]) - {"pycode"}
    assert set(CHANGE_TYPES) == in_schema == set(PROJECTCARD_TYPES) - {"pycode"}
    assert set(CHANGE_TYPES) == ROADWAY_TYPES | TRANSIT_TYPES and not ROADWAY_TYPES & TRANSIT_TYPES


def test_parse_picks_the_type():
    c = parse_change(
        {"type": "roadway_property_change", "facility": {"ids": [1]}, "property_changes": {"lanes": {"set": 2}}}
    )
    assert isinstance(c, RoadwayPropertyChange) and c.facility.table == "link"
    assert isinstance(parse_change({"type": "roadway_deletion", "links": [1]}), RoadwayDeletion)
    assert isinstance(parse_change({"type": "transit_property_change"}), TransitPropertyChange)


@pytest.mark.parametrize("bad", [{}, {"set": 1, "change": 1}, {"existing": 1}, {"set": None}])
def test_a_property_change_is_set_or_change(bad):
    with pytest.raises(ValidationError):
        PropertyChange(**bad)


def test_an_empty_value_is_refused_with_the_card_rule():
    with pytest.raises(ValidationError, match=EMPTY_VALUE):
        PropertyChange(set="  ")
    with pytest.raises(ValidationError, match=EMPTY_VALUE):
        PropertyChange(existing=1)


def test_a_list_value_is_refused():
    with pytest.raises(ValidationError, match="to a list"):
        PropertyChange(set=[1, 2])


def test_booleans_become_one_and_zero():
    assert PropertyChange(set=True).set == 1 and type(PropertyChange(set=True).set) is int
    assert PropertyChange(existing=False, set=1).existing == 0


def test_a_delta_is_a_number():
    assert PropertyChange(change="2").change == 2.0
    with pytest.raises(ValidationError, match="delta"):
        PropertyChange(change="fast")
    with pytest.raises(ValidationError, match="delta"):
        PropertyChange(change=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"ids": [1], "query": QUERY, "resolved": [1]},
        {"ids": []},
        {"query": QUERY},  # a query must record what it resolved to
        {"query": QUERY, "resolved": []},
        {"table": "node", "query": QUERY, "resolved": [1]},
    ],
)
def test_a_selection_is_ids_or_a_resolved_query(kwargs):
    with pytest.raises(ValidationError):
        Selection(**kwargs)


def test_target_ids_and_the_pick_default():
    assert Selection(ids=[3, 4]).target_ids() == [3, 4] and Selection(ids=[3]).ignore_missing is False
    assert Selection(query=QUERY, resolved=[7]).target_ids() == [7]


def test_incomplete_names_what_is_missing():
    assert "facility" in incomplete(RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)}))
    assert "links" in incomplete(RoadwayDeletion())
    assert incomplete(RoadwayDeletion(links=[1])) is None
    assert incomplete(TransitPropertyChange()) is None


def test_an_addition_needs_its_keys():
    with pytest.raises(ValidationError):
        RoadwayAddition()
    with pytest.raises(ValidationError, match="link_id"):
        RoadwayAddition(links=[{"from_node_id": 1, "to_node_id": 2}])
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_types.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.types'`.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/errors.py`:

```python
"""The plugin's own errors. Its Action handlers turn them into the Workbench's ``ActionError`` / ``NotSupportedYet``."""

__all__ = ["CardError", "CardNotSupported", "not_supported"]


class CardError(ValueError):
    """A change or a card cannot be applied, read or written; the message says why (nothing was changed)."""


class CardNotSupported(CardError):
    """Declared, but not supported in this version: transit changes, ``pycode``, or re-resolving a query."""


def not_supported(change_type: str) -> CardNotSupported:
    """The refusal for a declared change type this version cannot apply or write (transit, until P6)."""
    return CardNotSupported(
        f"{change_type} is not yet supported: transit changes arrive with the transit component (P6)"
    )
```

Create `packages/netstead-cards/netstead_cards/types.py`:

```python
"""The change model: one record per ProjectCard change type, discriminated on ``type``.

Records hold GMNS names (``link_id``, ``from_node_id``); :mod:`netstead_cards.card` translates to and from a card's
Wrangler names through the mapping file. The four transit types are declared so the union, the Action schema and the
assistant's tool vocabulary are stable; applying or writing one answers "not yet supported" until the transit
component lands (P6).

Values follow ProjectCard: a property's ``existing`` / ``set`` is one number or string. Booleans become ``1`` / ``0``;
an empty value or a list is refused ("a ProjectCard cannot set a property to empty"). Clearing a cell is a core edit,
never a card change. (``projectcard``'s validator does not check property values itself: this model is the check.)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

__all__ = [
    "CHANGE_TYPES",
    "EMPTY_VALUE",
    "LIST_VALUE",
    "ROADWAY_TYPES",
    "TRANSIT_TYPES",
    "Id",
    "NetworkChange",
    "PropertyChange",
    "RoadwayAddition",
    "RoadwayDeletion",
    "RoadwayPropertyChange",
    "Scalar",
    "Selection",
    "TransitPropertyChange",
    "TransitRouteAddition",
    "TransitRoutingChange",
    "TransitServiceDeletion",
    "card_scalar",
    "incomplete",
    "parse_change",
]

#: A value a ProjectCard property holds.
Scalar = int | float | str
#: A record id (GMNS ``link_id`` / ``node_id``).
Id = int | str

EMPTY_VALUE = "a ProjectCard cannot set a property to empty"
LIST_VALUE = "a ProjectCard cannot set a property to a list: give one number or string"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def card_scalar(value: Any) -> Any:
    """``value`` as a card holds it: a bool becomes 1/0; an empty string or a list raises ``ValueError``.

    >>> card_scalar(True), card_scalar("tertiary"), card_scalar(30)
    (1, 'tertiary', 30)
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list | tuple | set | dict):
        raise ValueError(LIST_VALUE)
    if isinstance(value, str) and not value.strip():
        raise ValueError(EMPTY_VALUE)
    return value


class PropertyChange(_Model):
    """One property: ``set`` it, or ``change`` it by a numeric delta; ``existing`` is checked first when given."""

    existing: Scalar | None = None
    set: Scalar | None = None
    change: float | None = None
    existing_value_conflict: Literal["error", "warn", "skip"] = "error"

    @field_validator("existing", "set", mode="before")
    @classmethod
    def _card_value(cls, value: Any) -> Any:
        return None if value is None else card_scalar(value)

    @field_validator("change", mode="before")
    @classmethod
    def _delta(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("change is a number: the delta to add")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("change is a number: the delta to add") from None

    @model_validator(mode="after")
    def _set_or_change(self) -> PropertyChange:
        if self.set is None and self.change is None:
            raise ValueError(f"{EMPTY_VALUE} (give set or change)")
        if self.set is not None and self.change is not None:
            raise ValueError("give set or change, not both")
        return self


class Selection(_Model):
    """Which records a property change applies to.

    ``ids`` picks records by GMNS key (a click-pick), written with ``ignore_missing`` (``False`` for a pick: a fix
    aimed at a specific link should fail loudly when that link is gone). ``query`` keeps a re-resolvable ProjectCard
    facility in card form (``host.selection["projectcard"]["query"]``, which carries its own ``ignore_missing``),
    and ``resolved`` the link ids it resolved to when the change was made (provenance). A query applies to its
    ``resolved`` ids; re-resolving it on another network version is ``apply_card`` (later).
    """

    table: Literal["link", "node"] = "link"
    ids: list[Id] | None = None
    query: dict[str, Any] | None = None
    resolved: list[Id] | None = None
    ignore_missing: bool = False

    @model_validator(mode="after")
    def _ids_or_query(self) -> Selection:
        if (self.ids is None) == (self.query is None):
            raise ValueError("give exactly one of ids or query")
        if self.ids is not None and not self.ids:
            raise ValueError("ids must not be empty")
        if self.query is not None:
            if self.table != "link":
                raise ValueError("a query selects links")
            if not self.resolved:
                raise ValueError("a query must record the link ids it resolved to (resolved=[...])")
        return self

    def target_ids(self) -> list[Id]:
        """The ids this selection applies to now."""
        return list(self.ids if self.ids is not None else self.resolved or [])


class RoadwayPropertyChange(_Model):
    """Change properties of selected links or nodes (ProjectCard ``roadway_property_change``).

    ``facility`` may be left out when the Action takes it from a selection (``use_selection`` / ``saved_selection``).
    """

    type: Literal["roadway_property_change"] = "roadway_property_change"
    facility: Selection | None = None
    property_changes: dict[str, PropertyChange] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)  # why (an issue code, a reason): written in the notes


class RoadwayDeletion(_Model):
    """Delete links and/or nodes by id (ProjectCard ``roadway_deletion``).

    A deleted link takes the rows that depend on it (lanes, time-of-day rows), as core plans it. ``clean_nodes`` also
    deletes the deleted links' end nodes that no remaining link uses. ``links`` may be left out when the Action takes
    them from a selection.
    """

    type: Literal["roadway_deletion"] = "roadway_deletion"
    links: list[Id] = Field(default_factory=list)
    nodes: list[Id] = Field(default_factory=list)
    clean_nodes: bool = False
    note: str | None = Field(default=None, max_length=500)


_REQUIRED = {"links": ("link_id", "from_node_id", "to_node_id"), "nodes": ("node_id", "x_coord", "y_coord")}


class RoadwayAddition(_Model):
    """Add GMNS link and/or node records (ProjectCard ``roadway_addition``); nodes are added before links."""

    type: Literal["roadway_addition"] = "roadway_addition"
    links: list[dict[str, Any]] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _records(self) -> RoadwayAddition:
        if not self.links and not self.nodes:
            raise ValueError("give links and/or nodes to add")
        for kind, required in _REQUIRED.items():
            for record in getattr(self, kind):
                missing = [k for k in required if record.get(k) is None]
                if missing:
                    raise ValueError(f"every added {kind[:-1]} needs {', '.join(missing)}")
        return self


class _Transit(_Model):
    """A transit change: declared so the union is stable; its fields arrive with the transit component (P6)."""

    body: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class TransitPropertyChange(_Transit):
    """ProjectCard ``transit_property_change`` (not yet supported)."""

    type: Literal["transit_property_change"] = "transit_property_change"


class TransitRoutingChange(_Transit):
    """ProjectCard ``transit_routing_change`` (not yet supported)."""

    type: Literal["transit_routing_change"] = "transit_routing_change"


class TransitRouteAddition(_Transit):
    """ProjectCard ``transit_route_addition`` (not yet supported)."""

    type: Literal["transit_route_addition"] = "transit_route_addition"


class TransitServiceDeletion(_Transit):
    """ProjectCard ``transit_service_deletion`` (not yet supported)."""

    type: Literal["transit_service_deletion"] = "transit_service_deletion"


NetworkChange = Annotated[
    RoadwayPropertyChange
    | RoadwayDeletion
    | RoadwayAddition
    | TransitPropertyChange
    | TransitRoutingChange
    | TransitRouteAddition
    | TransitServiceDeletion,
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[NetworkChange] = TypeAdapter(NetworkChange)

#: Every change type, as ProjectCard names them (``roadway_managed_lanes`` reuses the property change; out of scope).
CHANGE_TYPES: tuple[str, ...] = (
    "roadway_property_change",
    "roadway_deletion",
    "roadway_addition",
    "transit_property_change",
    "transit_routing_change",
    "transit_route_addition",
    "transit_service_deletion",
)
ROADWAY_TYPES = frozenset(t for t in CHANGE_TYPES if t.startswith("roadway_"))
TRANSIT_TYPES = frozenset(t for t in CHANGE_TYPES if t.startswith("transit_"))


def parse_change(data: dict[str, Any]) -> NetworkChange:
    """Validate a JSON dict into a :data:`NetworkChange` (raises ``pydantic.ValidationError``)."""
    return _ADAPTER.validate_python(data)


def incomplete(change: Any) -> str | None:
    """Why ``change`` cannot be applied or written as it stands (``None`` when it can)."""
    if isinstance(change, RoadwayPropertyChange) and change.facility is None:
        return "a roadway_property_change needs a facility (or use_selection / saved_selection)"
    if isinstance(change, RoadwayDeletion) and not change.links and not change.nodes:
        return "a roadway_deletion needs links and/or nodes (or use_selection / saved_selection)"
    return None
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_types.py -q`
Expected: all pass. If `test_the_change_types_are_the_installed_projectcard_schema_s` fails, the installed
`projectcard` renamed a type: rename the model's `type` literal and its `CHANGE_TYPES` entry to match, never loosen
the test.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/netstead-cards && uv run ruff format --check packages/netstead-cards
git add packages/netstead-cards/netstead_cards/errors.py packages/netstead-cards/netstead_cards/types.py \
  packages/netstead-cards/tests/test_types.py
git commit -m "feat(cards): the change model (roadway types; transit declared; card-shaped values)"
```

---

### Task 5: Validate cards with `projectcard`

**Files:**
- Create: `packages/netstead-cards/netstead_cards/validate.py`
- Test: `packages/netstead-cards/tests/test_validate.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_validate.py`:

```python
"""Cards are checked with projectcard's own validator, strictly (no defaults filled in) and on a copy."""

import copy

from netstead_cards.validate import card_problems

GOOD = {
    "project": "fix speeds",
    "tags": ["fixes"],
    "changes": [
        {
            "roadway_property_change": {
                "facility": {"links": {"model_link_id": [1, 2], "ignore_missing": False}},
                "property_changes": {"free_speed": {"existing": 40, "existing_value_conflict": "error", "set": 30}},
            }
        }
    ],
}


def _change(card):
    return card["changes"][0]["roadway_property_change"]


def test_a_valid_card_has_no_problems():
    assert card_problems(GOOD) == []


def test_a_missing_ignore_missing_is_a_problem_and_the_card_is_left_alone():
    bad = copy.deepcopy(GOOD)
    del _change(bad)["facility"]["links"]["ignore_missing"]
    before = copy.deepcopy(bad)
    assert card_problems(bad) == [
        "changes/0/roadway_property_change/facility/links: 'ignore_missing' is a required property"
    ]
    assert bad == before  # projectcard's parse_defaults would have filled it in, in place


def test_a_change_takes_no_extra_fields():
    bad = copy.deepcopy(GOOD)
    _change(bad)["notes"] = "per-change notes are not in the schema"
    (problem,) = card_problems(bad)
    assert problem.startswith("changes/0/roadway_property_change: Additional properties")


def test_a_name_and_a_ref_together_are_not_a_valid_selector():
    bad = copy.deepcopy(GOOD)
    _change(bad)["facility"]["links"] = {"name": ["Main Street"], "ref": ["US 2"], "ignore_missing": True}
    (problem,) = card_problems(bad)
    assert problem.startswith("changes/0/roadway_property_change/facility/links:")


def test_ids_must_be_integers():
    bad = copy.deepcopy(GOOD)
    _change(bad)["facility"]["links"]["model_link_id"] = ["1"]
    assert card_problems(bad) == [
        "changes/0/roadway_property_change/facility/links/model_link_id/0: '1' is not of type 'integer'"
    ]


def test_a_card_needs_a_project():
    assert card_problems({"changes": GOOD["changes"]}) == ["(card): 'project' is a required property"]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_validate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.validate'`.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/validate.py`:

```python
"""Check a ProjectCard dict with the ``projectcard`` package's own validator (its schema, its rules).

``projectcard.validate_card`` fills schema defaults into the dict it is given (``parse_defaults=True``), so a card
missing a required ``ignore_missing`` passes, and is changed in place. We validate a deep copy with
``parse_defaults=False``: the file must say what the schema requires. jsonschema stops at its best-matching error,
so :func:`card_problems` returns at most one problem. (The schema does not type-check property values; the change
model does, see :mod:`netstead_cards.types`.)
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from projectcard import validate_card
from projectcard.errors import ProjectCardValidationError

__all__ = ["card_problems"]


def card_problems(card: Mapping[str, Any]) -> list[str]:
    """What makes ``card`` an invalid ProjectCard, as ``["<json path>: <message>"]`` (empty when it is valid)."""
    try:
        validate_card(copy.deepcopy(dict(card)), parse_defaults=False)
    except ProjectCardValidationError as exc:
        cause = exc.__cause__
        if cause is None:  # a pycode error (the plugin never writes pycode) carries its own message
            return [str(exc)]
        where = "/".join(str(p) for p in cause.absolute_path) or "(card)"
        return [f"{where}: {cause.message}"]
    return []
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_validate.py -q`
Expected: all pass. (`projectcard` logs `---- Error validating … ----` at ERROR for each invalid card; that is its
logger, `CardLogger`, and harmless.)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/netstead-cards && uv run ruff format --check packages/netstead-cards
git add packages/netstead-cards/netstead_cards/validate.py packages/netstead-cards/tests/test_validate.py
git commit -m "feat(cards): validate cards with projectcard, strictly and on a copy"
```

---
### Task 6: `DraftCard`: ProjectCard dicts and YAML in and out

From the first P2 draft's Task 8 (`card.py`), adapted: picks write their own `ignore_missing` (false by default),
deletions always write `false` (decision 7), an incomplete change cannot be written, a pydantic error while reading
becomes a `CardError` naming the change, and the reader is named `read_card_file` (so it is not confused with
`projectcard.read_card`). The change log (`log.py`) of the first draft is not carried over: the plugin's draft state
(Task 9) replaces it, with undo through the Host.

**Files:**
- Create: `packages/netstead-cards/netstead_cards/card.py`
- Modify: `packages/netstead-cards/netstead_cards/__init__.py`
- Test: `packages/netstead-cards/tests/test_card.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_card.py`:

```python
"""Draft cards: ProjectCard dicts and YAML in and out, provenance in notes, tolerant reading."""

import pytest
import yaml
from netstead_cards.card import DraftCard, read_card_file
from netstead_cards.errors import CardError, CardNotSupported
from netstead_cards.types import (
    EMPTY_VALUE,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)
from netstead_cards.validate import card_problems

PICK = RoadwayPropertyChange(
    facility=Selection(ids=[1]),
    property_changes={
        "free_speed": PropertyChange(existing=40, set=30),
        "facility_type": PropertyChange(set="tertiary"),
    },
    note="quality.high_speed_residential: link 1",
)
QUERY = RoadwayPropertyChange(
    facility=Selection(query={"links": {"name": ["Benton Street"], "ignore_missing": True}}, resolved=[1, 5, 6, 8]),
    property_changes={"lanes": PropertyChange(change=1)},
)
ADD = RoadwayAddition(
    nodes=[{"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}],
    links=[
        {
            "link_id": 9001,
            "from_node_id": 1,
            "to_node_id": 9001,
            "name": "New Street",
            "facility_type": "residential",
            "lanes": 1,
            "allowed_uses": "auto,walk",
        }
    ],
)


def draft(*changes):
    return DraftCard(project="speed fixes", tags=["fixes"], changes=list(changes))


def test_a_pick_writes_wrangler_names_and_validates():
    card = draft(PICK).to_card()
    assert card["changes"] == [
        {
            "roadway_property_change": {
                "facility": {"links": {"model_link_id": [1], "ignore_missing": False}},
                "property_changes": {
                    "free_speed": {"existing": 40, "existing_value_conflict": "error", "set": 30},
                    "roadway": {"set": "tertiary"},
                },
            }
        }
    ]
    assert "netstead.note[0]: quality.high_speed_residential: link 1" in card["notes"]
    assert card_problems(card) == []


def test_a_query_keeps_its_query_and_records_what_it_resolved_to():
    card = draft(QUERY).to_card()
    assert card["changes"][0]["roadway_property_change"]["facility"] == QUERY.facility.query
    assert "netstead.resolved[0]: model_link_id=[1, 5, 6, 8]" in card["notes"]
    assert card_problems(card) == []
    assert DraftCard.from_card(card).changes[0].facility == QUERY.facility


def test_deletion_and_addition_cards_validate():
    card = draft(RoadwayDeletion(links=[1], clean_nodes=True), ADD).to_card()
    deletion, addition = (c.popitem()[1] for c in card["changes"])
    assert deletion == {"links": {"model_link_id": [1], "ignore_missing": False}, "clean_nodes": True}
    link = addition["links"][0]
    assert (link["A"], link["B"], link["roadway"]) == (1, 9001, "residential")
    assert (link["walk_access"], link["bike_access"], link["drive_access"]) == (1, 0, 1)
    assert addition["nodes"][0] == {"model_node_id": 9001, "X": -120.66, "Y": 47.6}
    assert card_problems(draft(RoadwayDeletion(links=[1], clean_nodes=True), ADD).to_card()) == []


def test_yaml_round_trip(tmp_path):
    original = DraftCard(
        project="speed fixes",
        tags=["fixes"],
        dependencies={"prerequisites": ["widen-main"]},
        notes="Reviewed with the city.",
        changes=[PICK, QUERY, RoadwayDeletion(links=[2]), ADD],
    )
    path = tmp_path / "card.yml"
    path.write_text(original.to_yaml(), encoding="utf-8")
    assert read_card_file(path) == original


def test_a_card_we_write_loads_with_projectcard(tmp_path):
    import projectcard

    path = tmp_path / "card.yml"
    path.write_text(draft(PICK, QUERY, RoadwayDeletion(links=[2]), ADD).to_yaml(), encoding="utf-8")
    assert projectcard.read_card(path, validate=True).project == "speed fixes"


def test_the_offline_reports_edit_log_imports(tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log  # the real writer: its format is what we read

    path = tmp_path / "edits.yaml"
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 7},
                column="lanes",
                from_value=1,
                to_value=2,
                reason="too few",
            ),
        ]
    )
    dump_edit_log(log, path)
    (change,) = read_card_file(path).changes
    assert change.facility == Selection(ids=[7]) and change.property_changes["lanes"] == PropertyChange(
        existing=1, set=2
    )
    assert "too few" in change.note and "edit_id=e1" in change.note


def test_an_edit_log_that_clears_a_cell_is_refused(tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log

    path = tmp_path / "edits.yaml"
    dump_edit_log(
        EditLog(
            edits=[
                Edit(
                    id="e1",
                    kind="fix",
                    table="link",
                    pk={"link_id": 7},
                    column="name",
                    from_value="Main",
                    to_value=None,
                )
            ]
        ),
        path,
    )
    with pytest.raises(CardError, match=f"change 1: .*{EMPTY_VALUE}"):
        read_card_file(path)


def test_an_id_card_keeps_its_ignore_missing():
    card = {
        "project": "p",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"model_link_id": [3], "ignore_missing": True}},
                    "property_changes": {"lanes": {"set": 2}},
                }
            }
        ],
    }
    assert DraftCard.from_card(card).changes[0].facility == Selection(ids=[3], ignore_missing=True)


def test_a_single_change_card_without_a_changes_list_reads():
    card = {"project": "p", "roadway_deletion": {"links": {"model_link_id": [4], "ignore_missing": False}}}
    assert DraftCard.from_card(card).changes == [RoadwayDeletion(links=[4])]


def test_a_query_without_provenance_is_not_supported_yet():
    card = {
        "project": "p",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"name": ["Main"], "ignore_missing": True}},
                    "property_changes": {"lanes": {"set": 2}},
                }
            }
        ],
    }
    with pytest.raises(CardNotSupported, match="apply_card"):
        DraftCard.from_card(card)


@pytest.mark.parametrize("kind", ["transit_property_change", "pycode", "roadway_managed_lanes"])
def test_other_change_types_are_not_supported_on_read(kind):
    with pytest.raises(CardNotSupported, match=kind):
        DraftCard.from_card({"project": "p", "changes": [{kind: {}}]})


def test_transit_and_incomplete_changes_cannot_be_written():
    with pytest.raises(CardNotSupported, match="not yet supported"):
        draft(TransitPropertyChange()).to_card()
    with pytest.raises(CardError, match="needs a facility"):
        draft(RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)})).to_card()


def test_not_a_card():
    with pytest.raises(CardError, match="project"):
        DraftCard.from_card({"changes": []})
    assert yaml.safe_load(draft(PICK).to_yaml())["project"] == "speed fixes"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_card.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.card'`.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/card.py`:

```python
"""Draft ProjectCards: group changes, and translate them to and from card dicts and YAML files.

``DraftCard.to_card()`` writes ProjectCard names through the maintained mapping (``link_id`` -> ``model_link_id``,
``from_node_id`` -> ``A``, ...). A change cannot carry extra fields in the ProjectCard schema, so netstead's
provenance travels in the card-level ``notes``, one ``netstead.<what>[<i>]: <value>`` line per item: ``resolved``
(the link ids a query selection resolved to) and ``note`` (why change ``i`` was made). :func:`read_card_file` reads
them back. It also reads the offline validation report's edit log (``netstead.map.edits.dump_edit_log``), whose
facility puts ``model_link_id`` at the top level and whose changes carry their own ``notes``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import CardError, CardNotSupported, not_supported
from .mapping import FieldMap, load_mapping
from .types import (
    CHANGE_TYPES,
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    incomplete,
)

__all__ = ["Dependencies", "DraftCard", "read_card_file"]

_PROVENANCE = re.compile(r"^netstead\.(resolved|note)\[(\d+)\]: (.*)$")
_PROP_KEYS = ("existing", "set", "change", "existing_value_conflict")


class Dependencies(BaseModel):
    """A card's ProjectCard ``dependencies``: other projects, by name."""

    model_config = ConfigDict(extra="forbid")
    prerequisites: list[str] = Field(default_factory=list)
    corequisites: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    def to_card(self) -> dict[str, list[str]]:
        """Only the non-empty lists (the schema has no required keys)."""
        return {k: list(v) for k, v in self.model_dump().items() if v}


class DraftCard(BaseModel):
    """A ProjectCard being built (or committed): project, tags, dependencies, notes and its changes."""

    model_config = ConfigDict(extra="forbid")
    project: str = ""
    tags: list[str] = Field(default_factory=list)
    dependencies: Dependencies = Field(default_factory=Dependencies)
    notes: str | None = None
    changes: list[NetworkChange] = Field(default_factory=list)

    def to_card(self, mapping: FieldMap | None = None) -> dict[str, Any]:
        """The ProjectCard dict, in Wrangler names.

        Raises:
            CardNotSupported: a transit change.
            CardError: a change that is incomplete (no facility, nothing to delete).
        """
        m = mapping or load_mapping()
        card: dict[str, Any] = {"project": self.project or "untitled netstead card"}
        if self.tags:
            card["tags"] = list(self.tags)
        if deps := self.dependencies.to_card():
            card["dependencies"] = deps
        notes = ([self.notes] if self.notes else []) + _provenance(self.changes)
        if notes:
            card["notes"] = "\n".join(notes)
        card["changes"] = [{c.type: _body(i, c, m)} for i, c in enumerate(self.changes)]
        return card

    def to_yaml(self, mapping: FieldMap | None = None) -> str:
        """The card as YAML text, in the field order ProjectCard documents use."""
        return yaml.safe_dump(self.to_card(mapping), sort_keys=False, default_flow_style=False, allow_unicode=True)

    @classmethod
    def from_card(cls, data: Mapping[str, Any], mapping: FieldMap | None = None) -> DraftCard:
        """Read a ProjectCard dict (a ``changes`` list, or one top-level change) back into GMNS names.

        Raises:
            CardError: not a ProjectCard (no ``project``), or a malformed change (named by its number).
            CardNotSupported: transit, ``pycode``, managed lanes, or a query selection without netstead provenance.
        """
        m = mapping or load_mapping()
        if not isinstance(data, Mapping) or "project" not in data:
            raise CardError("not a ProjectCard: expected a top-level 'project'")
        raw = data.get("changes")
        if raw is None:
            raw = [{k: data[k]} for k in (*CHANGE_TYPES, "roadway_managed_lanes", "pycode") if k in data]
        notes, resolved, why = _split_notes(data.get("notes"))
        changes = []
        for i, item in enumerate(raw):
            if not isinstance(item, Mapping) or len(item) != 1:
                raise CardError(f"change {i + 1}: expected exactly one change type")
            ((kind, body),) = item.items()
            try:
                changes.append(_change(kind, body or {}, m, resolved.get(i), why.get(i)))
            except ValidationError as exc:
                first = exc.errors(include_url=False, include_input=False)[0]["msg"]
                raise CardError(f"change {i + 1}: {first}") from exc
        deps = {k: list(v) for k, v in (data.get("dependencies") or {}).items()}
        return cls(
            project=str(data["project"]),
            tags=[str(t) for t in data.get("tags") or []],
            dependencies=Dependencies(**deps),
            notes=notes,
            changes=changes,
        )


def read_card_file(path: str | Path, mapping: FieldMap | None = None) -> DraftCard:
    """Read a ProjectCard YAML file (or an offline-report edit log) as a :class:`DraftCard`."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DraftCard.from_card(data or {}, mapping)


# ---------------------------------------------------------------- writing


def _provenance(changes: list[Any]) -> list[str]:
    lines = []
    for i, c in enumerate(changes):
        if isinstance(c, RoadwayPropertyChange) and c.facility is not None and c.facility.query is not None:
            lines.append(f"netstead.resolved[{i}]: model_link_id={json.dumps(c.facility.resolved)}")
        if c.note:
            lines.append(f"netstead.note[{i}]: {' '.join(c.note.split())}")
    return lines


def _ids(ids: list[Any], table: str, m: FieldMap, *, ignore_missing: bool) -> dict[str, Any]:
    _, key = m.selector(table)
    return {key: list(ids), "ignore_missing": ignore_missing}


def _facility(sel: Selection, m: FieldMap) -> dict[str, Any]:
    if sel.query is not None:
        query = copy.deepcopy(sel.query)
        query.setdefault("links", {}).setdefault("ignore_missing", True)  # required by the schema
        return query
    selector = _ids(list(sel.ids or []), sel.table, m, ignore_missing=sel.ignore_missing)
    return {"links" if sel.table == "link" else "nodes": selector}


def _prop(name: str, pc: PropertyChange, m: FieldMap) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if pc.existing is not None:
        out["existing"] = m.card_value(name, pc.existing)
        out["existing_value_conflict"] = pc.existing_value_conflict
    if pc.set is not None:
        out["set"] = m.card_value(name, pc.set)
    else:
        out["change"] = pc.change
    return out


def _record(record: dict[str, Any], table: str, m: FieldMap, *, derive: bool = False) -> dict[str, Any]:
    out = {}
    for column, value in record.items():
        if value is not None:
            name = m.to_card(table, column)
            out[name] = m.card_value(name, value)
    return {**m.derive(record), **out} if derive else out


def _body(i: int, c: Any, m: FieldMap) -> dict[str, Any]:
    if c.type in TRANSIT_TYPES:
        raise not_supported(c.type)
    if problem := incomplete(c):
        raise CardError(f"change {i + 1}: {problem}")
    if isinstance(c, RoadwayPropertyChange):
        table = c.facility.table
        props = {m.to_card(table, p): _prop(m.to_card(table, p), pc, m) for p, pc in c.property_changes.items()}
        return {"facility": _facility(c.facility, m), "property_changes": props}
    if isinstance(c, RoadwayDeletion):
        body: dict[str, Any] = {}
        if c.links:  # a deletion aims at specific records: fail loudly when one is gone
            body["links"] = _ids(c.links, "link", m, ignore_missing=False)
        if c.nodes:
            body["nodes"] = _ids(c.nodes, "node", m, ignore_missing=False)
        if c.clean_nodes:
            body["clean_nodes"] = True
        return body
    body = {}
    if c.links:
        body["links"] = [_record(r, "link", m, derive=True) for r in c.links]
    if c.nodes:
        body["nodes"] = [_record(r, "node", m) for r in c.nodes]
    return body


# ---------------------------------------------------------------- reading


def _split_notes(notes: Any) -> tuple[str | None, dict[int, list[Any]], dict[int, str]]:
    """``(the user's notes, {i: resolved ids}, {i: note})`` from a card's ``notes`` text."""
    if not isinstance(notes, str):
        return None, {}, {}
    kept, resolved, why = [], {}, {}
    for line in notes.splitlines():
        match = _PROVENANCE.match(line.strip())
        if match is None:
            kept.append(line)
        elif match[1] == "resolved":
            resolved[int(match[2])] = json.loads(match[3].split("=", 1)[1])
        else:
            why[int(match[2])] = match[3]
    text = "\n".join(kept).strip()
    return text or None, resolved, why


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def _selector_ids(selector: Any, table: str, m: FieldMap) -> list[Any]:
    if not selector:
        return []
    _, key = m.selector(table)
    if key not in selector:
        raise CardNotSupported(f"only {key} selections can be read for a deletion in this version")
    return _as_list(selector[key])


def _selection(facility: Mapping[str, Any], m: FieldMap, resolved: list[Any] | None) -> Selection:
    for table in ("link", "node"):  # the offline report's edit log: {model_link_id: [...]} at the top level
        _, key = m.selector(table)
        if key in facility:
            return Selection(table=table, ids=_as_list(facility[key]))
    if facility.get("nodes") is not None:
        nodes = facility["nodes"]
        return Selection(
            table="node", ids=_selector_ids(nodes, "node", m), ignore_missing=bool(nodes.get("ignore_missing"))
        )
    links = facility.get("links")
    if links is None:
        raise CardError("a property change needs a facility with links or nodes")
    _, key = m.selector("link")
    by_id = key in links and set(links) <= {key, "ignore_missing"} and "from" not in facility and "to" not in facility
    if by_id:
        return Selection(ids=_as_list(links[key]), ignore_missing=bool(links.get("ignore_missing")))
    if resolved is None:
        raise CardNotSupported(
            "this card selects links by a query (name, ref, ...) with no netstead provenance; "
            "re-resolving a query arrives with apply_card"
        )
    return Selection(query=dict(facility), resolved=resolved)


def _from_record(record: Mapping[str, Any], table: str, m: FieldMap) -> dict[str, Any]:
    return {m.from_card(table, k): m.gmns_value(k, v) for k, v in record.items() if k not in m.derived}


def _change(kind: str, body: Mapping[str, Any], m: FieldMap, resolved: list[Any] | None, note: str | None) -> Any:
    if kind not in CHANGE_TYPES or kind in TRANSIT_TYPES:
        raise CardNotSupported(f"{kind} changes are not supported yet")
    if note is None and isinstance(body.get("notes"), str):  # the offline report's per-change notes
        note = body["notes"]
    if kind == "roadway_property_change":
        sel = _selection(body.get("facility") or {}, m, resolved)
        props = {
            m.from_card(sel.table, name): PropertyChange(
                **{
                    k: (m.gmns_value(name, v) if k in ("existing", "set") else v)
                    for k, v in spec.items()
                    if k in _PROP_KEYS
                }
            )
            for name, spec in (body.get("property_changes") or {}).items()
        }
        return RoadwayPropertyChange(facility=sel, property_changes=props, note=note)
    if kind == "roadway_deletion":
        change = RoadwayDeletion(
            links=_selector_ids(body.get("links"), "link", m),
            nodes=_selector_ids(body.get("nodes"), "node", m),
            clean_nodes=bool(body.get("clean_nodes")),
            note=note,
        )
        if problem := incomplete(change):
            raise CardError(problem)
        return change
    return RoadwayAddition(
        links=[_from_record(r, "link", m) for r in body.get("links") or []],
        nodes=[_from_record(r, "node", m) for r in body.get("nodes") or []],
        note=note,
    )
```

Add to `packages/netstead-cards/netstead_cards/__init__.py`, which becomes:

```python
"""ProjectCard authoring in the netstead Workbench, as the Workbench plugin ``cards``."""

from ._version import __version__
from .card import Dependencies, DraftCard, read_card_file
from .errors import CardError, CardNotSupported
from .plugin import plugin
from .types import (
    CHANGE_TYPES,
    ROADWAY_TYPES,
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
    TransitRouteAddition,
    TransitRoutingChange,
    TransitServiceDeletion,
)

__all__ = [
    "CHANGE_TYPES",
    "ROADWAY_TYPES",
    "TRANSIT_TYPES",
    "CardError",
    "CardNotSupported",
    "Dependencies",
    "DraftCard",
    "NetworkChange",
    "PropertyChange",
    "RoadwayAddition",
    "RoadwayDeletion",
    "RoadwayPropertyChange",
    "Selection",
    "TransitPropertyChange",
    "TransitRouteAddition",
    "TransitRoutingChange",
    "TransitServiceDeletion",
    "__version__",
    "plugin",
    "read_card_file",
]
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_card.py -q`
Expected: all pass. If `projectcard` rejects the addition because a required Wrangler field is missing, add the field
to `ADD` **and**, if it can be derived, to the mapping's `derived` rules; do not loosen the check.

- [ ] **Step 5: Lint and commit**

Run `uv run ruff format packages/netstead-cards` first (the long `PropertyChange(**{...})` line wraps), then:

```bash
uv run ruff check packages/netstead-cards && uv run ruff format --check packages/netstead-cards
git add packages/netstead-cards/netstead_cards/card.py packages/netstead-cards/netstead_cards/__init__.py \
  packages/netstead-cards/tests/test_card.py
git commit -m "feat(cards): DraftCard to and from ProjectCard YAML (provenance, offline edit log)"
```

---
### Task 7: Lower a property change onto core's planner and apply it through the Host

**Files:**
- Create: `packages/netstead-cards/netstead_cards/lower.py`
- Test: `packages/netstead-cards/tests/test_lower_property.py`

These tests call `apply_change` with the plugin's real `Host` (the `host` fixture) outside an Action handler: each
`host.mutate` is then recorded as a non-replayable `cards.mutate` entry, which is fine for testing the lowering. Task
9 runs the same code inside Action handlers.

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_lower_property.py`:

```python
"""A property change becomes core edit plans (host.plan_update) applied in one host.mutate."""

import pytest
from netstead_cards.errors import CardError, CardNotSupported
from netstead_cards.lower import apply_change, describe
from netstead_cards.types import PropertyChange, RoadwayPropertyChange, Selection, TransitPropertyChange

NET = "leavenworth"


def cell(session, key, column, table="link"):
    h = session.network(NET)
    frame = h.links_df() if table == "link" else h.nodes_df()
    return frame.loc[frame[f"{table}_id"] == key, column].tolist()


def change(ids, table="link", **props):
    return RoadwayPropertyChange(
        facility=Selection(table=table, ids=ids), property_changes={k: PropertyChange(**v) for k, v in props.items()}
    )


def test_set_is_one_mutation_planned_by_core(session, host):
    applied = apply_change(host, NET, change([1, 2], free_speed={"set": 30}))
    assert cell(session, 1, "free_speed") == [30.0] and cell(session, 2, "free_speed") == [30.0]
    assert applied.steps == 1 and applied.rows == {"link": 2}
    last = session.edits[NET].last()
    assert last.source == "cards" and len(last.results) == 1
    assert session.network(NET).lineage[-1] == "cards: roadway_property_change link 1, 2: free_speed → 30"


def test_describe_names_the_change():
    assert describe(change([1], free_speed={"existing": 40, "set": 30})) == (
        "roadway_property_change link 1: free_speed 40 → 30"
    )
    assert describe(change([1, 2, 3, 4, 5, 6], lanes={"change": 1})) == (
        "roadway_property_change link 1, 2, 3, 4, 5 (+1 more): lanes +1"
    )


def test_an_existing_mismatch_is_a_conflict_and_changes_nothing(session, host):
    with pytest.raises(CardError, match=r"free_speed: expected 40 on link 2, found 40\.23"):
        apply_change(host, NET, change([1, 2], free_speed={"existing": 40, "set": 30}))
    assert session.network(NET).version == 0 and cell(session, 1, "free_speed") == [40.0]


def test_existing_conflict_can_warn_or_skip(session, host):
    warned = apply_change(
        host, NET, change([2], free_speed={"existing": 40, "set": 30, "existing_value_conflict": "warn"})
    )
    assert warned.warnings and cell(session, 2, "free_speed") == [30.0]
    skipped = apply_change(
        host,
        NET,
        change([1], free_speed={"existing": 99, "set": 30, "existing_value_conflict": "skip"}, lanes={"set": 2}),
    )
    assert skipped.skipped and cell(session, 1, "free_speed") == [40.0] and cell(session, 1, "lanes") == [2]


def test_all_properties_skipped_changes_nothing(session, host):
    applied = apply_change(
        host, NET, change([1], free_speed={"existing": 99, "set": 30, "existing_value_conflict": "skip"})
    )
    assert applied.steps == 0 and applied.skipped and session.network(NET).version == 0


def test_a_delta_groups_rows_by_their_current_value(session, host):
    apply_change(host, NET, change([1], lanes={"set": 2}))
    applied = apply_change(host, NET, change([1, 2], lanes={"change": 1}))
    assert cell(session, 1, "lanes") == [3] and cell(session, 2, "lanes") == [2]
    assert applied.steps == 1 and len(session.edits[NET].last().results) == 2  # one update per current value


def test_core_refuses_what_storage_cannot_hold(session, host):
    with pytest.raises(CardError, match="does not fit"):
        apply_change(host, NET, change([1], free_speed={"set": "fast"}))
    with pytest.raises(CardError, match="does not fit"):
        apply_change(host, NET, change([1], lanes={"set": 1.5}))
    with pytest.raises(CardError, match="key"):
        apply_change(host, NET, change([1], link_id={"set": 2}))
    assert session.network(NET).version == 0


@pytest.mark.parametrize(
    ("ids", "props", "message"),
    [
        ([999999], {"lanes": {"set": 2}}, "link 999999 not found"),
        ([1], {"nope": {"set": 2}}, "no column"),
        ([1], {"name": {"change": 1}}, "only numbers change by a delta"),
    ],
)
def test_bad_targets_are_refused(session, host, ids, props, message):
    with pytest.raises(CardError, match=message):
        apply_change(host, NET, change(ids, **props))


def test_a_node_property(session, host):
    apply_change(host, NET, change([1], table="node", ctrl_type={"existing": "stop_2_way", "set": "signal"}))
    assert cell(session, 1, "ctrl_type", table="node") == ["signal"]


def test_a_query_applies_to_its_resolved_ids(session, host):
    sel = Selection(query={"links": {"name": ["Benton Street"], "ignore_missing": True}}, resolved=[1, 5])
    apply_change(host, NET, RoadwayPropertyChange(facility=sel, property_changes={"lanes": PropertyChange(set=2)}))
    assert cell(session, 1, "lanes") == [2] and cell(session, 5, "lanes") == [2]


def test_an_incomplete_change_and_transit_are_refused(host):
    with pytest.raises(CardError, match="needs a facility"):
        apply_change(host, NET, RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)}))
    with pytest.raises(CardNotSupported, match="P6"):
        apply_change(host, NET, TransitPropertyChange())
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_lower_property.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.lower'`.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/lower.py`. Task 8 adds the deletion and addition appliers.

```python
"""Lower a change onto core's edit planner and apply it through the Host: ``host.plan_*``, then ``host.mutate``.

A property change or an addition is one ``host.mutate`` (one pending edit, one undo step, all or nothing). A
deletion is one per table it deletes from (links, then nodes): core plans a node deletion against the network as it
is now, so the links that used a node must be gone before that plan is made. Call :func:`apply_change` from an
Action handler: if a later step fails, the handler's failure undoes the earlier ones. Core's planner refuses what
storage cannot hold (``EditRefused``, raised here as :class:`~netstead_cards.errors.CardError`); the live checks
report the rest as warnings (``host.edit_warnings``).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from netstead.workbench.plugins import EditPlan, EditRefused, Host

from .errors import CardError, not_supported
from .types import (
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    incomplete,
)

__all__ = ["KEYS", "Applied", "apply_change", "describe", "few", "rows_by_key"]

#: The key column of each table a roadway change edits (GMNS names).
KEYS = {"link": "link_id", "node": "node_id"}


@dataclass
class Applied:
    """A change as applied: how many ``host.mutate`` calls it took (undone together), and what happened."""

    change: NetworkChange
    steps: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """JSON-safe: the type, its description, steps, rows per table, warnings and skipped properties."""
        return {
            "type": self.change.type,
            "describe": describe(self.change),
            "steps": self.steps,
            "rows": dict(self.rows),
            "warnings": list(self.warnings),
            "skipped": list(self.skipped),
        }


def apply_change(host: Host, net_id: str, change: NetworkChange) -> Applied:
    """Plan ``change`` with core and apply it to network ``net_id``; the lineage gets ``"cards: <describe>"``.

    Raises:
        CardNotSupported: a transit change.
        CardError: an incomplete change, a conflict with ``existing``, a missing record or column, or anything
            core's planner refuses.
    """
    if change.type in TRANSIT_TYPES:
        raise not_supported(change.type)
    if problem := incomplete(change):
        raise CardError(problem)
    applier = _APPLIERS[change.type]
    return applier(host, net_id, change, describe(change))


def describe(change: Any) -> str:
    """A short description of ``change``, for the lineage, the Edits panel and the draft card's list."""
    if isinstance(change, RoadwayPropertyChange):
        sel = change.facility
        target = f"{sel.table} {few(sel.target_ids())}" if sel is not None else "the selection"
        parts = [f"{name} {_what(pc)}" for name, pc in change.property_changes.items()]
        return f"roadway_property_change {target}: {', '.join(parts)}"
    if isinstance(change, RoadwayDeletion):
        bits = [f"link {few(change.links)}"] if change.links else []
        bits += [f"node {few(change.nodes)}"] if change.nodes else []
        clean = " (and unused end nodes)" if change.clean_nodes else ""
        return f"roadway_deletion {' and '.join(bits) or 'the selection'}{clean}"
    if isinstance(change, RoadwayAddition):
        bits = [f"link {few([r['link_id'] for r in change.links])}"] if change.links else []
        bits += [f"node {few([r['node_id'] for r in change.nodes])}"] if change.nodes else []
        return f"roadway_addition {' and '.join(bits)}"
    return change.type


def few(ids: Sequence[Any], n: int = 5) -> str:
    """The first ``n`` ids, and how many more.

    >>> few([1, 2, 3, 4, 5, 6, 7])
    '1, 2, 3, 4, 5 (+2 more)'
    """
    items = list(ids)
    shown = ", ".join(str(i) for i in items[:n])
    return shown + (f" (+{len(items) - n} more)" if len(items) > n else "")


def rows_by_key(frame: Any, pk: str, ids: Sequence[Any], columns: Sequence[str]) -> dict[str, dict[str, Any]]:
    """``{str(key): {column: value}}`` for the rows of a pandas ``frame`` whose ``pk`` is in ``ids``.

    Keys compare as text (an id typed ``"7"`` finds row 7); missing cells come back as ``None`` and numpy scalars as
    Python ones. Uses only the frame's own methods, so the plugin needs no pandas import.
    """
    wanted = {str(i) for i in ids}
    keep = list(dict.fromkeys([pk, *columns]))
    sub = frame.loc[frame[pk].astype(str).isin(wanted), keep].astype(object)
    sub = sub.where(sub.notna(), None)
    return {str(_py(r[pk])): {c: _py(r[c]) for c in columns} for r in sub.to_dict("records")}


# ---------------------------------------------------------------- helpers


def _py(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _what(pc: PropertyChange) -> str:
    new = f"{pc.set}" if pc.set is not None else f"{pc.change:+g}"
    return f"{pc.existing} → {new}" if pc.existing is not None else (f"→ {new}" if pc.set is not None else new)


def _frame(host: Host, net_id: str, table: str) -> Any:
    h = host.network(net_id)
    return h.links_df() if table == "link" else h.nodes_df()


def _plan(fn: Callable[..., EditPlan], *args: Any) -> EditPlan:
    try:
        return fn(*args)
    except EditRefused as exc:
        raise CardError(str(exc)) from exc


def _mutate(host: Host, net_id: str, plans: list[EditPlan], note: str) -> None:
    """Apply the plans' edits in one ``host.mutate``, refused if the network moved since the first plan."""
    edits = [e for p in plans for e in p.edits]
    host.mutate(net_id, edits, note=note, expected_version=plans[0].version)


def _same(current: Any, existing: Any) -> bool:
    if current is None:
        return False
    try:
        return math.isclose(float(current), float(existing), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(current) == str(existing)


def _groups(pc: PropertyChange, ids: list[Any], current: dict[str, dict[str, Any]], prop: str) -> dict[Any, list[Any]]:
    """New value -> the ids that get it: one group for ``set``; one per distinct current value for ``change``."""
    if pc.set is not None:
        return {pc.set: list(ids)}
    groups: dict[Any, list[Any]] = defaultdict(list)
    for i in ids:
        now = current[str(i)][prop]
        if now is None:
            raise CardError(f"{prop} on {i} is empty: set it rather than changing it by {pc.change:+g}")
        if isinstance(now, bool) or not isinstance(now, int | float):
            raise CardError(f"{prop} holds {type(now).__name__} values: only numbers change by a delta")
        new = now + pc.change
        groups[int(new) if isinstance(now, int) and float(new).is_integer() else new].append(i)
    return dict(groups)


# ---------------------------------------------------------------- appliers


def _property_change(host: Host, net_id: str, change: RoadwayPropertyChange, note: str) -> Applied:
    sel = change.facility
    pk = KEYS[sel.table]
    frame = _frame(host, net_id, sel.table)
    unknown = [p for p in change.property_changes if p not in frame.columns]
    if unknown:
        raise CardError(f"{sel.table} has no column(s) {', '.join(unknown)}")
    ids = list(dict.fromkeys(sel.target_ids()))
    current = rows_by_key(frame, pk, ids, list(change.property_changes))
    missing = [i for i in ids if str(i) not in current]
    if missing:
        raise CardError(f"{sel.table} {few(missing)} not found")
    out = Applied(change)
    plans: list[EditPlan] = []
    for prop, pc in change.property_changes.items():
        if pc.existing is not None:
            bad = [i for i in ids if not _same(current[str(i)][prop], pc.existing)]
            if bad:
                found = current[str(bad[0])][prop]
                message = f"{prop}: expected {pc.existing!r} on {sel.table} {few(bad)}, found {found!r}"
                if pc.existing_value_conflict == "error":
                    raise CardError(f"conflict: {message}")
                if pc.existing_value_conflict == "skip":
                    out.skipped.append(message)
                    continue
                out.warnings.append(message)
        for value, group in _groups(pc, ids, current, prop).items():
            plans.append(_plan(host.plan_update, net_id, sel.table, group, {prop: value}))
    if plans:
        _mutate(host, net_id, plans, note)
        out.steps, out.rows = 1, {sel.table: len(ids)}
    return out


_APPLIERS: dict[str, Callable[[Host, str, Any, str], Applied]] = {
    "roadway_property_change": _property_change,
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_lower_property.py -q`
Expected: all pass. The `key` refusal comes from core (`link_id is the link key; keys cannot be edited here …`).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check packages/netstead-cards && uv run lint-imports
git add packages/netstead-cards/netstead_cards/lower.py packages/netstead-cards/tests/test_lower_property.py
git commit -m "feat(cards): lower property changes onto host.plan_update and host.mutate"
```

---

### Task 8: Lower deletions (with `clean_nodes`) and additions

**Files:**
- Modify: `packages/netstead-cards/netstead_cards/lower.py`
- Test: `packages/netstead-cards/tests/test_lower_delete_add.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_lower_delete_add.py`:

```python
"""Deletions cascade through core's plans (links, then nodes); additions are one mutation, nodes first."""

import pytest
from netstead_cards.errors import CardError
from netstead_cards.lower import apply_change
from netstead_cards.types import RoadwayAddition, RoadwayDeletion

NET = "leavenworth"
NEW_NODE = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.60, "node_type": "intersection"}
NEW_LINK = {
    "link_id": 9001,
    "from_node_id": 1,
    "to_node_id": 9001,
    "name": "New Street",
    "facility_type": "residential",
    "lanes": 1,
    "free_speed": 25.0,
    "allowed_uses": "auto,walk,bike",
    "directed": False,
}


def counts(session):
    return {name: table.count() for name, table in session.network(NET).roadway.tables.items()}


def test_deleting_a_link_takes_its_lanes(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayDeletion(links=[1]))
    after = counts(session)
    assert after["link"] == before["link"] - 1 and after["lane"] == before["lane"] - 1
    assert applied.steps == 1 and applied.rows == {"lane": 1, "link": 1}
    assert session.network(NET).lineage[-1] == "cards: roadway_deletion link 1"


def test_a_node_still_used_by_a_link_is_refused(session, host):
    with pytest.raises(CardError, match="still used"):
        apply_change(host, NET, RoadwayDeletion(nodes=[1]))
    assert session.network(NET).version == 0


def test_missing_ids_are_refused(host):
    with pytest.raises(CardError, match="not found"):
        apply_change(host, NET, RoadwayDeletion(links=[999999]))


def test_an_empty_deletion_is_refused(host):
    with pytest.raises(CardError, match="needs links and/or nodes"):
        apply_change(host, NET, RoadwayDeletion())


def test_adding_a_node_and_a_link_is_one_mutation(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[NEW_LINK]))
    assert applied.steps == 1 and applied.rows == {"node": 1, "link": 1}
    assert counts(session)["link"] == before["link"] + 1 and counts(session)["node"] == before["node"] + 1
    assert len(session.edits[NET].last().results) == 2  # the node's add_rows, then the link's
    links = session.network(NET).links_df()
    assert links.loc[links.link_id == 9001, "name"].tolist() == ["New Street"]


def test_clean_nodes_deletes_end_nodes_nothing_else_uses(session, host):
    apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[NEW_LINK]))
    nodes = counts(session)["node"]
    applied = apply_change(host, NET, RoadwayDeletion(links=[9001], clean_nodes=True))
    assert counts(session)["node"] == nodes - 1  # 9001 went; node 1 is used by other links and stays
    assert applied.steps == 2  # links, then nodes: core plans a node deletion against the network as it is
    assert [e.note for e in session.edits[NET].entries[-2:]] == [
        "roadway_deletion link 9001 (and unused end nodes) (links)",
        "roadway_deletion link 9001 (and unused end nodes) (nodes)",
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (RoadwayAddition(links=[{**NEW_LINK, "link_id": 1}], nodes=[NEW_NODE]), "link 1 already exist"),
        (RoadwayAddition(links=[{**NEW_LINK, "to_node_id": 424242}]), "node.* 424242, which are not in the network"),
        (RoadwayAddition(links=[{**NEW_LINK, "colour": "red"}], nodes=[NEW_NODE]), "no column"),
        (RoadwayAddition(nodes=[NEW_NODE, NEW_NODE]), "repeat"),
    ],
)
def test_bad_additions_are_refused_and_leave_nothing_behind(session, host, change, message):
    before = counts(session)
    with pytest.raises(CardError, match=message):
        apply_change(host, NET, change)
    assert counts(session) == before and session.network(NET).version == 0
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_lower_delete_add.py -q`
Expected: FAIL with `KeyError: 'roadway_deletion'` (no applier yet).

- [ ] **Step 3: Implement**

In `packages/netstead-cards/netstead_cards/lower.py`, add before `_APPLIERS`:

```python
def _add_rows(into: dict[str, int], rows: dict[str, int]) -> None:
    for table, n in rows.items():
        into[table] = into.get(table, 0) + n


def _used(links: Any, nodes: set[Any]) -> set[Any]:
    """The nodes among ``nodes`` that some link (in the ``links`` frame) still starts or ends at."""
    wanted = {str(n) for n in nodes}
    hit = links["from_node_id"].astype(str).isin(wanted) | links["to_node_id"].astype(str).isin(wanted)
    sub = links.loc[hit, ["from_node_id", "to_node_id"]]
    found = {str(_py(v)) for v in [*sub["from_node_id"], *sub["to_node_id"]]}
    return {n for n in nodes if str(n) in found}


def _deletion(host: Host, net_id: str, change: RoadwayDeletion, note: str) -> Applied:
    out = Applied(change)
    two_steps = bool(change.links) and (bool(change.nodes) or change.clean_nodes)
    ends: set[Any] = set()
    if change.links:
        if change.clean_nodes:
            rows = rows_by_key(_frame(host, net_id, "link"), "link_id", change.links, ["from_node_id", "to_node_id"])
            ends = {v for r in rows.values() for v in (r["from_node_id"], r["to_node_id"]) if v is not None}
        plan = _plan(host.plan_delete, net_id, "link", change.links)
        host.mutate(net_id, plan.edits, note=f"{note} (links)" if two_steps else note, expected_version=plan.version)
        out.steps += 1
        _add_rows(out.rows, plan.rows)
    nodes = list(change.nodes)
    if ends:  # read the links again: the deleted ones are gone now
        unused = ends - _used(_frame(host, net_id, "link"), ends)
        nodes += [n for n in sorted(unused, key=str) if n not in nodes]
    if nodes:
        plan = _plan(host.plan_delete, net_id, "node", nodes)  # refuses a node a link still uses, naming the links
        host.mutate(net_id, plan.edits, note=f"{note} (nodes)" if two_steps else note, expected_version=plan.version)
        out.steps += 1
        _add_rows(out.rows, plan.rows)
    return out


def _addition(host: Host, net_id: str, change: RoadwayAddition, note: str) -> Applied:
    nodes_frame = _frame(host, net_id, "node")
    frames = {"node": nodes_frame, "link": _frame(host, net_id, "link")}
    for name, records in (("node", change.nodes), ("link", change.links)):
        pk = KEYS[name]
        ids = [r[pk] for r in records]
        if len({str(i) for i in ids}) != len(ids):
            raise CardError(f"{name} ids repeat within the change")
        taken = rows_by_key(frames[name], pk, ids, [])
        if taken:
            raise CardError(f"{name} {few(sorted(taken))} already exist")
    if change.links:
        ends = list(dict.fromkeys(r[c] for r in change.links for c in ("from_node_id", "to_node_id")))
        present = set(rows_by_key(nodes_frame, "node_id", ends, [])) | {str(r["node_id"]) for r in change.nodes}
        missing = [e for e in ends if str(e) not in present]
        if missing:  # core would only warn; Wrangler fails such a card, so refuse before anything is applied
            raise CardError(
                f"added links use node(s) {few(missing)}, which are not in the network; add them in the same change"
            )
    plans = []
    if change.nodes:  # nodes first: the links need their ends
        plans.append(_plan(host.plan_add, net_id, "node", change.nodes))
    if change.links:
        plans.append(_plan(host.plan_add, net_id, "link", change.links))
    _mutate(host, net_id, plans, note)
    out = Applied(change, steps=1)
    for plan in plans:
        _add_rows(out.rows, plan.rows)
    return out
```

and register them:

```python
_APPLIERS: dict[str, Callable[[Host, str, Any, str], Applied]] = {
    "roadway_property_change": _property_change,
    "roadway_deletion": _deletion,
    "roadway_addition": _addition,
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass.

- [ ] **Step 5: Lint, the default suite, commit, open PR A**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check . && uv run lint-imports \
  && uv run python scripts/lint_no_sql.py
uv run --all-extras pytest packages -n auto -q
git add packages/netstead-cards/netstead_cards/lower.py packages/netstead-cards/tests/test_lower_delete_add.py
git commit -m "feat(cards): lower deletions (clean_nodes) and additions onto host plans"
uv run --all-extras pytest packages -n auto -m "" -q
```

Push `feat/cards-a` and open PR A ("Cards plugin A: package and change model"), listing Tasks 0–8 and the Facts
section's items 1–6 and 9.

---
## PR B: Actions

Branch `feat/cards-b` from `main` once PR A has merged (or from `feat/cards-a` while it is in review, rebasing later).

### Task 9: Draft state, `cards.add_change` and `cards.undo_change`

**Files:**
- Create: `packages/netstead-cards/netstead_cards/state.py`, `flow.py`, `actions.py`, `handlers.py`
- Modify: `packages/netstead-cards/netstead_cards/plugin.py`, `__init__.py`
- Test: `packages/netstead-cards/tests/test_actions.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_actions.py`:

```python
"""cards.add_change / cards.undo_change: applied through core, recorded, drafted per network, replayable."""

import pytest
from netstead.workbench.actions import ClearSelection, EditCells, SaveSelection, Select
from netstead.workbench.errors import ActionError, NotSupportedYet
from netstead_cards.actions import AddChange, UndoChange
from netstead_cards.types import (
    PropertyChange,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)

NET = "leavenworth"


def props(**values):
    return RoadwayPropertyChange(property_changes={k: PropertyChange(**v) for k, v in values.items()})


def cell(session, key, column):
    links = session.network(NET).links_df()
    return links.loc[links.link_id == key, column].tolist()


def draft(session):
    return session.state()["plugins"]["cards"]["drafts"].get(NET)


def test_a_pick_is_applied_recorded_and_drafted(session):
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(AddChange(use_selection=True, change=props(free_speed={"existing": 40, "set": 30})))
    assert cell(session, 1, "free_speed") == [30.0]
    assert (result["net_id"], result["count"], result["steps"]) == (NET, 1, 1)
    assert result["change"]["facility"] == {
        "table": "link",
        "ids": [1],
        "query": None,
        "resolved": None,
        "ignore_missing": False,
    }  # a pick is written by id, ignore_missing false
    assert session.edits[NET].last().source == "cards"
    assert session.network(NET).lineage[-1] == "cards: roadway_property_change link 1: free_speed 40 → 30"
    assert session.history[-1].imports == "from netstead_cards.actions import AddChange"
    assert draft(session) == {
        "project": "",
        "count": 1,
        "changes": [
            {
                "type": "roadway_property_change",
                "describe": "roadway_property_change link 1: free_speed 40 → 30",
                "steps": 1,
            }
        ],
    }


def test_a_query_selection_keeps_its_query_and_the_ids_it_resolved_to(session):
    session.dispatch(Select(utterance="Benton Street"))
    result = session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))
    facility = result["change"]["facility"]
    assert facility["query"] == {"links": {"name": ["Benton Street"], "ignore_missing": True}}  # the intent's value
    assert facility["resolved"] == [1, 5, 6, 8] and facility["ids"] is None
    assert cell(session, 8, "lanes") == [2]


def test_a_saved_selection_seeds_the_facility(session):
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(SaveSelection(name="benton"))
    session.dispatch(ClearSelection())
    result = session.dispatch(AddChange(saved_selection="benton", change=props(lanes={"set": 2})))
    assert result["change"]["facility"]["resolved"] == [1, 5, 6, 8]


def test_a_deletion_takes_its_links_from_the_selection(session):
    session.dispatch(Select(link_ids=[2]))
    result = session.dispatch(AddChange(use_selection=True, change=RoadwayDeletion()))
    assert result["change"]["links"] == [2] and cell(session, 2, "lanes") == []


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (AddChange(use_selection=True, change=props(lanes={"set": 2})), "nothing is selected"),
        (AddChange(saved_selection="nope", change=props(lanes={"set": 2})), "no saved selection named 'nope'"),
        (AddChange(change=props(lanes={"set": 2})), "needs a facility"),
    ],
)
def test_a_change_without_targets_is_refused_and_changes_nothing(session, action, message):
    with pytest.raises(ActionError, match=message):
        session.dispatch(action)
    assert session.network(NET).version == 0 and draft(session) is None


def test_a_change_that_would_not_be_a_valid_card_is_refused_before_it_is_applied(session):
    bad = Selection(query={"links": {"name": ["Benton Street"], "ref": ["US 2"], "ignore_missing": True}}, resolved=[1])
    with pytest.raises(ActionError, match=r"would not be a valid ProjectCard: .*facility/links"):
        session.dispatch(
            AddChange(change=RoadwayPropertyChange(facility=bad, property_changes={"lanes": PropertyChange(set=2)}))
        )
    assert session.network(NET).version == 0


def test_transit_is_not_supported_yet(session):
    with pytest.raises(NotSupportedYet, match="transit_property_change is not yet supported"):
        session.dispatch(AddChange(change=TransitPropertyChange()))


def test_a_deletion_is_all_or_nothing_across_its_two_steps(session):
    links_before = len(session.network(NET).links_df())
    with pytest.raises(ActionError, match="still used"):
        session.dispatch(AddChange(change=RoadwayDeletion(links=[1], nodes=[1])))  # node 1 has seven more links
    assert len(session.network(NET).links_df()) == links_before and session.network(NET).version == 0
    assert not getattr(session.edits.get(NET), "entries", None)  # no pending edit survived the rollback


def test_undo_reverses_the_last_change_then_the_one_before(session):
    session.dispatch(AddChange(change=props(lanes={"set": 2}).model_copy(update={"facility": Selection(ids=[1])})))
    session.dispatch(AddChange(change=props(lanes={"set": 3}).model_copy(update={"facility": Selection(ids=[1])})))
    assert session.dispatch(UndoChange())["count"] == 1 and cell(session, 1, "lanes") == [2]
    assert session.dispatch(UndoChange())["count"] == 0 and cell(session, 1, "lanes") == [1]
    with pytest.raises(ActionError, match="nothing to undo"):
        session.dispatch(UndoChange())


def test_undo_takes_back_every_step_of_a_deletion(session):
    from netstead_cards.types import RoadwayAddition

    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}
    link = {
        "link_id": 9001,
        "from_node_id": 1,
        "to_node_id": 9001,
        "name": "New",
        "facility_type": "residential",
        "lanes": 1,
        "allowed_uses": "auto",
    }
    session.dispatch(AddChange(change=RoadwayAddition(nodes=[node], links=[link])))
    session.dispatch(AddChange(change=RoadwayDeletion(links=[9001], clean_nodes=True)))
    assert draft(session)["changes"][-1]["steps"] == 2
    session.dispatch(UndoChange())
    assert cell(session, 9001, "name") == ["New"] and len(session.edits[NET].entries) == 1


def test_undo_refuses_when_core_made_the_newest_change(session):
    session.dispatch(AddChange(change=props(lanes={"set": 2}).model_copy(update={"facility": Selection(ids=[1])})))
    session.dispatch(EditCells(ids=[3], values={"lanes": 3}))
    with pytest.raises(ActionError, match="was made by"):
        session.dispatch(UndoChange())
    assert draft(session)["count"] == 1 and cell(session, 1, "lanes") == [2]


def test_every_cards_action_is_drafted_by_the_assistant(session):
    types = [t for t in session.actions.types() if t.startswith("cards.")]
    assert types and all(session.actions.model(t).mutates for t in types)


def test_a_replayed_session_reproduces_the_network(session, make_session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(AddChange(use_selection=True, change=props(free_speed={"set": 30})))
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))
    session.dispatch(AddChange(use_selection=True, change=props(lanes={"change": 1})))
    session.dispatch(UndoChange())
    other = make_session(open_network=False)
    namespace = {"app": other}
    for entry in session.history:
        if entry.parent_seq is None and entry.ok:
            exec(entry.imports, namespace)  # our own recorded "copy as Python" lines
            exec(entry.python, namespace)
    links = other.network(NET).links_df()
    assert links.loc[links.link_id == 1, "free_speed"].tolist() == [30.0]
    assert links.loc[links.link_id.isin([1, 5, 6, 8]), "lanes"].tolist() == [2, 2, 2, 2]
    assert other.state()["plugins"]["cards"]["drafts"][NET]["count"] == 2
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_actions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'netstead_cards.actions'`.

- [ ] **Step 3: The Action models**

Create `packages/netstead-cards/netstead_cards/actions.py`:

```python
"""The cards plugin's Actions: recorded in history, replayable with "Copy as Python", dispatchable over HTTP.

Each one changes a network, registers or closes a preview network, or writes a file, so each sets ``mutates``: the
natural-language assistant drafts them for the user to approve.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from netstead.workbench.plugins import BaseAction
from pydantic import Field, model_validator

from .types import NetworkChange

__all__ = ["AddChange", "UndoChange"]


class _ChangeAction(BaseAction):
    """Fields an Action that applies one change shares (not an Action itself: it has no ``type``)."""

    mutates: ClassVar[bool] = True
    net_id: str | None = None
    change: NetworkChange
    use_selection: bool = False
    saved_selection: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _one_source(self) -> _ChangeAction:
        if self.use_selection and self.saved_selection is not None:
            raise ValueError("use the shared selection or a saved selection, not both")
        return self


class AddChange(_ChangeAction):
    """Apply one roadway change to a network and add it to that network's draft ProjectCard.

    ``change`` is a ProjectCard change: ``roadway_property_change``, ``roadway_deletion`` or ``roadway_addition``
    (the transit types answer "not yet supported"). With ``use_selection`` a property change or a deletion targets
    the shared selection: a click-pick by its link ids, a query by its query (with the ids it resolved to). With
    ``saved_selection`` it targets that saved selection instead. ``net_id`` defaults to the active network (an open
    preview stands for the network it previews).
    """

    type: Literal["cards.add_change"] = "cards.add_change"


class UndoChange(BaseAction):
    """Undo the last change of a network's draft ProjectCard (committed cards are not undone)."""

    type: Literal["cards.undo_change"] = "cards.undo_change"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
```

- [ ] **Step 4: Draft state**

Create `packages/netstead-cards/netstead_cards/state.py`:

```python
"""The plugin's own nouns: one draft card per network, the cards committed this session, and the open preview.

It lives in the plugin factory's closure (one per Workbench session) and is changed only by the plugin's Action
handlers, after every Host call they make has succeeded: a failing handler's network changes are rolled back by the
Workbench, and this state must not have moved. The routes read it outside the session lock, so it has its own.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .card import Dependencies, DraftCard
from .lower import Applied, describe
from .types import CHANGE_TYPES, ROADWAY_TYPES, NetworkChange

__all__ = ["CardsState", "Draft", "slug"]


def slug(text: str) -> str:
    """A card id from a project name: lower case, words joined by ``-``.

    >>> slug("I-90 HOV lanes, 2035")
    'i-90-hov-lanes-2035'
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "card"


@dataclass
class Draft:
    """A network's draft card and, per change, how many ``host.mutate`` calls it took (undone together)."""

    card: DraftCard = field(default_factory=DraftCard)
    steps: list[int] = field(default_factory=list)


class CardsState:
    """Drafts per network, committed cards and the open preview, behind one lock."""

    def __init__(self) -> None:
        """Start with no drafts, no committed cards and no preview."""
        self._lock = threading.RLock()
        self.drafts: dict[str, Draft] = {}
        self.committed: list[dict[str, Any]] = []
        self.preview: dict[str, Any] | None = None

    def draft(self, net_id: str) -> Draft:
        """``net_id``'s draft (an empty one is created on first use)."""
        with self._lock:
            return self.drafts.setdefault(net_id, Draft())

    def add(self, net_id: str, applied: Applied) -> int:
        """Append an applied change to ``net_id``'s draft; return the draft's change count."""
        with self._lock:
            d = self.draft(net_id)
            d.card = d.card.model_copy(update={"changes": [*d.card.changes, applied.change]})
            d.steps.append(applied.steps)
            return len(d.steps)

    def pop(self, net_id: str) -> NetworkChange:
        """Drop and return the last change of ``net_id``'s draft (the caller has undone it)."""
        with self._lock:
            d = self.drafts[net_id]
            change = d.card.changes[-1]
            d.card = d.card.model_copy(update={"changes": d.card.changes[:-1]})
            d.steps.pop()
            return change

    def name(
        self, net_id: str, *, project: str, tags: list[str], dependencies: Dependencies, notes: str | None = None
    ) -> None:
        """Set the draft's project, tags, dependencies and notes (an import names an empty draft after its card)."""
        with self._lock:
            d = self.draft(net_id)
            d.card = d.card.model_copy(
                update={"project": project, "tags": list(tags), "dependencies": dependencies, "notes": notes}
            )

    def commit(self, net_id: str, card: DraftCard, path: str) -> str:
        """Record ``card`` as committed to ``path``, start a new empty draft, and return the card's id."""
        with self._lock:
            taken = {c["card_id"] for c in self.committed}
            base = card_id = slug(card.project)
            n = 2
            while card_id in taken:
                card_id, n = f"{base}-{n}", n + 1
            self.committed.append(
                {
                    "card_id": card_id,
                    "net_id": net_id,
                    "path": path,
                    "project": card.project,
                    "changes": len(card.changes),
                }
            )
            self.drafts[net_id] = Draft()
            return card_id

    def snapshot(self, is_open: Callable[[str], bool]) -> dict[str, Any]:
        """JSON-safe state for ``/api/state`` (``plugins.cards``); drops drafts and previews of closed networks.

        A closed network's pending edits went with it, so its draft can never be committed or undone.
        """
        with self._lock:
            for net_id in [n for n in self.drafts if not is_open(n)]:
                del self.drafts[net_id]
            if self.preview is not None and not is_open(self.preview["net_id"]):
                self.preview = None
            drafts = {
                net_id: {
                    "project": d.card.project,
                    "count": len(d.steps),
                    "changes": [
                        {"type": c.type, "describe": describe(c), "steps": s}
                        for c, s in zip(d.card.changes, d.steps, strict=True)
                    ],
                }
                for net_id, d in self.drafts.items()
                if d.steps or d.card.project
            }
            return {
                "drafts": drafts,
                "committed": [dict(c) for c in self.committed],
                "preview": dict(self.preview) if self.preview is not None else None,
                "change_types": list(CHANGE_TYPES),
                "supported": sorted(ROADWAY_TYPES),
            }
```

- [ ] **Step 5: Shared handler steps**

Create `packages/netstead-cards/netstead_cards/flow.py`:

```python
"""Steps the Action handlers and the routes share: which network, which facility, is it a valid card, and errors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from netstead.workbench.plugins import ActionError, Host, NotSupportedYet
from pydantic import ValidationError

from .card import DraftCard
from .errors import CardError, CardNotSupported, not_supported
from .mapping import FieldMap
from .state import CardsState
from .types import TRANSIT_TYPES, NetworkChange, RoadwayDeletion, RoadwayPropertyChange, Selection
from .validate import card_problems

__all__ = [
    "SELECTABLE",
    "as_action_errors",
    "check_card_shape",
    "complete_change",
    "is_open",
    "refuse_transit",
    "selection_facility",
    "target_network",
]

#: The change types that take their targets from a selection.
SELECTABLE = frozenset({"roadway_property_change", "roadway_deletion"})


def refuse_transit(change: NetworkChange) -> None:
    """Raise ``NotSupportedYet`` for a transit change (declared, so it parses; not applied or written yet)."""
    if change.type in TRANSIT_TYPES:
        raise NotSupportedYet(str(not_supported(change.type)))


@contextmanager
def as_action_errors(prefix: str = "") -> Iterator[None]:
    """Turn the plugin's own errors into the Workbench's: shown to the user, recorded as a failed Action."""
    try:
        yield
    except CardNotSupported as exc:
        raise NotSupportedYet(prefix + str(exc)) from exc
    except CardError as exc:
        raise ActionError(prefix + str(exc)) from exc
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise ActionError(prefix + "; ".join(e["msg"] for e in errors)) from exc


def is_open(host: Host, net_id: str) -> bool:
    """Whether a network with this id is open in the session."""
    try:
        host.network(net_id)
    except ActionError:
        return False
    return True


def target_network(host: Host, state: CardsState, net_id: str | None) -> str:
    """The network a change goes to: ``net_id``, else the active one; an open preview stands for its base."""
    net = net_id or host.active
    if net is None:
        raise CardError("no network is open")
    preview = state.preview
    if preview is not None and net == preview["net_id"]:
        net = preview["base"]
    if not is_open(host, net):
        raise CardError(f"there is no open network {net!r}")
    return net


def selection_facility(host: Host, net: str, *, use_selection: bool, saved: str | None) -> tuple[Selection, list[str]]:
    """The facility the shared (or a saved) selection gives, and its warnings.

    A click-pick becomes its link ids (``ignore_missing`` false); a query becomes its query, carrying the intent's
    ``ignore_missing``, with the link ids it resolved to as provenance.
    """
    if use_selection:
        payload: dict[str, Any] | None = host.selection
        where = "the selection"
        if payload is None:
            raise CardError("nothing is selected")
    else:
        payload = host.saved_selection(saved or "")
        where = f"the saved selection {saved!r}"
        if payload is None:
            raise CardError(f"there is no saved selection named {saved!r}")
    if payload.get("net_id") != net:
        raise CardError(f"{where} is on network {payload.get('net_id')!r}, not {net!r}")
    forms = payload.get("projectcard")
    if payload.get("status") != "resolved" or not forms:
        raise CardError(f"{where} is not resolved; pin its anchors first")
    ids = list(payload["link_ids"])
    selection = Selection(ids=ids) if forms["picked"] else Selection(query=forms["query"], resolved=ids)
    return selection, list(payload.get("warnings") or [])


def complete_change(
    host: Host, net: str, change: NetworkChange, use_selection: bool, saved: str | None
) -> tuple[NetworkChange, list[str]]:
    """``change`` with its targets taken from the shared (or a saved) selection when asked, and its warnings."""
    if not use_selection and saved is None:
        return change, []
    if change.type not in SELECTABLE:
        raise CardError(f"{change.type} does not take a selection")
    selection, warnings = selection_facility(host, net, use_selection=use_selection, saved=saved)
    if isinstance(change, RoadwayDeletion):
        if change.links or change.nodes:
            raise CardError("give the links to delete or use a selection, not both")
        return change.model_copy(update={"links": selection.target_ids()}), warnings
    if not isinstance(change, RoadwayPropertyChange) or change.facility is not None:
        raise CardError("give a facility or use a selection, not both")
    return change.model_copy(update={"facility": selection}), warnings


def check_card_shape(change: NetworkChange, mapping: FieldMap) -> None:
    """Refuse a change that would not be a valid ProjectCard (checked with ``projectcard``) before it is applied."""
    problems = card_problems(DraftCard(project="check", changes=[change]).to_card(mapping))
    if problems:
        raise CardError(f"this change would not be a valid ProjectCard: {problems[0]}")
```

- [ ] **Step 6: The handlers**

Create `packages/netstead-cards/netstead_cards/handlers.py`:

```python
"""The cards plugin's Action handlers, sharing one session's drafts (:class:`~netstead_cards.state.CardsState`).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network.
"""

from __future__ import annotations

from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, UndoChange
from .flow import as_action_errors, check_card_shape, complete_change, refuse_transit, target_network
from .lower import apply_change, describe
from .mapping import FieldMap
from .state import CardsState

__all__ = ["Handlers"]


class Handlers:
    """The Action handlers of one plugin instance (one Workbench session)."""

    def __init__(self, state: CardsState, mapping: FieldMap) -> None:
        """Bind the handlers to a session's draft state and the field mapping."""
        self.state, self.mapping = state, mapping

    def specs(self) -> tuple[ActionSpec, ...]:
        """Every Action this plugin registers, paired with its handler."""
        return (
            ActionSpec(AddChange, self.add_change),
            ActionSpec(UndoChange, self.undo_change),
        )

    def add_change(self, host: Host, action: AddChange) -> dict[str, Any]:
        """Apply the change to the base network (never a preview) and append it to that network's draft."""
        refuse_transit(action.change)
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            change, warnings = complete_change(host, net, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            applied = apply_change(host, net, change)
        applied.warnings[:0] = warnings
        count = self.state.add(net, applied)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "count": count, "change": change.model_dump(mode="json"), **applied.summary()}

    def undo_change(self, host: Host, action: UndoChange) -> dict[str, Any]:
        """Undo the draft's last change: as many ``host.undo`` calls as it made mutations, then drop it."""
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        draft = self.state.drafts.get(net)
        if draft is None or not draft.steps:
            raise ActionError("nothing to undo: the draft card has no changes (committed cards are not undone)")
        for _ in range(draft.steps[-1]):
            host.undo(net)  # refuses (ActionError) when the network's newest change is not this plugin's
        change = self.state.pop(net)
        host.publish("changed", {"net_id": net, "count": len(draft.steps)})
        return {"net_id": net, "undone": describe(change), "count": len(draft.steps)}
```

Replace `packages/netstead-cards/netstead_cards/plugin.py`'s `plugin()` and imports with:

```python
from __future__ import annotations

from netstead.workbench.plugins import WorkbenchPlugin

from ._version import __version__
from .flow import is_open
from .handlers import Handlers
from .mapping import load_mapping
from .state import CardsState

__all__ = ["REQUIRES_API", "plugin"]

#: The plugin API this plugin is written against (1.5: ActionError and NotSupportedYet).
REQUIRES_API = "1.5"


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin whose drafts live in this closure (one per Workbench session)."""
    state = CardsState()
    handlers = Handlers(state, load_mapping())
    return WorkbenchPlugin(
        id="cards",
        name="ProjectCards",
        version=__version__,
        requires_api=REQUIRES_API,
        actions=handlers.specs(),
        state=lambda host: state.snapshot(lambda net_id: is_open(host, net_id)),
    )
```

(keep the module docstring from Task 2). In `__init__.py`, add `from .actions import AddChange, UndoChange` and
`"AddChange"`, `"UndoChange"` to `__all__` (sorted after the constants, as ruff's `RUF022` wants).

- [ ] **Step 7: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check --fix packages/netstead-cards && uv run ruff format packages/netstead-cards
uv run ruff check . && uv run ruff format --check . && uv run lint-imports
git add packages/netstead-cards/netstead_cards/actions.py packages/netstead-cards/netstead_cards/state.py \
  packages/netstead-cards/netstead_cards/flow.py packages/netstead-cards/netstead_cards/handlers.py \
  packages/netstead-cards/netstead_cards/plugin.py packages/netstead-cards/netstead_cards/__init__.py \
  packages/netstead-cards/tests/test_actions.py
git commit -m "feat(cards): cards.add_change and cards.undo_change with per-network draft cards"
```

---
### Task 10: `cards.commit` and `cards.import`

**Files:**
- Modify: `packages/netstead-cards/netstead_cards/actions.py`, `handlers.py`, `__init__.py`
- Test: `packages/netstead-cards/tests/test_commit_import.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_commit_import.py`:

```python
"""cards.commit writes a projectcard-valid YAML file (warnings confirmed first); cards.import applies one."""

from typing import ClassVar, Literal

import pytest
from netstead.workbench.errors import ActionError, NotSupportedYet, PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_cards.actions import AddChange, CommitCard, ImportCard, UndoChange
from netstead_cards.card import DraftCard
from netstead_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"


def lanes(link_id, value):
    return RoadwayPropertyChange(
        facility=Selection(ids=[link_id]), property_changes={"lanes": PropertyChange(set=value)}
    )


def cell(session, key, column):
    links = session.network(NET).links_df()
    return links.loc[links.link_id == key, column].tolist()


def cards_state(session):
    return session.state()["plugins"]["cards"]


def test_commit_writes_a_card_projectcard_reads_and_starts_a_new_draft(session, tmp_path):
    import projectcard

    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "cards" / "speed.yml"
    result = session.dispatch(CommitCard(path=str(path), project="Speed fixes", tags=["fixes"]))
    assert result == {
        "card_id": "speed-fixes",
        "net_id": NET,
        "path": str(path.resolve()),
        "changes": 1,
        "warnings": 0,
        "catalog": False,
    }
    assert projectcard.read_card(path, validate=True).project == "Speed fixes"
    state = cards_state(session)
    assert NET not in state["drafts"] and state["committed"][0]["card_id"] == "speed-fixes"
    with pytest.raises(ActionError, match="nothing to undo"):
        session.dispatch(UndoChange())  # a committed card is frozen
    session.dispatch(AddChange(change=lanes(2, 2)))
    other = tmp_path / "cards" / "speed-2.yml"
    assert session.dispatch(CommitCard(path=str(other), project="Speed fixes"))["card_id"] == "speed-fixes-2"


def test_live_edit_warnings_must_be_confirmed(session, tmp_path):
    bad_end = RoadwayPropertyChange(
        facility=Selection(ids=[1]), property_changes={"from_node_id": PropertyChange(set=424242)}
    )
    session.dispatch(AddChange(change=bad_end))
    path = tmp_path / "card.yml"
    with pytest.raises(ActionError, match="live edit warning"):
        session.dispatch(CommitCard(path=str(path), project="Bad end"))
    assert not path.exists()
    result = session.dispatch(CommitCard(path=str(path), project="Bad end", confirm_warnings=True))
    assert result["warnings"] >= 1 and path.exists()


def test_commit_refusals(session, tmp_path):
    with pytest.raises(ActionError, match="no changes to commit"):
        session.dispatch(CommitCard(path=str(tmp_path / "a.yml"), project="Empty"))
    session.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(PathNotAllowed):
        session.dispatch(CommitCard(path="/etc/cards/a.yml", project="Outside"))
    taken = tmp_path / "taken.yml"
    taken.write_text("keep me", encoding="utf-8")
    with pytest.raises(ActionError, match="already exists"):
        session.dispatch(CommitCard(path=str(taken), project="Taken"))
    assert taken.read_text(encoding="utf-8") == "keep me"
    session.dispatch(CommitCard(path=str(taken), project="Taken", overwrite=True))
    assert "project: Taken" in taken.read_text(encoding="utf-8")


def test_a_replayed_commit_confirms_and_overwrites(session, tmp_path):
    session.dispatch(AddChange(change=lanes(1, 2)))
    session.dispatch(CommitCard(path=str(tmp_path / "c.yml"), project="C"))
    assert "confirm_warnings=True" in session.history[-1].python and "overwrite=True" in session.history[-1].python


class AddToCatalog(BaseAction):
    type: Literal["catalog.add_card"] = "catalog.add_card"
    mutates: ClassVar[bool] = True
    path: str


def _catalog(added, fail=False):
    def add(host, action):
        if fail:
            raise ActionError("the catalog is read-only")
        added.append(action.path)
        return "added"

    return WorkbenchPlugin(
        id="catalog", name="Catalog", version="0", requires_api=HOST_API, actions=(ActionSpec(AddToCatalog, add),)
    )


def test_commit_adds_the_card_to_the_catalog_when_there_is_one(make_session, tmp_path):
    added = []
    session = make_session(_catalog(added))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    assert session.dispatch(CommitCard(path=str(path), project="C"))["catalog"] is True
    assert added == [str(path.resolve())]


def test_a_failing_catalog_leaves_no_file_and_the_draft(make_session, tmp_path):
    session = make_session(_catalog([], fail=True))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    with pytest.raises(ActionError, match="read-only"):
        session.dispatch(CommitCard(path=str(path), project="C"))
    assert not path.exists() and cards_state(session)["drafts"][NET]["count"] == 1


def write(card, path):
    path.write_text(card.to_yaml(), encoding="utf-8")
    return str(path)


def test_import_applies_a_card_and_names_the_draft(session, tmp_path):
    path = write(DraftCard(project="Imported", tags=["x"], changes=[lanes(1, 3)]), tmp_path / "card.yml")
    result = session.dispatch(ImportCard(path=path))
    assert result["imported"] == 1 and result["project"] == "Imported" and cell(session, 1, "lanes") == [3]
    assert cards_state(session)["drafts"][NET]["project"] == "Imported"
    assert session.history[-1].python == f"app.do(ImportCard(path={path!r}))"


def test_import_is_all_or_nothing(session, tmp_path):
    path = write(DraftCard(project="Bad", changes=[lanes(1, 3), lanes(999999, 3)]), tmp_path / "bad.yml")
    with pytest.raises(ActionError, match="change 2 of 2: link 999999 not found"):
        session.dispatch(ImportCard(path=path))
    assert (
        cell(session, 1, "lanes") == [1]
        and session.network(NET).version == 0
        and NET not in cards_state(session)["drafts"]
    )


def test_import_reads_the_offline_reports_edit_log(session, tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log

    now = cell(session, 7, "lanes")[0]
    path = tmp_path / "edits.yaml"
    dump_edit_log(
        EditLog(
            edits=[
                Edit(
                    id="e1",
                    kind="fix",
                    table="link",
                    pk={"link_id": 7},
                    column="lanes",
                    from_value=now,
                    to_value=now + 1,
                    reason="too few",
                )
            ]
        ),
        path,
    )
    session.dispatch(ImportCard(path=str(path)))
    assert cell(session, 7, "lanes") == [now + 1]


def test_import_refuses_a_query_without_provenance(session, tmp_path):
    path = tmp_path / "query.yml"
    path.write_text(
        "project: q\nchanges:\n- roadway_property_change:\n    facility:\n      links:\n        name: [Benton Street]\n"
        "        ignore_missing: true\n    property_changes:\n      lanes: {set: 2}\n",
        encoding="utf-8",
    )
    with pytest.raises(NotSupportedYet, match="apply_card"):
        session.dispatch(ImportCard(path=str(path)))


def test_import_refusals(session, tmp_path):
    with pytest.raises(PathNotAllowed):
        session.dispatch(ImportCard(path="/etc/card.yml"))
    with pytest.raises(ActionError, match="is not a file"):
        session.dispatch(ImportCard(path=str(tmp_path / "missing.yml")))
    empty = write(DraftCard(project="Empty"), tmp_path / "empty.yml")
    with pytest.raises(ActionError, match="no changes to import"):
        session.dispatch(ImportCard(path=empty))
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_commit_import.py -q`
Expected: FAIL with `ImportError: cannot import name 'CommitCard' from 'netstead_cards.actions'`.

- [ ] **Step 3: The Action models**

In `packages/netstead-cards/netstead_cards/actions.py`: the typing import becomes
`from typing import Any, ClassVar, Literal`, `__all__` becomes `["AddChange", "CommitCard", "ImportCard",
"UndoChange"]`, and append:

```python
class CommitCard(BaseAction):
    """Write a network's draft card as a ProjectCard YAML file and start a new, empty draft.

    Live edit warnings must be confirmed (``confirm_warnings``), the same rule core applies before saving a copy.
    ``path`` must be inside ``io.allowed_roots``; an existing file is replaced only with ``overwrite``. When the
    ``catalog`` plugin is installed, the card is also added to it (``catalog.add_card``).
    """

    type: Literal["cards.commit"] = "cards.commit"
    mutates: ClassVar[bool] = True
    # The user confirmed in the live session; a replayed script writes the same card again without stopping.
    replay_overrides: ClassVar[dict[str, Any]] = {"confirm_warnings": True, "overwrite": True}
    net_id: str | None = None
    path: str = Field(min_length=1, max_length=1000)
    project: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)
    dependencies: dict[Literal["prerequisites", "corequisites", "conflicts"], list[str]] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=5000)
    confirm_warnings: bool = False
    overwrite: bool = False


class ImportCard(BaseAction):
    """Apply every change of a ProjectCard YAML file to a network's draft card, all or nothing.

    Also reads the offline validation report's edit log. A query selection is applied to the link ids recorded in
    the card's notes when the plugin wrote it; a query without them is refused (re-resolving arrives with
    ``apply_card``). ``path`` must be inside ``io.allowed_roots``.
    """

    type: Literal["cards.import"] = "cards.import"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
    path: str = Field(min_length=1, max_length=1000)
```

- [ ] **Step 4: The handlers**

In `packages/netstead-cards/netstead_cards/handlers.py`:
- imports become:

```python
from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, CommitCard, ImportCard, UndoChange
from .card import Dependencies, read_card_file
from .errors import CardError
from .flow import as_action_errors, check_card_shape, complete_change, refuse_transit, target_network
from .lower import Applied, apply_change, describe
from .mapping import FieldMap
from .state import CardsState
from .validate import card_problems
```

- `specs()` returns:

```python
        return (
            ActionSpec(AddChange, self.add_change),
            ActionSpec(UndoChange, self.undo_change),
            ActionSpec(CommitCard, self.commit),
            ActionSpec(ImportCard, self.import_card),
        )
```

- append the two handlers to `Handlers`:

```python
    def commit(self, host: Host, action: CommitCard) -> dict[str, Any]:
        """Check, write and record the draft as a card; dispatch ``catalog.add_card`` when a catalog is installed."""
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            draft = self.state.drafts.get(net)
            if draft is None or not draft.steps:
                raise CardError("the draft card has no changes to commit")
            warnings = host.edit_warnings(net)
            if warnings and not action.confirm_warnings:
                raise CardError(
                    f"{net} has {len(warnings)} live edit warning(s); review them in the Edits panel, "
                    "then commit with confirm_warnings=True"
                )
            card = draft.card.model_copy(
                update={
                    "project": action.project,
                    "tags": list(action.tags),
                    "dependencies": Dependencies(**action.dependencies),
                    "notes": action.notes,
                }
            )
            problems = card_problems(card.to_card(self.mapping))
            if problems:
                raise CardError(f"not a valid ProjectCard: {problems[0]}")
        path = host.writable(action.path)  # PathNotAllowed (an ActionError) outside io.allowed_roots
        if path.exists() and not action.overwrite:
            raise ActionError(f"{path} already exists; commit with overwrite=True to replace it")
        previous = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(card.to_yaml(self.mapping), encoding="utf-8")
        catalog = host.has_action("catalog.add_card")
        if catalog:
            try:
                host.dispatch({"type": "catalog.add_card", "path": str(path)})
            except Exception:  # boundary: another plugin failed; the file must not outlive a failed commit
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
                raise
        card_id = self.state.commit(net, card, str(path))
        host.publish("committed", {"net_id": net, "card_id": card_id, "path": str(path)})
        return {
            "card_id": card_id,
            "net_id": net,
            "path": str(path),
            "changes": len(card.changes),
            "warnings": len(warnings),
            "catalog": catalog,
        }

    def import_card(self, host: Host, action: ImportCard) -> dict[str, Any]:
        """Read a card (or an offline edit log) and apply its changes to the draft, all or nothing."""
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        path = host.writable(action.path)  # the allowed-roots check (the Host has no read-only variant)
        with as_action_errors():
            if not path.is_file():
                raise CardError(f"{action.path} is not a file")
            incoming = read_card_file(path, self.mapping)
            if not incoming.changes:
                raise CardError(f"{action.path} has no changes to import")
        total = len(incoming.changes)
        applied: list[Applied] = []
        for k, change in enumerate(incoming.changes, start=1):
            with as_action_errors(f"change {k} of {total}: "):
                check_card_shape(change, self.mapping)
                applied.append(apply_change(host, net, change))
        draft = self.state.draft(net)
        count = len(draft.steps)
        if not draft.steps and not draft.card.project:
            self.state.name(
                net,
                project=incoming.project,
                tags=incoming.tags,
                dependencies=incoming.dependencies,
                notes=incoming.notes,
            )
        for one in applied:
            count = self.state.add(net, one)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "imported": total, "project": self.state.draft(net).card.project, "count": count}
```

In `__init__.py`, import and export `CommitCard` and `ImportCard` as well (`uv run ruff check --fix
packages/netstead-cards` keeps `__all__` sorted).

- [ ] **Step 5: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass. Note `test_import_is_all_or_nothing`: the first change's mutation is undone by the Workbench
because the handler failed, and the draft is untouched because the handler adds to it only at the end.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
git add packages/netstead-cards/netstead_cards/actions.py packages/netstead-cards/netstead_cards/handlers.py \
  packages/netstead-cards/netstead_cards/__init__.py packages/netstead-cards/tests/test_commit_import.py
git commit -m "feat(cards): cards.commit (warnings, allowed roots, catalog) and cards.import"
```

---

### Task 11: `cards.preview` and `cards.close_preview`

A preview is a derived network (`host.derive`) with the change applied, made active so the map shows it; the base is
untouched. `cards.add_change` while a preview is active targets the base (Task 9's `target_network`).

**Files:**
- Modify: `packages/netstead-cards/netstead_cards/actions.py`, `handlers.py`, `__init__.py`
- Test: `packages/netstead-cards/tests/test_preview.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_preview.py`:

```python
"""cards.preview applies a change to a derived copy and shows it; the base network is untouched."""

import pytest
from netstead.workbench.actions import Select
from netstead.workbench.errors import ActionError
from netstead_cards.actions import AddChange, ClosePreview, PreviewChange
from netstead_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"
LANES_3 = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=3)})


def lanes(session, net_id, key=1):
    links = session.network(net_id).links_df()
    return links.loc[links.link_id == key, "lanes"].tolist()


def test_a_preview_is_a_derived_network_made_active(session):
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    preview = result["preview"]
    assert preview != NET and session.active == preview and result["base"] == NET
    assert lanes(session, preview) == [3] and lanes(session, NET) == [1] and session.network(NET).version == 0
    assert session.network(preview).derived_from == NET
    assert session.state()["plugins"]["cards"]["preview"] == {
        "net_id": preview,
        "base": NET,
        "describe": "roadway_property_change link 1: lanes → 3",
    }


def test_adding_while_previewing_changes_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    result = session.dispatch(AddChange(use_selection=True, change=LANES_3))
    assert result["net_id"] == NET and lanes(session, NET) == [3]


def test_close_preview_returns_to_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    preview = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert session.dispatch(ClosePreview()) == {"closed": preview, "active": NET}
    assert preview not in session.registry.ids() and session.state()["plugins"]["cards"]["preview"] is None
    with pytest.raises(ActionError, match="no preview is open"):
        session.dispatch(ClosePreview())


def test_a_second_preview_replaces_the_first(session):
    session.dispatch(Select(link_ids=[1]))
    first = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    second = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert first not in session.registry.ids() and set(session.registry.ids()) == {NET, second}


def test_a_failing_preview_leaves_nothing_behind(session):
    missing = RoadwayPropertyChange(facility=Selection(ids=[999999]), property_changes={"lanes": PropertyChange(set=3)})
    with pytest.raises(ActionError, match="not found"):
        session.dispatch(PreviewChange(change=missing))
    assert session.registry.ids() == [NET] and session.active == NET
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_preview.py -q`
Expected: FAIL with `ImportError: cannot import name 'ClosePreview'`.

- [ ] **Step 3: Implement**

In `actions.py`, `__all__` gains `"ClosePreview"` and `"PreviewChange"`; append:

```python
class PreviewChange(_ChangeAction):
    """Preview one roadway change on a copy of a network, shown on the map; the network itself is untouched.

    Takes the same fields as ``cards.add_change``. A second preview replaces the first; ``cards.close_preview``
    returns to the network.
    """

    type: Literal["cards.preview"] = "cards.preview"


class ClosePreview(BaseAction):
    """Close the open preview and show the network it previewed again."""

    type: Literal["cards.close_preview"] = "cards.close_preview"
    mutates: ClassVar[bool] = True  # it closes (unregisters) the preview network
```

In `handlers.py`: import `ClosePreview, PreviewChange` from `.actions` and `is_open` from `.flow`; `specs()` gains

```python
            ActionSpec(PreviewChange, self.preview),
            ActionSpec(ClosePreview, self.close_preview),
```

and append to `Handlers`:

```python
    def preview(self, host: Host, action: PreviewChange) -> dict[str, Any]:
        """Derive a copy of the base, apply the change to it, and make it active; then close any older preview."""
        refuse_transit(action.change)
        with as_action_errors():
            base = target_network(host, self.state, action.net_id)
            change, warnings = complete_change(host, base, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            text = describe(change)
            copy_id = host.derive(base, label=f"Preview: {text}"[:120], note=f"preview {change.type}")
            applied = apply_change(host, copy_id, change)
        older = self.state.preview  # closed last: a closed network is not brought back if a later step fails
        host.dispatch({"type": "set_active_network", "net_id": copy_id})
        if older is not None and is_open(host, older["net_id"]):
            host.dispatch({"type": "close_network", "net_id": older["net_id"]})
        self.state.preview = {"net_id": copy_id, "base": base, "describe": text}
        return {"preview": copy_id, "base": base, **applied.summary(), "warnings": [*warnings, *applied.warnings]}

    def close_preview(self, host: Host, action: ClosePreview) -> dict[str, Any]:
        """Close the preview network and make its base active again."""
        preview = self.state.preview
        if preview is None:
            raise ActionError("no preview is open")
        if is_open(host, preview["base"]):
            host.dispatch({"type": "set_active_network", "net_id": preview["base"]})
        if is_open(host, preview["net_id"]):
            host.dispatch({"type": "close_network", "net_id": preview["net_id"]})
        self.state.preview = None
        return {"closed": preview["net_id"], "active": host.active}
```

In `__init__.py`, export `ClosePreview` and `PreviewChange` too (then `uv run ruff check --fix packages/netstead-cards`).

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
git add packages/netstead-cards/netstead_cards/actions.py packages/netstead-cards/netstead_cards/handlers.py \
  packages/netstead-cards/netstead_cards/__init__.py packages/netstead-cards/tests/test_preview.py
git commit -m "feat(cards): cards.preview on a derived network, cards.close_preview"
```

---

### Task 12: The assistant drafts every `cards.*` Action for approval

The mechanism is core's and generic (Facts, item 10): with `assistant.plugins = ["cards"]` the plugin's Actions
become tools `cards__<name>`, and `mutates = True` makes the assistant draft them. This task pins that for the cards
plugin, end to end, with a scripted provider.

**Files:**
- Test: `packages/netstead-cards/tests/test_assistant.py`

- [ ] **Step 1: Write the test**

Create `packages/netstead-cards/tests/test_assistant.py`:

```python
"""With the plugin opted in, the assistant offers cards.* as tools and drafts them for the user to approve."""

from netstead.llm.types import Completion, ToolCall
from netstead.workbench.assistant.tools import tool_specs

NET = "leavenworth"


class Scripted:
    """A provider that replays Completions (local, so nothing counts against a budget)."""

    name, label, base_url = "ollama", "Scripted", "http://localhost:11434"

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def complete(self, request):
        self.requests.append(request)
        return self.replies.pop(0)

    def list_models(self):
        return []


def reply(*calls):
    return Completion(tool_calls=tuple(ToolCall(n, a) for n, a in calls), text="", input_tokens=100, output_tokens=20)


OPTED_IN = {"assistant.plugins": ["cards"], "assistant.compact_tools": "off"}
SELECT = ("select", {"utterance": "Benton Street", "facility": {"name": "Benton Street"}})
ADD = (
    "cards__add_change",
    {"use_selection": True, "change": {"type": "roadway_property_change", "property_changes": {"lanes": {"set": 2}}}},
)


def test_every_cards_action_is_a_drafted_tool(make_session):
    session = make_session(overrides=OPTED_IN)
    specs = {s.action_type: s for s in tool_specs(session) if s.plugin == "cards"}
    assert set(specs) == {
        "cards.add_change",
        "cards.undo_change",
        "cards.commit",
        "cards.import",
        "cards.preview",
        "cards.close_preview",
    }
    assert all(s.mutates for s in specs.values()) and specs["cards.add_change"].tool.name == "cards__add_change"


def test_a_card_change_from_the_assistant_waits_for_approval(make_session):
    session = make_session(provider=Scripted(reply(SELECT, ADD)), overrides=OPTED_IN)
    turn = session.assistant.ask("set Benton Street to 2 lanes in the card")
    assert [s["status"] for s in turn["steps"]] == ["done", "drafted"]
    assert session.network(NET).version == 0  # nothing applied yet
    out = session.assistant.approve(turn["draft_id"])
    assert out["steps"][1]["status"] == "done"
    assert session.history[-1].action["type"] == "cards.add_change" and session.history[-1].via == "assistant"
    assert session.state()["plugins"]["cards"]["drafts"][NET]["count"] == 1 and session.network(NET).version == 1


def test_without_the_opt_in_the_assistant_has_no_cards_tools(make_session):
    assert not [s for s in tool_specs(make_session()) if s.plugin == "cards"]
```

- [ ] **Step 2: Run it**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_assistant.py -q`
Expected: all pass with no code change (the mechanism is core's). If `test_every_cards_action_is_a_drafted_tool`
fails because a `cards.*` Action is missing from the tools, check `offerable()` in
`netstead/workbench/assistant/tools.py` and the Action's JSON schema; do not set `assistant = False` to make it pass.

- [ ] **Step 3: Lint, commit, open PR B**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
uv run --all-extras pytest packages -n auto -m "" -q
git add packages/netstead-cards/tests/test_assistant.py
git commit -m "test(cards): the assistant drafts cards.* Actions for approval"
```

Push `feat/cards-b` and open PR B ("Cards plugin B: Actions"), listing Tasks 9–12 and Facts items 5, 7, 8, 10, 11.

---
## PR C: UI and docs

Branch `feat/cards-c` from `main` once PR B has merged.

### Task 13: The `/form` and `/commit-check` routes

Read-only routes that tell the browser what a form should hold. Every change still goes through an Action.

**Files:**
- Create: `packages/netstead-cards/netstead_cards/routes.py`
- Modify: `packages/netstead-cards/netstead_cards/plugin.py`
- Test: `packages/netstead-cards/tests/test_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_routes.py`:

```python
"""GET /api/plugins/cards/form and /commit-check: what the panel's forms and the commit dialog show."""

import pytest
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead.workbench.actions import Select
from netstead_cards.actions import AddChange
from netstead_cards.types import PropertyChange, RoadwayPropertyChange, Selection


@pytest.fixture
def client(session):
    return TestClient(build_app(session))


def form(client, **params):
    response = client.get("/api/plugins/cards/form", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_property_change_form_on_a_pick(client, session):
    session.dispatch(Select(link_ids=[1, 2]))
    got = form(client, type="roadway_property_change", use_selection="true")
    assert got["supported"] is True
    assert got["targets"] == {"table": "link", "count": 2, "picked": True, "label": "2 picked links", "warnings": []}
    assert got["existing"]["facility_type"] == "residential" and got["existing"]["lanes"] == 1
    assert "free_speed" not in got["existing"]  # 40.0 and 40.23: not every target agrees
    columns = got["columns"]["properties"]
    assert "link_id" not in columns
    assert columns["lanes"] == {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "lanes (now 1)"}
    assert columns["free_speed"]["title"] == "free_speed (values differ)"
    assert columns["directed"]["anyOf"][0] == {"type": "boolean"}
    assert got["options"]["properties"]["mode"]["enum"] == ["set", "change"]


def test_a_query_selection_is_labelled_by_its_query(client, session):
    session.dispatch(Select(utterance="Benton Street"))
    got = form(client, type="roadway_property_change", use_selection="true")
    assert got["targets"]["picked"] is False and got["targets"]["count"] == 4
    assert got["targets"]["label"] == '4 links matching {"name": ["Benton Street"]}'


def test_deletion_and_addition_forms(client, session):
    session.dispatch(Select(link_ids=[2]))
    deletion = form(client, type="roadway_deletion", use_selection="true")
    assert deletion["options"]["properties"]["clean_nodes"]["type"] == "boolean"
    addition = form(client, type="roadway_addition")
    assert addition["next_ids"] == {"link_id": 340, "node_id": 122}
    assert {"link_id", "from_node_id", "to_node_id", "name", "facility_type", "lanes", "allowed_uses"} <= set(
        addition["link"]["required"]
    )
    assert addition["node"]["required"] == ["node_id", "x_coord", "y_coord"]


def test_transit_forms_say_not_yet_supported(client):
    got = form(client, type="transit_route_addition")
    assert got["type"] == "transit_route_addition" and got["supported"] is False
    assert got["message"].startswith("transit_route_addition is not yet supported: transit changes arrive")


def test_form_errors_are_400_or_404(client):
    assert client.get("/api/plugins/cards/form", params={"type": "pycode"}).status_code == 404
    response = client.get(
        "/api/plugins/cards/form", params={"type": "roadway_property_change", "use_selection": "true"}
    )
    assert response.status_code == 400 and "nothing is selected" in response.json()["detail"]


def test_commit_check_shows_warnings_problems_and_the_yaml(client, session):
    empty = client.get("/api/plugins/cards/commit-check").json()
    assert empty["changes"] == 0 and empty["problems"] == ["the draft card has no changes"]
    bad_end = RoadwayPropertyChange(
        facility=Selection(ids=[1]), property_changes={"from_node_id": PropertyChange(set=424242)}
    )
    session.dispatch(AddChange(change=bad_end))
    got = client.get("/api/plugins/cards/commit-check", params={"project": "Bad end"}).json()
    assert got["changes"] == 1 and got["problems"] == []
    assert [w["code"] for w in got["warnings"]] == ["edit.fk_missing"]
    assert got["yaml"].startswith("project: Bad end\n") and "A:\n" in got["yaml"]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_routes.py -q`
Expected: FAIL with 404s (the plugin has no router yet).

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/routes.py`:

```python
"""The plugin's HTTP routes (mounted at ``/api/plugins/cards``): what the browser's forms need to know.

``GET /form`` describes the form for a change type on the shared (or a saved) selection: the target, each column with
its current value, and the options. ``GET /commit-check`` returns the live edit warnings, the schema problems and the
draft as YAML, for the commit dialog. Both only read; every change is an Action.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from netstead.workbench.plugins import ActionError, Host

from .card import DraftCard
from .errors import CardError, not_supported
from .flow import selection_facility, target_network
from .lower import KEYS, rows_by_key
from .mapping import FieldMap
from .state import CardsState
from .types import ROADWAY_TYPES, TRANSIT_TYPES, Selection
from .validate import card_problems

__all__ = ["build_router", "column_schema", "current_values"]

_NOTE = {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Why (written in the card's notes)"}
PROPERTY_OPTIONS = {
    "type": "object",
    "properties": {
        "mode": {
            "title": "How",
            "enum": ["set", "change"],
            "default": "set",
            "description": "set a new value, or change a number by a delta",
        },
        "conflict": {
            "title": "If a current value differs when the card is applied",
            "enum": ["error", "warn", "skip"],
            "default": "error",
        },
        "note": _NOTE,
    },
}
DELETION_OPTIONS = {
    "type": "object",
    "properties": {
        "clean_nodes": {"type": "boolean", "title": "Also delete end nodes no other link uses", "default": False},
        "note": _NOTE,
    },
}
ADDITION_OPTIONS = {"type": "object", "properties": {"note": _NOTE}}
#: What an added link needs to be a valid ProjectCard record (A, B, model_link_id, name, roadway, lanes, and the
#: access flags, derived from allowed_uses).
LINK_REQUIRED = ("link_id", "from_node_id", "to_node_id", "name", "facility_type", "lanes", "allowed_uses")
NODE_REQUIRED = ("node_id", "x_coord", "y_coord")
_KINDS = {"i": "integer", "u": "integer", "f": "number", "b": "boolean"}


def _kind(series: Any) -> str:
    return _KINDS.get(series.dtype.kind, "string")


def current_values(frame: Any, pk: str, ids: list[Any]) -> tuple[dict[str, Any], set[str]]:
    """``(existing, mixed)``: the value every target shares, per column (when set), and the columns that differ."""
    columns = [c for c in frame.columns if c != pk]
    rows = rows_by_key(frame, pk, ids, columns)
    existing: dict[str, Any] = {}
    mixed: set[str] = set()
    for column in columns:
        distinct = {repr(r[column]): r[column] for r in rows.values()}
        if len(distinct) > 1:
            mixed.add(column)
        elif distinct and (value := next(iter(distinct.values()))) is not None:
            existing[column] = value
    return existing, mixed


def column_schema(frame: Any, skip: set[str], existing: dict[str, Any], mixed: set[str]) -> dict[str, Any]:
    """A JSON schema with one optional field per column (left blank: unchanged), titled with its current value."""
    properties = {}
    for column in frame.columns:
        if column in skip:
            continue
        if column in existing:
            title = f"{column} (now {existing[column]})"
        else:
            title = f"{column} (values differ)" if column in mixed else f"{column} (empty)"
        properties[column] = {"anyOf": [{"type": _kind(frame[column])}, {"type": "null"}], "title": title}
    return {"type": "object", "properties": properties}


def _record_schema(frame: Any, required: tuple[str, ...]) -> dict[str, Any]:
    need = [c for c in required if c in frame.columns]
    properties = {}
    for c in frame.columns:
        kind = {"type": _kind(frame[c])}
        properties[c] = {**kind, "title": c} if c in need else {"anyOf": [kind, {"type": "null"}], "title": c}
    return {"type": "object", "properties": properties, "required": need}


def _next_id(frame: Any, pk: str) -> int | None:
    if frame[pk].dtype.kind not in "iu" or not len(frame):
        return None
    return int(frame[pk].max()) + 1


def _label(selection: Selection) -> str:
    n = len(selection.target_ids())
    links = "link" if n == 1 else "links"
    if selection.ids is not None:
        return f"{n} picked {links}"
    query = {k: v for k, v in (selection.query or {}).get("links", {}).items() if k != "ignore_missing"}
    return f"{n} {links} matching {json.dumps(query, sort_keys=True)}"


def _form(host: Host, net: str, change_type: str, use_selection: bool, saved: str | None) -> dict[str, Any]:
    handle = host.network(net)
    if change_type == "roadway_addition":
        links, nodes = handle.links_df(), handle.nodes_df()
        return {
            "type": change_type,
            "supported": True,
            "link": _record_schema(links, LINK_REQUIRED),
            "node": _record_schema(nodes, NODE_REQUIRED),
            "options": ADDITION_OPTIONS,
            "next_ids": {"link_id": _next_id(links, "link_id"), "node_id": _next_id(nodes, "node_id")},
        }
    if not use_selection and saved is None:
        raise CardError(f"{change_type} acts on a selection: pass use_selection=true or saved=<name>")
    selection, warnings = selection_facility(host, net, use_selection=use_selection, saved=saved)
    ids = selection.target_ids()
    targets = {
        "table": selection.table,
        "count": len(ids),
        "picked": selection.ids is not None,
        "label": _label(selection),
        "warnings": warnings,
    }
    if change_type == "roadway_deletion":
        return {"type": change_type, "supported": True, "targets": targets, "options": DELETION_OPTIONS}
    frame = handle.links_df()
    existing, mixed = current_values(frame, KEYS["link"], ids)
    return {
        "type": change_type,
        "supported": True,
        "targets": targets,
        "columns": column_schema(frame, {KEYS["link"]}, existing, mixed),
        "options": PROPERTY_OPTIONS,
        "existing": existing,
    }


def build_router(host: Host, state: CardsState, mapping: FieldMap) -> APIRouter:
    """The plugin's router (``router(host)`` in :class:`~netstead.workbench.plugins.WorkbenchPlugin`)."""
    api = APIRouter()

    @api.get("/form")
    def form(
        type: str, net_id: str | None = None, use_selection: bool = False, saved: str | None = None
    ) -> dict[str, Any]:
        if type in TRANSIT_TYPES:
            return {"type": type, "supported": False, "message": str(not_supported(type))}
        if type not in ROADWAY_TYPES:
            raise HTTPException(404, f"no change type {type!r}")
        try:
            net = target_network(host, state, net_id)
            return _form(host, net, type, use_selection, saved)
        except (CardError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @api.get("/commit-check")
    def commit_check(net_id: str | None = None, project: str = "") -> dict[str, Any]:
        try:
            net = target_network(host, state, net_id)
        except (CardError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        draft = state.drafts.get(net)
        card = draft.card if draft is not None else DraftCard()
        if project:
            card = card.model_copy(update={"project": project})
        problems = card_problems(card.to_card(mapping)) if card.changes else ["the draft card has no changes"]
        return {
            "net_id": net,
            "changes": len(card.changes),
            "warnings": host.edit_warnings(net),
            "problems": problems,
            "yaml": card.to_yaml(mapping),
        }

    return api
```

In `plugin.py`, add `from .routes import build_router`, keep the mapping in a variable, and pass the router:

```python
def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin whose drafts live in this closure (one per Workbench session)."""
    state = CardsState()
    mapping = load_mapping()
    handlers = Handlers(state, mapping)
    return WorkbenchPlugin(
        id="cards",
        name="ProjectCards",
        version=__version__,
        requires_api=REQUIRES_API,
        actions=handlers.specs(),
        router=lambda host: build_router(host, state, mapping),
        state=lambda host: state.snapshot(lambda net_id: is_open(host, net_id)),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
git add packages/netstead-cards/netstead_cards/routes.py packages/netstead-cards/netstead_cards/plugin.py \
  packages/netstead-cards/tests/test_routes.py
git commit -m "feat(cards): /form and /commit-check routes for the panel"
```

---
### Task 14: Front-end rules and flow: `cardmodel.js` and `controller.js`

Both are DOM-free, so they run under node. `cardmodel.js` is pure (the change a form describes, the badge);
`controller.js` is the panel's flow over `wb` (which route to read, which Action to dispatch). `main.js` (Task 15)
only draws.

**Files:**
- Create: `packages/netstead-cards/netstead_cards/static/cardmodel.js`
- Create: `packages/netstead-cards/netstead_cards/static/controller.js`
- Test: `packages/netstead-cards/tests/test_frontend_model.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_frontend_model.py`:

```python
"""cardmodel.js and controller.js under node: the change a form describes, the badge, and the panel's flow."""

from pathlib import Path

import netstead_cards

STATIC = Path(netstead_cards.__file__).parent / "static"
MODEL = {"cardmodel.js": STATIC / "cardmodel.js"}
BOTH = {**MODEL, "controller.js": STATIC / "controller.js"}


def test_a_property_change_from_form_values(run_js):
    got = run_js(
        MODEL,
        """
import { propertyChange } from "./cardmodel.js";
const set = propertyChange({ lanes: 3, name: "", free_speed: null }, { mode: "set", note: "widened" }, { lanes: 1 });
const delta = propertyChange({ lanes: "1" }, { mode: "change", conflict: "warn" }, {});
let error = null;
try { propertyChange({ lanes: "" }, {}, {}); } catch (e) { error = e.message; }
console.log(JSON.stringify({ set, delta, error }));
""",
    )
    assert got["set"] == {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
        "note": "widened",
    }
    assert got["delta"] == {"type": "roadway_property_change", "property_changes": {"lanes": {"change": 1}}}
    assert got["error"] == "give a new value for at least one property"


def test_deletion_addition_and_the_action(run_js):
    got = run_js(
        MODEL,
        """
import { addition, changeAction, deletion, needsSelection } from "./cardmodel.js";
const del = deletion({ clean_nodes: true });
const add = addition({ link_id: 340, from_node_id: 1, to_node_id: 122, name: "", lanes: 1 },
                     { node_id: 122, x_coord: 1, y_coord: 2 });
const linkOnly = addition({ link_id: 340, from_node_id: 1, to_node_id: 2 }, {});
console.log(JSON.stringify({
  del, add, linkOnly,
  onSelection: changeAction("add_change", del),
  onSaved: changeAction("preview", del, { saved: "ski" }),
  addAction: changeAction("add_change", add),
  needs: ["roadway_property_change", "roadway_deletion", "roadway_addition"].map(needsSelection),
}));
""",
    )
    assert got["del"] == {"type": "roadway_deletion", "clean_nodes": True}
    assert got["add"]["links"] == [{"link_id": 340, "from_node_id": 1, "to_node_id": 122, "lanes": 1}]
    assert got["add"]["nodes"] == [{"node_id": 122, "x_coord": 1, "y_coord": 2}] and got["linkOnly"]["nodes"] == []
    assert got["onSelection"] == {"type": "cards.add_change", "change": got["del"], "use_selection": True}
    assert got["onSaved"] == {"type": "cards.preview", "change": got["del"], "saved_selection": "ski"}
    assert got["addAction"] == {"type": "cards.add_change", "change": got["add"]}
    assert got["needs"] == [True, True, False]


def test_the_badge_follows_the_draft_and_the_preview(run_js):
    got = run_js(
        MODEL,
        """
import { draftBadge, draftNet, warningLines } from "./cardmodel.js";
const state = { plugins: { cards: { drafts: { base: { count: 2, project: "", changes: [] } },
                                    preview: { net_id: "base-2", base: "base" } } } };
console.log(JSON.stringify({
  badge: draftBadge(state, "base"), none: draftBadge(state, "other"), net: draftNet(state, "base-2"),
  lines: warningLines([{ table: "link", key: 1, message: "to_node_id 424242 is not a node" }]),
}));
""",
    )
    assert got["badge"] == {"text": "2", "dirty": True, "title": "2 uncommitted card change(s)"}
    assert got["none"] is None and got["net"] == "base"
    assert got["lines"] == ["link 1: to_node_id 424242 is not a node"]


CONTROLLER_HARNESS = """
import { createController } from "./controller.js";
const calls = [];
const forms = {
  roadway_property_change: { supported: true, existing: { lanes: 1 }, targets: { count: 2 } },
  transit_property_change: { supported: false, message: "transit_property_change is not yet supported" },
};
const wb = {
  api: {
    get: async path => { calls.push(["get", path]);
      if (path.startsWith("/commit-check")) return { warnings: [{ code: "edit.fk_missing" }], problems: [], yaml: "" };
      return forms[new URLSearchParams(path.split("?")[1]).get("type")]; },
    dispatch: async action => { calls.push(["dispatch", action]);
      return action.type === "cards.preview" ? { preview: "net-2" } : { count: 1, describe: "x" }; },
  },
  toast: message => calls.push(["toast", message]),
};
const ctl = createController(wb);
const values = { columns: { lanes: 3 }, options: { mode: "set" } };
await ctl.start("transit_property_change");
await ctl.start("roadway_property_change");
await ctl.preview(values);
const afterPreview = ctl.state.preview;
await ctl.add(values);
await ctl.check({ project: "Lanes" });
await ctl.commit({ project: "Lanes", path: "cards/lanes.yml", tags: [] });
console.log(JSON.stringify({ calls, afterPreview, compose: ctl.state.compose, preview: ctl.state.preview }));
"""


def test_the_controller_reads_the_form_then_dispatches_actions(run_js):
    got = run_js(BOTH, CONTROLLER_HARNESS)
    calls = got["calls"]
    assert calls[0] == ["get", "/form?type=transit_property_change"]
    assert calls[1] == ["toast", "transit_property_change is not yet supported"]
    assert calls[2] == ["get", "/form?type=roadway_property_change&use_selection=true"]
    change = {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
    }
    assert calls[3] == ["dispatch", {"type": "cards.preview", "change": change, "use_selection": True}]
    assert got["afterPreview"] == {"preview": "net-2"}
    assert calls[4] == ["dispatch", {"type": "cards.add_change", "change": change, "use_selection": True}]
    assert calls[5] == ["dispatch", {"type": "cards.close_preview"}]  # adding closes the preview
    assert calls[6] == ["get", "/commit-check?project=Lanes"]
    assert calls[7] == [
        "dispatch",
        {"type": "cards.commit", "project": "Lanes", "path": "cards/lanes.yml", "tags": [], "confirm_warnings": True},
    ]  # the dialog showed a warning: committing confirms it
    assert got["compose"] is None and got["preview"] is None
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_frontend_model.py -q`
Expected: FAIL (`FileNotFoundError` copying `static/cardmodel.js`), or SKIP if node is not installed: install node
(it is a dev prerequisite of the Workbench's own JS tests) and run again.

- [ ] **Step 3: Implement**

Create `packages/netstead-cards/netstead_cards/static/cardmodel.js`:

```js
// Pure rules for the cards panel: change types, the change a form describes, the draft badge. No DOM, no wb, so
// they are unit-tested under node (tests/test_frontend_model.py).

export const ROADWAY_TYPES = ["roadway_property_change", "roadway_deletion", "roadway_addition"];
export const TRANSIT_TYPES = ["transit_property_change", "transit_routing_change", "transit_route_addition",
  "transit_service_deletion"];
export const TYPE_TITLES = {
  roadway_property_change: "Property change",
  roadway_deletion: "Deletion",
  roadway_addition: "Addition",
  transit_property_change: "Transit property change",
  transit_routing_change: "Transit routing change",
  transit_route_addition: "Transit route addition",
  transit_service_deletion: "Transit service deletion",
};

// Property changes and deletions act on the selection; an addition does not.
export const needsSelection = type => type === "roadway_property_change" || type === "roadway_deletion";

const filled = v => v !== undefined && v !== null && v !== "";
const withNote = (change, options) => (filled(options && options.note) ? { ...change, note: options.note } : change);

// The roadway_property_change a form describes. `columns` maps a column to its new value (or delta); `options` is
// {mode: "set" | "change", conflict, note}; `existing` is each column's current value when every selected record
// agrees. It is written as the change's `existing`, so the card fails loudly if the network has moved on.
export function propertyChange(columns, options = {}, existing = {}) {
  const mode = options.mode === "change" ? "change" : "set";
  const property_changes = {};
  for (const [column, value] of Object.entries(columns || {})) {
    if (!filled(value)) continue;
    const pc = mode === "change" ? { change: Number(value) } : { set: value };
    if (filled(existing[column])) {
      pc.existing = existing[column];
      pc.existing_value_conflict = options.conflict || "error";
    }
    property_changes[column] = pc;
  }
  if (!Object.keys(property_changes).length) throw new Error("give a new value for at least one property");
  return withNote({ type: "roadway_property_change", property_changes }, options);
}

export function deletion(options = {}) {
  return withNote({ type: "roadway_deletion", clean_nodes: Boolean(options.clean_nodes) }, options);
}

// A new link, and a new end node when the node form has an id.
export function addition(link, node, options = {}) {
  const keep = record => Object.fromEntries(Object.entries(record || {}).filter(([, v]) => filled(v)));
  const nodes = node && filled(node.node_id) ? [keep(node)] : [];
  return withNote({ type: "roadway_addition", links: [keep(link)], nodes }, options);
}

// The Action for a change (`kind`: "add_change" or "preview"), on the shared selection or a saved one when it needs
// one.
export function changeAction(kind, change, { saved = null } = {}) {
  const action = { type: `cards.${kind}`, change };
  if (needsSelection(change.type)) {
    if (saved) action.saved_selection = saved;
    else action.use_selection = true;
  }
  return action;
}

const cards = state => (state && state.plugins && state.plugins.cards) || {};

// The network a draft belongs to: an open preview stands for the network it previews.
export function draftNet(state, netId) {
  const preview = cards(state).preview;
  return preview && preview.net_id === netId ? preview.base : netId;
}

export const draftOf = (state, netId) => (cards(state).drafts || {})[netId] || null;

// The panel's badge: the number of uncommitted changes, marked dirty (a dot on the tab and its workspace).
export function draftBadge(state, netId) {
  const draft = draftOf(state, netId);
  if (!draft || !draft.count) return null;
  return { text: String(draft.count), dirty: true, title: `${draft.count} uncommitted card change(s)` };
}

export const warningLines = warnings => (warnings || []).map(w => `${w.table} ${w.key}: ${w.message}`);
```

Create `packages/netstead-cards/netstead_cards/static/controller.js`:

```js
// The cards panel's flow without the DOM: which route each step reads and which Action it dispatches. main.js draws
// it; tests/test_frontend_model.py runs it under node with a stub `wb`.
import { addition, changeAction, deletion, needsSelection, propertyChange } from "./cardmodel.js";

const query = params => Object.entries(params)
  .filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  .join("&");

export function createController(wb) {
  const state = { compose: null, preview: null, check: null };

  // Open the form for a change type: the plugin's /form route says what the selection holds now.
  async function start(type, { saved = null } = {}) {
    const params = { type, saved };
    if (needsSelection(type) && !saved) params.use_selection = "true";
    const form = await wb.api.get(`/form?${query(params)}`);
    if (!form.supported) {
      state.compose = null;
      wb.toast(form.message);
      return null;
    }
    state.compose = { type, form, saved };
    return form;
  }

  // The change the open form describes. `values` is {columns, options, link, node}: each form's value().
  function changeFrom(values) {
    const { type, form } = state.compose;
    if (type === "roadway_property_change") return propertyChange(values.columns, values.options, form.existing);
    if (type === "roadway_deletion") return deletion(values.options);
    return addition(values.link, values.node, values.options);
  }

  const dispatchChange = (kind, values) =>
    wb.api.dispatch(changeAction(kind, changeFrom(values), { saved: state.compose.saved }));

  async function preview(values) {
    state.preview = await dispatchChange("preview", values);
    return state.preview;
  }

  async function closePreview() {
    await wb.api.dispatch({ type: "cards.close_preview" });
    state.preview = null;
  }

  async function add(values) {
    const result = await dispatchChange("add_change", values);
    if (state.preview) await closePreview();
    state.compose = null;
    return result;
  }

  async function cancel() {
    state.compose = null;
    if (state.preview) await closePreview();
  }

  const undo = () => wb.api.dispatch({ type: "cards.undo_change" });

  // Before committing: the live edit warnings, the schema problems and the card as YAML, for the commit dialog.
  async function check(fields) {
    state.check = await wb.api.get(`/commit-check?${query({ project: fields.project })}`);
    return state.check;
  }

  // Committing after the dialog showed warnings is the confirmation the server asks for.
  function commit(fields) {
    const confirm = Boolean(state.check && state.check.warnings.length);
    state.check = null;
    return wb.api.dispatch({ type: "cards.commit", ...fields, confirm_warnings: confirm });
  }

  const importCard = path => wb.api.dispatch({ type: "cards.import", path });

  return { state, start, changeFrom, preview, closePreview, add, cancel, undo, check, commit, importCard };
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_frontend_model.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead-cards/netstead_cards/static/cardmodel.js packages/netstead-cards/netstead_cards/static/controller.js \
  packages/netstead-cards/tests/test_frontend_model.py
git commit -m "feat(cards): front-end rules (cardmodel.js) and panel flow (controller.js)"
```

---

### Task 15: The front end: the "Card" panel and "New change ▸ …" commands

`main.js` registers everything through `wb` (no core imports, no reach into core's DOM): one dock panel in every
workspace, with a dirty badge; a "New change ▸ {type}" command per roadway type on the selection menus and in the
palette; the transit types in the palette, answering "not yet supported"; "Undo the draft card's last change".
The panel composes a change with `wb.schemaForm`, previews it, adds it, lists the draft, and commits through a
`<dialog>` that shows the live edit warnings and the card's YAML.

**Files:**
- Create: `packages/netstead-cards/netstead_cards/static/main.js`
- Modify: `packages/netstead-cards/netstead_cards/plugin.py`
- Test: `packages/netstead-cards/tests/test_frontend_main.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead-cards/tests/test_frontend_main.py`:

```python
"""main.js under node, against the Workbench's real wb factory and slots (registration, whens, badge, commands)."""

from pathlib import Path

import netstead_cards
from netstead.workbench.server import STATIC_DIR

STATIC = Path(netstead_cards.__file__).parent / "static"
CORE = ("wbhost.js", "slots.js", "layers.js", "store.js", "hub.js")
FILES = {
    **{name: STATIC_DIR / "js" / name for name in CORE},
    **{name: STATIC / name for name in ("main.js", "cardmodel.js", "controller.js")},
}

HARNESS = """
import { activate } from "./main.js";
import { createWb } from "./wbhost.js";
import { createSlots } from "./slots.js";
import { createLayerRegistry } from "./layers.js";
import { activeSelection, createStore } from "./store.js";
import { createHub } from "./hub.js";

const slots = createSlots(), hub = createHub(), calls = [], panels = [];
const server = { networks: [], active: "base", selection: null,
                 plugins: { cards: { drafts: { base: { count: 2, project: "", changes: [] } }, preview: null } } };
const store = createStore({ server });
const deps = {
  hostApi: "1.5", store, activeSelection, events: hub, actionTypes: new Set(), actionSchema: () => null,
  frozenCopy: x => JSON.parse(JSON.stringify(x)),
  getJSON: async p => { calls.push(["get", p]);
    return p.includes("transit") ? { supported: false, message: "transit_routing_change is not yet supported" }
                                 : { supported: true, existing: {}, targets: { label: "2 picked links" } }; },
  postJSON: async () => ({}), fetch: async () => ({}),
  dispatch: async a => { calls.push(["dispatch", a.type]); return {}; },
  addCommand: (owner, spec) => slots.addCommand(owner, spec), layers: createLayerRegistry(),
  dock: { addWorkspace: (o, s) => slots.addWorkspace(o, s),
          addPanel: (o, s) => { panels.push(s); return { dispose: slots.addPanel(o, s), refreshBadge() {} }; },
          showPanel: id => calls.push(["show", id]) },
  schemaForm: () => ({ value: () => ({}), errors: () => [], set() {}, focus() {} }),
  toast: m => calls.push(["toast", m]),
  onError: (id, phase, e) => calls.push(["error", id, phase, String(e.message || e)]),
};
const { wb } = createWb("cards", deps);
activate(wb);
const commands = slots.commands.list();
const byId = Object.fromEntries(commands.map(c => [c.id, c]));
const none = { selectionCount: 0, state: server, network: { id: "base" } };
const two = { selectionCount: 2, state: server, network: { id: "base" } };
await byId["cards.new_roadway_property_change"].run(two);
await byId["cards.new_transit_routing_change"].run(two);
await byId["cards.undo"].run(two);
console.log(JSON.stringify({
  panels: slots.panels.list().map(p => [p.id, p.workspace, p.title]),
  commands: commands.map(c => [c.id, c.group || null, c.contexts]),
  whenNone: commands.map(c => (c.when ? c.when(none) : true)),
  whenTwo: commands.map(c => (c.when ? c.when(two) : true)),
  badge: panels[0].badge({ state: server, network: { id: "base" } }),
  calls,
}));
"""


def test_main_registers_the_panel_and_the_commands(run_js):
    got = run_js(FILES, HARNESS)
    assert got["panels"] == [["cards.draft", "*", "Card"]]
    ids = [c[0] for c in got["commands"]]
    assert ids == [
        "cards.new_roadway_property_change",
        "cards.new_roadway_deletion",
        "cards.new_roadway_addition",
        "cards.new_transit_property_change",
        "cards.new_transit_routing_change",
        "cards.new_transit_route_addition",
        "cards.new_transit_service_deletion",
        "cards.undo",
    ]
    groups_contexts = {c[0]: (c[1], c[2]) for c in got["commands"]}
    assert groups_contexts["cards.new_roadway_property_change"] == ("New change", ["selection", "palette"])
    assert groups_contexts["cards.new_roadway_addition"] == ("New change", ["palette"])
    assert groups_contexts["cards.new_transit_property_change"] == ("New change", ["palette"])
    assert got["whenNone"][:3] == [False, False, True]  # property change and deletion need a selection
    assert got["whenTwo"][:3] == [True, True, True]
    assert got["badge"] == {"text": "2", "dirty": True, "title": "2 uncommitted card change(s)"}


def test_commands_open_the_form_or_say_not_yet_supported(run_js):
    calls = run_js(FILES, HARNESS)["calls"]
    assert calls[0] == ["get", "/api/plugins/cards/form?type=roadway_property_change&use_selection=true"]
    assert calls[1] == ["show", "cards.draft"]
    assert calls[2] == ["get", "/api/plugins/cards/form?type=transit_routing_change"]
    assert calls[3] == ["toast", "cards: transit_routing_change is not yet supported"]
    assert calls[4] == ["dispatch", "cards.undo_change"]
    assert not [c for c in calls if c[0] == "error"]
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --all-extras pytest packages/netstead-cards/tests/test_frontend_main.py -q`
Expected: FAIL (`main.js` does not exist).

- [ ] **Step 3: Implement `main.js`**

Create `packages/netstead-cards/netstead_cards/static/main.js`:

```js
// The cards plugin's front end: a "Card" dock panel (compose a change, preview it, the draft card's changes, commit
// and import) and "New change ▸ …" commands. Everything goes through `wb` (see the cookbook page "Write a Workbench
// plugin"); the flow lives in controller.js and the rules in cardmodel.js, both tested under node.
import { ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, draftBadge, draftNet, draftOf, needsSelection, warningLines }
  from "./cardmodel.js";
import { createController } from "./controller.js";

const PANEL = "cards.draft";

// A DOM element: `props.text` is its text (never markup), `props.onclick` its handler, other props attributes.
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") el.textContent = value;
    else if (key === "onclick") el.onclick = value;
    else el.setAttribute(key, value);
  }
  for (const child of children) el.append(child);
  return el;
}

export function activate(wb) {
  const ctl = createController(wb);
  let view = null;
  const currentNet = state => draftNet(state, state.active);

  const panel = wb.registerPanel({
    workspace: "*",
    id: PANEL,
    title: "Card",
    order: 40,
    badge: ctx => draftBadge(ctx.state, draftNet(ctx.state, ctx.network && ctx.network.id)),
    render(el) {
      view = createView(wb, ctl, el, () => currentNet(wb.store.get()));
      view.draw();
    },
    onShow: () => view && view.draw(),
  });

  async function compose(type) {
    const form = await ctl.start(type);
    if (!form) return;
    wb.showPanel(PANEL);
    if (view) view.draw();
  }

  for (const type of ROADWAY_TYPES) {
    wb.registerCommand({
      id: `cards.new_${type}`,
      title: TYPE_TITLES[type],
      group: "New change",
      contexts: needsSelection(type) ? ["selection", "palette"] : ["palette"],
      when: ctx => !needsSelection(type) || ctx.selectionCount > 0,
      run: () => compose(type),
    });
  }
  for (const type of TRANSIT_TYPES) {
    wb.registerCommand({
      id: `cards.new_${type}`,
      title: `${TYPE_TITLES[type]} (not yet supported)`,
      group: "New change",
      contexts: ["palette"],
      run: () => compose(type),
    });
  }
  wb.registerCommand({
    id: "cards.undo",
    title: "Undo the draft card's last change",
    group: "Card",
    contexts: ["palette"],
    when: ctx => Boolean(draftOf(ctx.state, draftNet(ctx.state, ctx.network && ctx.network.id))),
    run: () => ctl.undo(),
  });

  wb.store.subscribe(["plugins.cards", "active"], () => {
    panel.refreshBadge();
    if (view) view.draw();
  });
}

function createView(wb, ctl, el, netId) {
  const composeBox = h("section");
  const draftBox = h("section");
  const commitBox = h("section");
  const status = h("p", { class: "muted", role: "status" });
  const dialog = h("dialog", { "aria-label": "Commit the draft card" });
  el.append(composeBox, draftBox, commitBox, status, dialog);
  const say = text => { status.textContent = text; };
  const guard = fn => async () => {
    try { await fn(); } catch (e) { say(e.message); }
  };

  function drawCompose() {
    composeBox.replaceChildren();
    const c = ctl.state.compose;
    if (!c) return;
    composeBox.append(h("h4", { text: `New change: ${TYPE_TITLES[c.type]}` }));
    if (c.form.targets) composeBox.append(h("p", { class: "muted", text: c.form.targets.label }));
    const forms = {};
    for (const key of ["columns", "link", "node", "options"]) {
      if (!c.form[key]) continue;
      if (key === "node") {
        const next = c.form.next_ids && c.form.next_ids.node_id;
        composeBox.append(h("p", { class: "muted", text: `New end node (optional; next free id: ${next})` }));
      }
      const slot = h("div");
      composeBox.append(slot);
      const start = key === "link" && c.form.next_ids ? { link_id: c.form.next_ids.link_id } : {};
      forms[key] = wb.schemaForm(slot, c.form[key], start);
    }
    const values = () => Object.fromEntries(Object.entries(forms).map(([k, f]) => [k, f.value()]));
    const run = step => guard(async () => {
      const errors = Object.values(forms).flatMap(f => f.errors());
      if (errors.length) { say(errors.join("; ")); return; }
      say(await step(values()));
      draw();
    });
    composeBox.append(
      h("button", { class: "mini", onclick: run(async v => `Previewing on ${(await ctl.preview(v)).preview}`) },
        "Preview"),
      " ",
      h("button", { class: "mini", onclick: run(async v => `Added: ${(await ctl.add(v)).describe}`) }, "Add to card"),
      " ",
      h("button", { class: "ghost mini", onclick: guard(async () => { await ctl.cancel(); draw(); }) }, "Cancel"),
    );
  }

  function drawDraft() {
    draftBox.replaceChildren();
    const state = wb.store.get();
    const preview = state.plugins && state.plugins.cards && state.plugins.cards.preview;
    if (preview) {
      draftBox.append(h("p", { text: `Previewing ${preview.describe} on ${preview.net_id}. ` },
        h("button", { class: "ghost mini", onclick: guard(async () => { await ctl.closePreview(); draw(); }) },
          "Back to the network")));
    }
    const draft = draftOf(state, netId());
    if (!draft) {
      draftBox.append(h("p", { class: "muted",
        text: "No draft card for this network yet. Select links, then right-click: New change ▸ …" }));
      return;
    }
    draftBox.append(h("h4", { text: `Draft card${draft.project ? `: ${draft.project}` : ""} (${draft.count})` }));
    const list = h("ol");
    for (const change of draft.changes) list.append(h("li", { text: change.describe }));
    draftBox.append(list,
      h("button", { class: "mini", onclick: guard(async () => { await ctl.undo(); say("Undid the last change"); }) },
        "Undo last change"));
  }

  async function openDialog(fields) {
    if (!fields.project || !fields.path) { say("Give the card a project name and a file"); return; }
    const check = await ctl.check(fields);
    const lines = warningLines(check.warnings);
    dialog.replaceChildren(
      h("h4", { text: `Commit "${fields.project}"` }),
      h("p", { class: lines.length ? "" : "muted",
        text: lines.length ? `${lines.length} live edit warning(s). Committing records the card as it is.`
                           : "No live edit warnings." }),
      h("ul", {}, ...lines.map(line => h("li", { text: line }))),
      h("p", { text: check.problems.length ? `Not a valid ProjectCard yet: ${check.problems.join("; ")}` : "" }),
      h("pre", { text: check.yaml }),
      h("button", { class: "mini", onclick: guard(async () => {
        const result = await ctl.commit(fields);
        dialog.close();
        say(`Committed ${result.card_id} to ${result.path}`);
      }) }, lines.length ? "Commit anyway" : "Commit"),
      " ",
      h("button", { class: "ghost mini", onclick: () => dialog.close() }, "Cancel"),
    );
    dialog.showModal();
  }

  // Drawn once: redrawing on every state change would wipe what the user is typing.
  function drawCommit() {
    const project = h("input", { type: "text", placeholder: "Project name", "aria-label": "Project name" });
    const tags = h("input", { type: "text", placeholder: "Tags, comma separated", "aria-label": "Tags" });
    const path = h("input", { type: "text", placeholder: "cards/my-project.yml", "aria-label": "Card file" });
    const overwrite = h("input", { type: "checkbox", "aria-label": "Replace an existing file" });
    const source = h("input", { type: "text", placeholder: "path/to/card.yml", "aria-label": "Card to import" });
    const fields = () => ({
      project: project.value.trim(),
      path: path.value.trim(),
      overwrite: overwrite.checked,
      tags: tags.value.split(",").map(t => t.trim()).filter(Boolean),
    });
    commitBox.append(
      h("h4", { text: "Commit" }), project, tags, path, h("label", {}, overwrite, " replace an existing file"),
      h("button", { class: "mini", onclick: guard(() => openDialog(fields())) }, "Commit…"),
      h("h4", { text: "Import a card" }), source,
      h("button", { class: "mini", onclick: guard(async () => {
        say(`Imported ${(await ctl.importCard(source.value.trim())).imported} change(s)`);
      }) }, "Import"),
    );
  }

  function draw() {
    drawCompose();
    drawDraft();
  }

  drawCommit();
  return { draw };
}
```

In `plugin.py`, add `from pathlib import Path`, a module constant
`STATIC = Path(__file__).parent / "static"` (with `#: The front end: main.js, controller.js, cardmodel.js.`), and
`static_dir=STATIC,` to the `WorkbenchPlugin(...)` call (after `router=`). `frontend` stays the default `main.js`.

Add a Python test in `packages/netstead-cards/tests/test_plugin_loads.py`:

```python
def test_the_front_end_is_served(make_session):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    session = make_session(open_network=False)
    (status,) = [s for s in session.plugin_status if s.id == "cards"]
    assert status.frontend == "/plugins/cards/main.js"
    client = TestClient(build_app(session))
    for name in ("main.js", "controller.js", "cardmodel.js"):
        assert client.get(f"/plugins/cards/{name}").status_code == 200
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead-cards/tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff format packages/netstead-cards && uv run ruff check . && uv run ruff format --check .
git add packages/netstead-cards/netstead_cards/static/main.js packages/netstead-cards/netstead_cards/plugin.py \
  packages/netstead-cards/tests/test_frontend_main.py packages/netstead-cards/tests/test_plugin_loads.py
git commit -m "feat(cards): the Card dock panel and New change commands"
```

---
### Task 16: Docs: cookbook page, indexes, contributing, changelogs

**Files:**
- Create: `packages/netstead/docs/cookbook/workbench-cards.md`
- Modify: `packages/netstead/docs/cookbook/index.md`, `packages/netstead/mkdocs.yml`, `CONTRIBUTING.md`
- Modify: `packages/netstead/tests/test_documented_api_contract.py` (two provenance keys are not attribute paths)
- Modify: `docs/design/README.md`, `docs/design/2026-10-07-cards-plugin-scope.md` (status line only),
  `packages/netstead-cards/CHANGELOG.md`, `packages/netstead-cards/README.md`

- [ ] **Step 1: Write the cookbook page**

Create `packages/netstead/docs/cookbook/workbench-cards.md` (follow `packages/netstead/docs/_page-style-guide.md`):

````markdown
---
title: Author ProjectCards in the Workbench
audience: users
kind: howto
summary: Turn Workbench edits into network_wrangler ProjectCards with the cards plugin: select, change, preview, commit.
---

# Author ProjectCards in the Workbench

## When to use this

You edit a GMNS network in the [Workbench](workbench.md) and want the edits as a
[ProjectCard](https://github.com/network-wrangler/projectcard): a YAML file network_wrangler applies to its own
network, reviewed like code. The `cards` plugin (the `netstead-cards` package) adds that to the Workbench.

## Install

In the netstead workspace, `uv sync --all-packages` installs `netstead-cards` next to netstead. Elsewhere,
`pip install netstead-cards`. Then run `uv run netstead app`; **Settings → Plugins** lists *ProjectCards* as loaded.

## Make a card

1. **Select** links: click them, box-select, or type a request such as "Benton Street" in the command bar.
2. **Pick a change.** Right-click the selection (or open **Selection actions ▾**) and choose
   **New change ▸ Property change** or **New change ▸ Deletion**. **New change ▸ Addition** is in the command
   palette (**Ctrl+K** / **⌘K**): it adds a link, and optionally a new end node, without a selection.
3. **Fill the form** in the **Card** panel. Each column shows its current value when every selected link agrees
   ("lanes (now 1)"). Leave a field blank to leave it alone. **How** is *set* (a new value) or *change* (add a number
   to each link's current value). When the selected links agree on a value, the card records it as `existing`, and
   **If a current value differs** says what network_wrangler does when the network has moved on: `error` (stop),
   `warn` or `skip`.
4. **Preview** applies the change to a copy of the network and shows the copy on the map. The network itself is
   untouched. **Back to the network** closes the copy.
5. **Add to card** applies the change to the network and lists it in the draft card. Core's **Edits** panel lists it
   too, with `cards` as its source, and the live checks run as for any edit. **Undo last change** takes back the
   draft's newest change (core's Undo leaves plugin changes alone, and the plugin's undo leaves yours alone).
6. **Commit**: give the card a project name, tags and a file inside `io.allowed_roots`, then **Commit…**. The dialog
   shows the live edit warnings and the card's YAML. With warnings, the button reads **Commit anyway**. The card is
   checked with `projectcard`'s own validator before it is written, and a new, empty draft starts.

**Import a card** applies a card file to the draft, all or nothing. It also reads the edit log that the offline
validation report's "Fix locally" editor downloads.

## What goes in the card

| You did | The card says |
|---|---|
| Clicked links | `facility: {links: {model_link_id: [...], ignore_missing: false}}`: a fix aimed at those links fails loudly if one is gone. |
| Selected by a request ("Benton Street") | The query (`links: {name: [Benton Street], ignore_missing: true}`), plus a `netstead.resolved[<i>]: model_link_id=[...]` line in the card's `notes` recording the links it matched. |
| Deleted links | `roadway_deletion` by id, `ignore_missing: false`; *Also delete end nodes* is `clean_nodes`. |
| Added a link | `roadway_addition` in network_wrangler's names (`A`, `B`, `roadway`, `model_link_id`), with `walk_access` / `bike_access` / `drive_access` derived from `allowed_uses`. |
| Wrote a note | `netstead.note[<i>]: …` in the card's `notes` (a change itself has no notes field). |

GMNS names map to network_wrangler's through a maintained data file, `netstead_cards/mappings/gmns_to_wrangler.yaml`.
A column it doesn't list keeps its GMNS name.

## Limits

- A property can be set to one number or text. A card cannot set a property to empty: clear a cell with a core edit
  instead. `true` / `false` are written as `1` / `0`.
- A request that names both a street and a route number is not a valid ProjectCard selector, so the plugin refuses
  it when you add the change; select by one of them.
- Transit change types are listed, and answer "not yet supported" until the Workbench edits transit.
- Importing a card that selects by a query works only for cards the plugin wrote (it applies the query to the links
  recorded in `notes`). Re-resolving a query on another network version is a later feature.

## From Python and the assistant

Every step is a recorded Action, so "Copy as Python" replays it:

<!-- doctest: skip -->
```python
from netstead.workbench import Select
from netstead_cards import AddChange, CommitCard, PropertyChange, RoadwayPropertyChange

app.do(Select(utterance="Benton Street"))
app.do(AddChange(use_selection=True,
                 change=RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)})))
app.do(CommitCard(path="cards/benton-lanes.yml", project="Benton Street lanes", confirm_warnings=True))
````

- [ ] **Step 2: Index it**

- `packages/netstead/docs/cookbook/index.md`: after the "Write a Workbench plugin" bullet, add
  `* [Author ProjectCards in the Workbench](workbench-cards.md) — the cards plugin: select, change, preview and commit network_wrangler project cards.`
- `packages/netstead/mkdocs.yml`: after `- Write a Workbench plugin: cookbook/workbench-plugins.md` add
  `      - Author ProjectCards: cookbook/workbench-cards.md` (same indentation).
- `packages/netstead/docs/cookbook/workbench-plugins.md`: in "When to use this", link "authoring project cards" to
  `workbench-cards.md`, and add after the hello walk-through's first paragraph: "For a full plugin built on the same
  API (Actions with undo, routes, a dock panel, schema forms, a derived-network preview), read the `netstead-cards`
  package in `packages/netstead-cards`."

- [ ] **Step 3: CONTRIBUTING.md**

In "Running tests": change the first sentence to "Tests live in `packages/corral/tests`, `packages/netstead/tests`
and `packages/netstead-cards/tests` …", and add a row to the per-domain table:

```markdown
| `netstead-cards` (the cards plugin) | `packages/netstead-cards/tests` |
```

- [ ] **Step 4: Design index and changelog**

In `docs/design/README.md`:
- in "Implementation plans", add
  `| [Cards plugin (ProjectCard authoring)](2026-10-19-cards-plugin-plan.md) | cards scope | implemented | #<A>, #<B>, #<C> |`
  (the three PR numbers);
- in "Scopes", the cards row becomes
  `| [Cards plugin (ProjectCard authoring)](2026-10-07-cards-plugin-scope.md) | 10-07 | implemented | #<A>, #<B>, #<C> | see the scope's "Follow-ups" |`;
- move the plan out of "In flight" if it was listed there.

In `docs/design/2026-10-07-cards-plugin-scope.md` and this plan, set the status line to
`Status: **implemented** (#<A>, #<B>, #<C>) · …`.

In `packages/netstead-cards/CHANGELOG.md`, under `## [Unreleased]`:

```markdown
### Added

- The Workbench plugin `cards`: ProjectCard authoring. A **Card** dock panel and
  **New change ▸ Property change / Deletion / Addition** commands; a preview on a
  derived network; per-network draft cards with undo; commit to a ProjectCard
  YAML file validated by `projectcard` (live edit warnings confirmed first; the
  card is also added to a `catalog` plugin when one is installed); import of
  cards and of the offline report's edit log.
- Actions `cards.add_change`, `cards.undo_change`, `cards.preview`,
  `cards.close_preview`, `cards.commit`, `cards.import` (recorded, replayable,
  drafted for approval by the assistant).
- Routes `GET /api/plugins/cards/form` and `/commit-check`.
- Requires netstead with plugin Host API 1.5 and `projectcard>=0.3.3,<0.4`.
```

- [ ] **Step 5: Tell the documented-API test about the provenance keys**

`test_documented_api_contract.py` resolves every `netstead.<name>` it finds in the docs; the page's
`netstead.resolved[<i>]` and `netstead.note[<i>]` are keys in a card's notes text. In
`packages/netstead/tests/test_documented_api_contract.py`, add to `_KNOWN_FICTIONAL`, after `"corral.quality.rules",`:

```python
    # The cards plugin's provenance keys in a ProjectCard's `notes` text (`netstead.resolved[<i>]: ...`,
    # `netstead.note[<i>]: ...`; cookbook/workbench-cards.md), not Python attribute paths.
    "netstead.resolved",
    "netstead.note",
```

Without it the default suite fails with `test_documented_symbol_exists[netstead.note-]` and `[netstead.resolved-]`.

- [ ] **Step 6: Check the docs build and the documented-code tests**

```bash
uv sync --all-packages --all-extras --group docs
uv run mkdocs build -f packages/netstead/mkdocs.yml -d "$PWD/.site-check" 2>&1 | grep -i "workbench-cards" || true
rm -rf .site-check
uv run --all-extras pytest packages/netstead/tests/test_documented_python_contract.py packages/netstead/tests/test_documented_api_contract.py -m "" -q
```

Expected: the site builds as in CI (`.github/workflows/documentation.yml`) and prints no warning that names
`workbench-cards`; the documented-code test passes (the page's one Python block is `doctest: skip`).

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/docs/cookbook/workbench-cards.md packages/netstead/docs/cookbook/index.md \
  packages/netstead/tests/test_documented_api_contract.py \
  packages/netstead/docs/cookbook/workbench-plugins.md packages/netstead/mkdocs.yml CONTRIBUTING.md \
  docs/design/README.md docs/design/2026-10-07-cards-plugin-scope.md docs/design/2026-10-19-cards-plugin-plan.md \
  packages/netstead-cards/CHANGELOG.md packages/netstead-cards/README.md
git commit -m "docs(cards): cookbook page, indexes, contributing and changelog"
```

(Fill the PR numbers in a fixup commit once PR C has its number; A and B are known by then.)

---

### Task 17: Verify, set the coverage gate, walk through it in a browser, record follow-ups

**Files:**
- Modify: `.github/workflows/tests.yml` (the gate), `docs/design/2026-10-07-cards-plugin-scope.md` ("Follow-ups"),
  `docs/design/README.md` (the scope row's follow-ups column)

- [ ] **Step 1: The full suite, lint, and coverage**

```bash
uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run python scripts/lint_no_sql.py
uv run --all-extras pytest packages -n auto -m "" -q
uv run --all-extras pytest -m "" -n auto packages/netstead-cards/tests --doctest-modules \
  packages/netstead-cards/netstead_cards --cov=netstead_cards --cov-report=term-missing
```

Expected: all green. Read the coverage total `T` (the plan's dry run measured 94.9 %, so expect a gate of 93) and
set the CI gate to `floor(T) - 1` (at least 85) in the "Test
netstead-cards" step of `.github/workflows/tests.yml` (`--cov-fail-under=<gate>`). A line the report lists as missed
in `handlers.py`, `flow.py` or `lower.py` is a refusal path: add a test for it rather than lowering the gate.

- [ ] **Step 2: Walk through it in the browser**

```bash
uv run netstead app packages/netstead/netstead/fixtures/leavenworth/parquet
```

Check, in order:

1. **Settings → Plugins** lists ProjectCards 0.1.0 as loaded, with no front-end error.
2. Click link 1; right-click it: the menu has **Selection (1 link) → New change ▸ Property change** and **Deletion**.
3. Choose Property change: the **Card** panel shows "1 picked link" and a form whose fields read "lanes (now 1)" etc.
   Set lanes 2, **Preview**: a "Preview: …" network becomes active and link 1 is drawn with 2 lanes; the base network
   still says 1 when you switch back in the network switcher.
4. **Add to card**: the preview closes, the draft lists the change, the panel tab and the workspace tab show the
   dirty dot and "1"; core's **Edits** panel lists the edit with source `cards`.
5. Type "Benton Street" in the command bar, **New change ▸ Property change**, set lanes 3 with **How: set**, **Add
   to card**. The draft has 2 changes. **Undo last change**: back to 1; Benton Street's links are back to their lanes.
6. Fill project "Leavenworth fixes", file `cards/leavenworth-fixes.yml` (inside an allowed root), **Commit…**: the
   dialog shows the YAML with `model_link_id: [1]` and `ignore_missing: false`. **Commit**. The badge clears.
7. Open the command palette: **New change ▸ Transit route addition (not yet supported)** toasts "not yet supported".
8. **Copy as Python** in the history strip: the `AddChange`, `UndoChange` and `CommitCard` lines import from
   `netstead_cards.actions`.
9. Run `uv run python -c "import projectcard; print(projectcard.read_card('cards/leavenworth-fixes.yml', validate=True).project)"`:
   prints `Leavenworth fixes`.

Note anything that looks wrong as a task in the PR before merging.

- [ ] **Step 3: Record the follow-ups**

Append to `docs/design/2026-10-07-cards-plugin-scope.md`:

```markdown
## Follow-ups (from the implementation, 2026-10)

Not filed as issues yet; each is small and independent.

- **The offline report's edit-log writer is not schema-valid.** `netstead.map.edits.dump_edit_log` writes
  `facility: {model_link_id: [...]}` (the schema wants `facility.links.model_link_id` plus `ignore_missing`) and a
  per-change `notes` (forbidden by `additionalProperties: false`). The cards plugin reads it tolerantly. Make the
  writer emit `links: {model_link_id: [...], ignore_missing: false}` and move each change's note to a
  `netstead.note[<i>]: …` line in the card's `notes` (the plugin's provenance format), keeping `apply_edits` reading
  both shapes; then the plugin's tolerant path can stay for old files only.
- **Host: a lineage entry on commit.** The scope wants a committed card's id in the network's lineage; `Host` has no
  method for it (only `host.mutate` appends). Candidate: `host.note_lineage(net_id, text)` in a Host API minor.
- **Host: a read-path check.** `cards.import` vets the file it reads with `host.writable`. Candidate:
  `host.readable(path)`.
- **projectcard upstream.** Its `validate_card` fills defaults into the caller's dict (`parse_defaults=True` by
  default); `property_changes` is never type-checked (its `anyOf` alternatives are not keywords); `utils.py` has an
  invalid escape (`SyntaxWarning`). Report them upstream, then drop the `SyntaxWarning` filter.
- **`apply_card`:** re-resolve a card's query selections on another network version (out of scope for v1).
```

In `docs/design/README.md`, set the cards scope row's "Open follow-ups" cell to
`offline edit-log writer; Host lineage + read-path; projectcard upstream; apply_card (see the scope's Follow-ups)`.

- [ ] **Step 4: Commit and open PR C**

```bash
git add .github/workflows/tests.yml docs/design/2026-10-07-cards-plugin-scope.md docs/design/README.md
git commit -m "chore(cards): coverage gate and follow-up notes"
```

Push `feat/cards-c` and open PR C ("Cards plugin C: UI and docs"), with the walk-through's results.

---
## Self-review (against the scope)

**Coverage.** Each requirement of the [scope](2026-10-07-cards-plugin-scope.md), and where it is built:

| Scope requirement | Task |
|---|---|
| Separate package; `projectcard` a required dependency of the plugin, not of netstead core; minimum version pinned | 2 |
| Imports only `netstead.workbench.plugins` (enforced) | 2 (import-linter contract), 1 (the export it needs) |
| Selection → verb → form → preview → commit | 13 (form data), 14–15 (commands, panel, forms), 11 (preview), 10 (commit) |
| Change-type names from the schema, pinned against the installed package | 4 |
| Transit declared, "not yet supported" (model, card, Action, form, command) | 4, 6, 7, 9, 13, 15 |
| Values: number or string; empty and list refused with the card rule; booleans to 1/0 | 4 (tests 4, 6) |
| Provenance in top-level `notes` (`netstead.resolved[i]`, `netstead.note[i]`), read back; query without provenance refused | 6, 10 |
| `ignore_missing`: picks and deletions false; query selections carry the intent's value | 6, 9 |
| Offline report edit log imported tolerantly; writer unchanged; follow-up note | 6, 10, 17 |
| FieldMap and the mapping file (selectors, fields, values, derived access flags) | 3 |
| Lowering through `host.plan_update` / `plan_delete` / `plan_add` and `host.mutate`; `existing` conflicts (`error`, `warn`, `skip`); delta grouped by current value; `clean_nodes`; addition end-node and key checks | 7, 8 |
| One change = one undo step, atomic | 7, 8, 9 (a deletion is one or two pending edits, undone together; atomic at the Action) — see Facts 5 |
| The change's note names it (`cards: roadway_property_change link 1: free_speed 40 → 30`) | 7 |
| Draft card as plugin state with a dirty badge; `cards.*` Actions; recorded and replayable | 9 (state, replay test), 15 (badge) |
| `cards.undo_change` LIFO through `host.undo`; refused when core's change is newer | 9 |
| Commit: live warnings confirmed first; written through `host.writable`; `catalog.add_card` when it exists | 10 |
| Import all or nothing ("change 2 of 2: …") | 10 |
| Saved selections (P3a) seed a card's facility via `host.saved_selection` | 9 (`saved_selection`), 13 (`saved` on `/form`) |
| Assistant: mutating `cards.*` Actions go through draft approval | 9 (all `mutates`), 12 |
| A card the plugin writes loads with `projectcard`'s own reader | 6, 10, 17 |
| CI (own test run, coverage gate), workspace and lock, docs (cookbook, design index, changelog) | 2, 16, 17 |
| Out of scope respected: `apply_card`, scenarios, catalog plugin, transit, `pycode`, managed lanes | not built; recorded in 17's follow-ups |

The committed card's id does not join the network's lineage (Facts 7): the Host has no method for it. It is
recorded in plugin state and listed as a follow-up.

**Placeholders.** None: every code step has the code, every run step the command and its expected outcome. The PR
numbers in Task 16 are filled in when the PRs exist; the coverage gate in Task 17 is measured, with the rule for
setting it.

**Names used across tasks** (checked for consistency): `card_problems` (5); `CardError`, `CardNotSupported`,
`not_supported` (4); `DraftCard`, `Dependencies`, `read_card_file` (6); `apply_change`, `Applied`, `describe`,
`few`, `rows_by_key`, `KEYS` (7); `CardsState`, `Draft`, `slug` (9); `target_network`, `complete_change`,
`selection_facility`, `check_card_shape`, `refuse_transit`, `as_action_errors`, `is_open` (9); `Handlers.specs`
(9, extended in 10, 11); Actions `AddChange` / `cards.add_change`, `UndoChange` / `cards.undo_change` (9),
`CommitCard` / `cards.commit`, `ImportCard` / `cards.import` (10), `PreviewChange` / `cards.preview`,
`ClosePreview` / `cards.close_preview` (11); `build_router` (13); JS `propertyChange`, `deletion`, `addition`,
`changeAction`, `draftNet`, `draftOf`, `draftBadge`, `warningLines` (14), `createController` (14), `activate` (15);
panel id `cards.draft`; command ids `cards.new_<type>`, `cards.undo`; state keys `plugins.cards.drafts`,
`committed`, `preview`, `change_types`, `supported`.
