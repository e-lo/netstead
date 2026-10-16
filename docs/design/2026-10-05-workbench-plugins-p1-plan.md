# Workbench Plugins, Part 1 (Python plugin core): Implementation Plan

Status: **implemented** (#222) · Date: 2026-10-05 · Owner: Elizabeth Sall · Parent: [Workbench plugins design](2026-10-05-workbench-plugins-design.md)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to carry out this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a separately installed pip package add Actions, handlers, settings, session state, an API router and static files to the Workbench. It does so through an entry point and a small, versioned `Host` API, without editing Workbench core.

**Architecture:**
- Each `Session` gets its own `ActionRegistry`, seeded with the core Actions, plus a `type → handler` table. Plugins add to both, so core and plugin Actions dispatch the same way.
- Plugins come from the `netstead.workbench.plugins` entry-point group, or are passed explicitly as `Session(plugins=[...])`. Each one is checked (id, Action namespacing, API version, settings) and installed, or recorded as `disabled`, `incompatible` or `error` without affecting the others.
- A plugin only ever sees a `Host`. `Host` wraps the session and offers read access, `mutate`, `derive`, cross-plugin dispatch, browser events, jobs and the path sandbox.
- `build_app` mounts each loaded plugin's router and static directory, and serves `GET /api/plugins`.

**Tech stack:** Python 3.11+, pydantic v2, FastAPI, corral editing (`apply_edit` / `reverse_edit`), `importlib.metadata` entry points. No new dependencies.

**Spec:** `docs/design/2026-10-05-workbench-plugins-design.md`.

**Depends on:** PR #211 (Workbench P0 + P1a) and PR #212 (P1b).

**Implemented with these deviations:**
- `BaseAction.action_type()` requires the exact shape `type: Literal["<type>"] = "<type>"` (one value), checked strictly rather than inferred loosely.
- A plugin installs atomically: its router and static dir are built and checked (mount-able) at install time, not deferred to when the app attaches them.
- `mutate` applies the edits to a derived copy of the network and swaps it in on success, rather than mutating in place; a failing Action rolls that swap (and any nested `derive`) back.
- A nested `host.dispatch` records its entry with `parent_seq` pointing at the outer Action, and "copy as Python" comments it as `# via <outer type>: ...` rather than inlining it.
- A `mutate` called outside any Action is recorded as a non-replayable history entry, not silently applied.
- Per-session caches (the LLM parser, the settings payload) are keyed off a private generation counter, invalidated by bumping it, rather than cleared field by field.
- `Settings.plugins` values are checked for secret-shaped keys and values at validation time, and redacted again defensively wherever settings are read back.
- Tests that need a real installed entry point are marked `@pytest.mark.plugin_discovery` and skip when the example package isn't installed (CI installs it).
- The CLI (`netstead app`) still builds and serves exactly one `Session`; multi-session serving stayed out of scope.
- The public API grew beyond the spec's sketch: `Session.network`, `selection_copy`, `plugin_mounts`, `unload_plugin`, `mutate(origin=...)`, `NetworkHandle.replace_tables`/`restore`, and `HistoryEntry.parent_seq`.

**Part 2 (separate plan, written after P1b merges):** front-end slots (workspace tabs, dock, commands, layer registry, `schemaForm`), the browser plugin loader that calls `activate(wb)`, and the Plugins settings UI. That plan is kept separate because P1b is rewriting `index.html`, `main.js`, `map.js` and `table.js`, and the settings dialog, right now.

---

## Where to work

- **Base.** Branch `feat/workbench-plugins` from `main` once `feat/workbench-p1b` has merged. If P1b has not merged yet, branch from the tip of `feat/workbench-p1b` instead, and rebase later.
- **P1b overlap.** P1b's Task 6 edits `actions.py` and `session.py` (the settings payload). Merge it before starting so this plan's edits don't conflict.
- **Names.** Use the post-rename names everywhere: package `netstead` (`packages/netstead/netstead/...`), engine `corral`, env prefix `NETSTEAD_`.
- **Test command** (domain-scoped; the full suite runs only before merging):

```bash
uv run pytest packages/netstead/tests -k "workbench" -q
```

## Deviations from the spec (decided here, for simplicity)

- **API version check.** The plugin declares `requires_api="1.0"`. It is compatible when the major versions match and the host's minor version is at least the plugin's. This needs no `packaging` dependency.
- **Disabling plugins.** The setting is `app.disabled_plugins: list[str]`. There is no `[workbench]` section.
- **No Python-side `events.subscribe` hooks in v1.** Neither target plugin needs them: the authoring plugin reads `host.selection` when its form opens. Browser reactivity is SSE, which Part 2 covers. Hooks are added when a plugin actually needs them.
- **`host.selection` is the selection payload dict** that `Session.state()` already exposes. Keeping a live `SelectionResult` on `Session` is an existing P1 follow-up; `Host` will expose it once that exists.
- **No `netstead.workbench.testing` module.** Plugin tests just build `Session(plugins=[my_plugin()], project_dir=tmp_path, environ=...)`, and the authoring guide shows how.
- **Plugin Actions cannot be `runs_as_job`.** A plugin that needs background work calls `host.submit_job` from a normal Action handler.
- **No enforcement of "only mutating Actions may call `mutate`".** Every mutation is still visible: it bumps the version, appends to lineage and publishes `state`.

## File structure

| File | Responsibility |
|---|---|
| `packages/netstead/netstead/workbench/actions.py` (modify) | `BaseAction` (renamed from `_Action`), `CORE_ACTIONS`, `ActionRegistry`, `import_line` |
| `packages/netstead/netstead/workbench/registry.py` (modify) | `derived_copy(net)`, plus `NetworkHandle.derived_from` |
| `packages/netstead/netstead/workbench/session.py` (modify) | Per-session registry and handler table, plugin install, `mutate`, `derive`, plugin state, plugin-settings check |
| `packages/netstead/netstead/workbench/plugins/__init__.py` (create) | Public plugin API re-exports |
| `packages/netstead/netstead/workbench/plugins/spec.py` (create) | `HOST_API`, `ActionSpec`, `WorkbenchPlugin`, `api_compatible`, `problems` |
| `packages/netstead/netstead/workbench/plugins/discovery.py` (create) | `ENTRY_POINT_GROUP`, `PluginStatus`, `discover` |
| `packages/netstead/netstead/workbench/plugins/host.py` (create) | `Host`, `validate_plugin_settings`, `PluginSettingsError` |
| `packages/netstead/netstead/config.py` (modify) | `Settings.plugins`, `AppSettings.disabled_plugins` |
| `packages/netstead/netstead/workbench/server.py` (modify) | Mount plugin routers and static dirs; `GET /api/plugins` |
| `packages/netstead/netstead/workbench/routes/core.py` (modify) | Parse with `session.actions`, not the core-only parser |
| `packages/netstead/netstead/workbench/static/js/history.js` (modify) | Build the script's imports from `entry.imports` |
| `packages/netstead/tests/test_workbench_action_registry.py` (create) | Registry tests |
| `packages/netstead/tests/test_workbench_plugins.py` (create) | Discovery, install, Host, settings and state tests |
| `packages/netstead/tests/test_workbench_plugin_routes.py` (create) | HTTP tests |
| `examples/workbench-plugin-hello/` (create) | Minimal external-style plugin package |
| `packages/netstead/docs/cookbook/workbench-plugins.md` (create) and `packages/netstead/mkdocs.yml` (modify) | Authoring guide |

---

### Task 0: Branch and baseline

**Files:** none.

- [ ] **Step 1: Create the branch and worktree**

```bash
git fetch origin
git worktree add .claude/worktrees/workbench-plugins-impl -b feat/workbench-plugins origin/main   # or feat/workbench-p1b if P1b is unmerged
cd .claude/worktrees/workbench-plugins-impl
uv sync --all-packages --all-extras
```

- [ ] **Step 2: Baseline the workbench tests**

Run: `uv run pytest packages/netstead/tests -k workbench -q`
Expected: all pass. Write down any failures that already exist so they aren't blamed on this work later.

---

### Task 1: `BaseAction` and a per-session `ActionRegistry`

**Files:**
- Modify: `packages/netstead/netstead/workbench/actions.py`
- Test: `packages/netstead/tests/test_workbench_action_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_action_registry.py`:

```python
"""Tests for the per-session ActionRegistry (core Actions + plugin Actions)."""

from typing import Literal

import pytest
from netstead.workbench.actions import (
    CORE_ACTIONS,
    ActionRegistry,
    BaseAction,
    OpenNetwork,
    action_json_schema,
    import_line,
    parse_action,
)
from pydantic import ValidationError


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


class NoType(BaseAction):
    name: str


def test_action_type_reads_the_discriminator():
    assert Greet.action_type() == "hello.greet" and OpenNetwork.action_type() == "open_network"
    assert NoType.action_type() is None


def test_default_registry_holds_exactly_the_core_actions():
    reg = ActionRegistry()
    assert reg.types() == [m.action_type() for m in CORE_ACTIONS]
    assert isinstance(reg.parse({"type": "open_network", "source": "/x"}), OpenNetwork)


def test_registering_a_plugin_action_extends_parse_and_schema():
    reg = ActionRegistry()
    reg.register(Greet)
    assert reg.has("hello.greet") and reg.model("hello.greet") is Greet
    assert reg.parse({"type": "hello.greet", "name": "Ada"}) == Greet(name="Ada")
    assert "Greet" in reg.json_schema()["$defs"]


def test_registry_rejects_duplicates_and_non_actions():
    reg = ActionRegistry()
    with pytest.raises(ValueError, match="already registered"):
        reg.register(OpenNetwork)
    with pytest.raises(ValueError, match="type"):
        reg.register(NoType)
    with pytest.raises(TypeError):
        reg.register(dict)  # type: ignore[arg-type]


def test_registries_are_independent():
    a, b = ActionRegistry(), ActionRegistry()
    a.register(Greet)
    assert not b.has("hello.greet")
    with pytest.raises(ValidationError):
        b.parse({"type": "hello.greet", "name": "Ada"})


def test_module_level_parse_and_schema_stay_core_only():
    with pytest.raises(ValidationError):
        parse_action({"type": "hello.greet", "name": "Ada"})
    assert "Greet" not in action_json_schema().get("$defs", {})


def test_import_line_uses_the_public_module_for_core_and_the_class_module_otherwise():
    assert import_line(OpenNetwork(source="/x")) == "from netstead.workbench import OpenNetwork"
    assert import_line(Greet(name="Ada")) == f"from {Greet.__module__} import Greet"
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_action_registry.py -q`
Expected: FAIL with `ImportError: cannot import name 'CORE_ACTIONS'`.

- [ ] **Step 3: Rename `_Action` to `BaseAction`, then add `action_type`**

In `actions.py`, replace every `_Action` with `BaseAction`. The class definition and the subclasses are all in this file, and `grep -rn "_Action\b" packages/netstead` finds no other users. Then make the class read:

```python
class BaseAction(BaseModel):
    """Base class for every Workbench Action, core and plugin.

    A subclass declares ``type: Literal["<type>"] = "<type>"`` (plugins: ``"<plugin id>.<name>"``)
    and its fields. ``mutates`` marks actions the assistant must draft before applying;
    ``runs_as_job`` (core only) runs the slow work on a background job thread; ``replay_overrides``
    are fields forced in the ``to_python`` replay snippet.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)  # errors never echo a value (keys)
    mutates: ClassVar[bool] = False
    runs_as_job: ClassVar[bool] = False
    replay_overrides: ClassVar[dict[str, Any]] = {}

    @classmethod
    def action_type(cls) -> str | None:
        """The ``type`` discriminator this class carries (``None`` when it declares none)."""
        field_info = cls.model_fields.get("type")
        default = field_info.default if field_info is not None else None
        return default if isinstance(default, str) and default else None

    def job_label(self) -> str:
        """The label a ``runs_as_job`` action's background job shows in the jobs panel."""
        return self.type.replace("_", " ")
```

Add `"BaseAction"`, `"CORE_ACTIONS"`, `"ActionRegistry"` and `"import_line"` to `__all__`, keeping it sorted.

- [ ] **Step 4: Replace the module-level `_ADAPTER` with `CORE_ACTIONS` and `ActionRegistry`**

Keep the `Action = Annotated[...]` union, which typing still uses. Replace the `_ADAPTER = ...` line, `parse_action` and `action_json_schema` with the code below. Add `import functools`, `import operator`, and `from collections.abc import Iterable` (alongside the existing `Iterator, Mapping`):

```python
#: The Actions every session understands, in registry order (``to_python`` imports them from ``netstead.workbench``).
CORE_ACTIONS: tuple[type[BaseAction], ...] = (
    OpenNetwork,
    BuildNetwork,
    CloseNetwork,
    SetActiveNetwork,
    Select,
    ClearSelection,
    Style,
    Navigate,
    SetSetting,
)


class ActionRegistry:
    """The Action types one session understands: the core ones plus any its plugins register."""

    def __init__(self, models: Iterable[type[BaseAction]] = CORE_ACTIONS) -> None:
        """Start with ``models`` registered (default: :data:`CORE_ACTIONS`)."""
        self._models: dict[str, type[BaseAction]] = {}
        self._adapter: TypeAdapter[Any] | None = None
        for model in models:
            self.register(model)

    def register(self, model: type[BaseAction]) -> None:
        """Add ``model``; raises ``TypeError`` for a non-Action, ``ValueError`` for a missing or taken ``type``."""
        if not (isinstance(model, type) and issubclass(model, BaseAction)):
            raise TypeError(f"{model!r} is not a BaseAction subclass")
        action_type = model.action_type()
        if action_type is None:
            raise ValueError(f"{model.__name__} needs a `type: Literal[...]` field with a default")
        if action_type in self._models:
            raise ValueError(f"action type {action_type!r} is already registered")
        self._models[action_type] = model
        self._adapter = None

    def has(self, action_type: str) -> bool:
        """Whether ``action_type`` is registered."""
        return action_type in self._models

    def types(self) -> list[str]:
        """Registered types, in registration order."""
        return list(self._models)

    def model(self, action_type: str) -> type[BaseAction]:
        """The class registered for ``action_type`` (``KeyError`` if none)."""
        return self._models[action_type]

    def parse(self, data: Mapping[str, Any]) -> BaseAction:
        """Validate a JSON dict into a registered Action (raises ``pydantic.ValidationError``)."""
        return self._get_adapter().validate_python(data)

    def json_schema(self) -> dict[str, Any]:
        """JSON schema of every registered Action (the assistant's tool vocabulary)."""
        return self._get_adapter().json_schema()

    def _get_adapter(self) -> TypeAdapter[Any]:
        if self._adapter is None:
            union = functools.reduce(operator.or_, self._models.values())
            self._adapter = TypeAdapter(
                Annotated[union, Field(discriminator="type")], config=ConfigDict(hide_input_in_errors=True)
            )
        return self._adapter


#: The core-only registry behind the module-level helpers (a session has its own, see ``Session.actions``).
_CORE_REGISTRY = ActionRegistry()


def parse_action(data: dict[str, Any]) -> Action:
    """Validate a JSON dict into a core Action (raises ``pydantic.ValidationError``)."""
    return _CORE_REGISTRY.parse(data)  # type: ignore[return-value]  # core registry: always a core Action


def action_json_schema() -> dict[str, Any]:
    """JSON schema of the core Action union."""
    return _CORE_REGISTRY.json_schema()


def import_line(action: BaseAction) -> str:
    """The ``from ... import ...`` line a replayed ``to_python`` snippet needs for ``action``."""
    cls = type(action)
    module = "netstead.workbench" if cls in CORE_ACTIONS else cls.__module__
    return f"from {module} import {cls.__name__}"
```

Change `to_python`'s signature to `def to_python(action: BaseAction) -> str:`. Its body doesn't change.

- [ ] **Step 5: Run the new tests and the existing action tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_action_registry.py packages/netstead/tests/test_workbench_actions.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/actions.py packages/netstead/tests/test_workbench_action_registry.py
git commit -m "feat(workbench): BaseAction + per-session ActionRegistry (core actions seed it)"
```

---

### Task 2: Session dispatches through its registry and handler table, and records imports

**Files:**
- Modify: `packages/netstead/netstead/workbench/session.py`
- Modify: `packages/netstead/netstead/workbench/routes/core.py`
- Test: `packages/netstead/tests/test_workbench_session.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `packages/netstead/tests/test_workbench_session.py`:

```python
def test_history_entry_carries_its_import_line(opened):
    assert opened.history[-1].imports == "from netstead.workbench import OpenNetwork"


def test_session_has_its_own_action_registry(session):
    assert session.actions.has("open_network") and session.actions.types()[0] == "open_network"


def test_dispatch_refuses_an_unregistered_action_instance(session):
    from typing import Literal

    from netstead.workbench.actions import BaseAction

    class Stray(BaseAction):
        type: Literal["stray.thing"] = "stray.thing"

    with pytest.raises(ValueError, match="not registered"):
        session.dispatch(Stray())
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_session.py -q -k "import_line or own_action_registry or unregistered"`
Expected: FAIL, with `AttributeError: 'HistoryEntry' object has no attribute 'imports'` and similar.

- [ ] **Step 3: Implement**

In `session.py`:

1. **Imports.** Change the `.actions` import to bring in `ActionRegistry`, `BaseAction` and `import_line`. Drop `parse_action`, which `session.py` no longer uses. Add `from collections.abc import Callable` next to `Mapping`.
2. **`HistoryEntry`.** Add a field right after `python: str`:

```python
    imports: str  # the ``from ... import ...`` line ``python`` needs (a plugin's Action lives in its own package)
```

3. **`Session.__init__`.** Add these lines after `self._committed = {}`:

```python
        #: The Actions this session understands: the core ones, plus any its plugins register.
        self.actions = ActionRegistry()
        #: ``type`` -> handler run under the lock. Core handlers are this class's ``_do_<type>`` methods
        #: (job actions use ``_job_<type>``); plugins add theirs when installed.
        self._handlers: dict[str, Callable[..., Any]] = {
            t: getattr(self, f"_do_{t}") for t in self.actions.types() if not self.actions.model(t).runs_as_job
        }
```

4. **`_coerce`.** Add this helper right after `dispatch_recorded`:

```python
    def _coerce(self, action: BaseAction | dict[str, Any]) -> BaseAction:
        """Parse a dict with this session's registry; refuse an instance of a class it doesn't know."""
        if isinstance(action, dict):
            return self.actions.parse(action)
        if not self.actions.has(action.type) or self.actions.model(action.type) is not type(action):
            raise ValueError(f"action type {action.type!r} is not registered in this session")
        return action
```

5. **`dispatch_recorded`.** Replace `if isinstance(action, dict): action = parse_action(action)` with `action = self._coerce(action)`. Replace `handler = getattr(self, f"_do_{action.type}")` with `handler = self._handlers[action.type]`. Leave the `_prepare_<type>` lookup as is: preparing is core-only (`Select`).
6. **`submit`.** Replace the `isinstance` / `parse_action` lines with `action = self._coerce(action)`.
7. **Signatures.** Change `Action` to `BaseAction` in the parameter annotations of `dispatch`, `dispatch_recorded`, `submit`, `_commit`, `_finish` and `_record`.
8. **`_record`.** Pass `imports=import_line(action)` to `HistoryEntry(...)`, right after `python=`.

In `routes/core.py`, drop the `from ..actions import parse_action` import. Inside `actions()`, change `action = parse_action(body)` to `action = session.actions.parse(body)`.

- [ ] **Step 4: Run the workbench tests**

Run: `uv run pytest packages/netstead/tests -k workbench -q`
Expected: PASS, and the earlier `python` snippets are unchanged (for example `test_open_network_registers_activates_and_records`).

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/session.py packages/netstead/netstead/workbench/routes/core.py packages/netstead/tests/test_workbench_session.py
git commit -m "refactor(workbench): dispatch via the session's ActionRegistry + handler table; history records imports"
```

---

### Task 3: "Copy session as Python" uses each entry's imports

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/history.js`
- Test: `packages/netstead/tests/test_workbench_static.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `packages/netstead/tests/test_workbench_static.py`:

```python
def test_history_script_imports_come_from_entries():
    from netstead.workbench.server import STATIC_DIR

    js = (STATIC_DIR / "js" / "history.js").read_text(encoding="utf-8")
    assert "e.imports" in js
    assert "SetActiveNetwork, Select" not in js  # no hard-coded core import list
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run pytest packages/netstead/tests/test_workbench_static.py -q -k imports_come_from_entries`
Expected: FAIL.

- [ ] **Step 3: Replace `sessionScript` in `history.js`**

```javascript
export function sessionScript(entries) {
  // Each entry names its own import: a plugin's Actions live in the plugin's package.
  const imports = [...new Set(entries.filter(e => e.ok).map(e => e.imports))];
  const lines = [
    "from netstead.workbench import Session",
    ...imports,
    "",
    "app = Session()  # or reuse a live session",
  ];
  for (const e of entries) lines.push(e.ok ? e.python : `# failed: ${e.python}  # ${e.error}`);
  return lines.join("\n");
}
```

- [ ] **Step 4: Run the static tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_static.py -q`
Expected: PASS. If P1b added a Node test harness (`test_workbench_js.py`) that covers `sessionScript`, run it as well and update any assertion that expected the old import line.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/static/js/history.js packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): session script imports come from each history entry"
```

---

### Task 4: Settings: a `plugins` table and `app.disabled_plugins`

**Files:**
- Modify: `packages/netstead/netstead/config.py`
- Test: `packages/netstead/tests/test_config.py` (append; if the config tests live in a differently named file, run `ls packages/netstead/tests | grep -i config` and use that)

- [ ] **Step 1: Write the failing tests**

```python
def test_plugin_tables_are_free_form_and_layered(tmp_path):
    from netstead.config import load_settings

    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "cfg"), "NETSTEAD_PLUGINS__HELLO__PREFIX": "Hi"}
    (tmp_path / "netstead.toml").write_text('[plugins.hello]\ncount = 3\n', encoding="utf-8")
    loaded = load_settings(project_dir=tmp_path, environ=env)
    assert loaded.settings.plugins == {"hello": {"count": 3, "prefix": "Hi"}}
    assert loaded.sources["plugins.hello.prefix"] == "env" and loaded.sources["plugins.hello.count"] == "project"


def test_disabled_plugins_defaults_empty(tmp_path):
    from netstead.config import load_settings

    loaded = load_settings(project_dir=tmp_path, environ={"NETSTEAD_CONFIG_DIR": str(tmp_path / "cfg")})
    assert loaded.settings.app.disabled_plugins == []
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests -q -k "plugin_tables or disabled_plugins_defaults"`
Expected: FAIL with `extra_forbidden` on `plugins`.

- [ ] **Step 3: Implement**

In `config.py`, add this field to `AppSettings`:

```python
    disabled_plugins: list[str] = Field(default_factory=list)  # plugin ids not to load (see netstead.workbench.plugins)
```

Add this field to `Settings`, after `credentials`:

```python
    #: One free-form table per Workbench plugin (``[plugins.<id>]``), validated by that plugin's own model.
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

- [ ] **Step 4: Run the config and workbench tests**

Run: `uv run pytest packages/netstead/tests -q -k "config or settings or workbench"`
Expected: PASS. Any snapshot of `Settings.model_json_schema()` keys must be updated to include `plugins` (search the tests for `model_json_schema`).

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/config.py packages/netstead/tests
git commit -m "feat(config): [plugins.<id>] tables and app.disabled_plugins"
```

---

### Task 5: Plugin spec, the API check, and the static checks

**Files:**
- Create: `packages/netstead/netstead/workbench/plugins/__init__.py`
- Create: `packages/netstead/netstead/workbench/plugins/spec.py`
- Test: `packages/netstead/tests/test_workbench_plugins.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_plugins.py`:

```python
"""Tests for Workbench plugins: spec checks, discovery, install, Host, settings, state."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import pytest
from netstead.workbench.actions import ActionRegistry, BaseAction
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin
from netstead.workbench.plugins.spec import api_compatible, problems
from pydantic import BaseModel, ConfigDict


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


class HelloSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = "Hello"


def make_hello(**overrides: Any) -> WorkbenchPlugin:
    """A fresh ``hello`` plugin: ``hello.greet`` returns ``"<prefix>, <name>!"`` and remembers names in state."""
    greeted: list[str] = []

    def greet(host, action: Greet) -> str:
        greeted.append(action.name)
        return f"{host.settings.prefix}, {action.name}!"

    fields: dict[str, Any] = {
        "id": "hello",
        "name": "Hello",
        "version": "0.1",
        "requires_api": HOST_API,
        "actions": (ActionSpec(Greet, greet),),
        "settings_model": HelloSettings,
        "state": lambda host: {"greeted": list(greeted)},
    }
    fields.update(overrides)
    return WorkbenchPlugin(**fields)


# ---------------------------------------------------------------------------- spec


def test_api_compatible_same_major_and_new_enough_minor():
    assert api_compatible("1.0", "1.2") and api_compatible("1.2", "1.2")
    assert not api_compatible("1.3", "1.2") and not api_compatible("2.0", "1.2")
    assert not api_compatible("one", "1.0") and not api_compatible("1", "1.0")


def test_problems_none_for_a_good_plugin():
    assert problems(make_hello(), ActionRegistry(), taken=()) == []


class Unprefixed(BaseAction):
    type: Literal["greet"] = "greet"


class JobAction(BaseAction):
    type: Literal["hello.slow"] = "hello.slow"
    runs_as_job: ClassVar[bool] = True


@pytest.mark.parametrize(
    ("overrides", "taken", "match"),
    [
        ({"id": "Hello-World"}, (), "must match"),
        ({}, ("hello",), "already uses"),
        ({"actions": (ActionSpec(Unprefixed, lambda h, a: None),)}, (), "must start with 'hello.'"),
        ({"actions": (ActionSpec(JobAction, lambda h, a: None),)}, (), "job actions"),
        ({"actions": (ActionSpec(dict, lambda h, a: None),)}, (), "not a BaseAction"),  # type: ignore[arg-type]
    ],
)
def test_problems_reports_each_violation(overrides, taken, match):
    found = problems(make_hello(**overrides), ActionRegistry(), taken=taken)
    assert any(match in p for p in found), found


def test_problems_reports_a_type_already_registered():
    registry = ActionRegistry()
    registry.register(Greet)
    assert any("already registered" in p for p in problems(make_hello(), registry, taken=()))
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q`
Expected: FAIL with `ModuleNotFoundError: netstead.workbench.plugins`.

- [ ] **Step 3: Create `plugins/spec.py`**

```python
"""What a Workbench plugin declares (:class:`WorkbenchPlugin`) and the checks run before installing it."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..actions import ActionRegistry, BaseAction

if TYPE_CHECKING:
    from .host import Host

__all__ = ["HOST_API", "ActionSpec", "WorkbenchPlugin", "api_compatible", "problems"]

#: The plugin API this netstead provides: ``major.minor``. A minor bump only adds; a major bump breaks.
#: Provisional until netstead v1.0 (it may change without a major bump before then).
HOST_API = "1.0"

#: A plugin id: the namespace for its Action types, routes, state, settings and static files.
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ActionSpec:
    """One plugin Action: its model and the handler that applies it (``handler(host, action) -> result``)."""

    model: type[BaseAction]
    handler: Callable[[Host, Any], Any]


@dataclass(frozen=True)
class WorkbenchPlugin:
    """Everything one plugin contributes. An entry point's zero-argument factory returns one.

    ``router(host)`` returns a FastAPI ``APIRouter`` mounted at ``/api/plugins/<id>``; ``static_dir``
    is served at ``/plugins/<id>/`` and ``frontend`` names the ES module the browser loads from it.
    ``settings_model`` validates the ``[plugins.<id>]`` settings table; ``state(host)`` is merged into
    the session state under ``plugins.<id>``; ``on_load(host)`` runs once, before the Actions register.
    """

    id: str
    name: str
    version: str
    requires_api: str
    actions: tuple[ActionSpec, ...] = ()
    router: Callable[[Host], Any] | None = None
    static_dir: Path | None = None
    frontend: str = "main.js"
    settings_model: type[BaseModel] | None = None
    state: Callable[[Host], dict[str, Any]] | None = None
    on_load: Callable[[Host], None] | None = None


def api_compatible(required: str, host: str = HOST_API) -> bool:
    """Whether a plugin needing API ``required`` runs on ``host``: same major, host minor at least as new.

    >>> api_compatible("1.0", "1.2"), api_compatible("1.3", "1.2"), api_compatible("2.0", "1.2")
    (True, False, False)
    """
    try:
        req_major, req_minor = (int(part) for part in required.split("."))
        host_major, host_minor = (int(part) for part in host.split("."))
    except ValueError:
        return False
    return req_major == host_major and host_minor >= req_minor


def problems(plugin: WorkbenchPlugin, registry: ActionRegistry, taken: Collection[str]) -> list[str]:
    """Every reason ``plugin`` can't install next to ``registry``'s Actions and the ``taken`` plugin ids."""
    found: list[str] = []
    if not _PLUGIN_ID.match(plugin.id):
        found.append(f"plugin id {plugin.id!r} must match [a-z][a-z0-9_]*")
    if plugin.id in taken:
        found.append(f"another plugin already uses the id {plugin.id!r}")
    for spec in plugin.actions:
        model = spec.model
        if not (isinstance(model, type) and issubclass(model, BaseAction)):
            found.append(f"{model!r} is not a BaseAction subclass")
            continue
        action_type = model.action_type()
        if action_type is None:
            found.append(f"{model.__name__} needs a `type: Literal[...]` field with a default")
            continue
        if not action_type.startswith(f"{plugin.id}."):
            found.append(f"action type {action_type!r} must start with {plugin.id + '.'!r}")
        if model.runs_as_job:
            found.append(f"{action_type}: plugin actions can't be job actions; call host.submit_job from the handler")
        if registry.has(action_type):
            found.append(f"action type {action_type!r} is already registered")
    return found
```

- [ ] **Step 4: Create `plugins/__init__.py`, with the re-exports later tasks will fill in**

```python
"""Workbench plugins: separately installed packages that add Actions, routes, settings, state and UI.

A plugin package exposes a zero-argument factory returning a :class:`WorkbenchPlugin` under the
``netstead.workbench.plugins`` entry-point group (the entry-point name must equal the plugin id).
Plugins import only what this package exports. See the cookbook page "Write a Workbench plugin".
"""

from ..actions import BaseAction
from .spec import HOST_API, ActionSpec, WorkbenchPlugin

__all__ = ["HOST_API", "ActionSpec", "BaseAction", "WorkbenchPlugin"]
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q --doctest-modules packages/netstead/netstead/workbench/plugins`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/plugins packages/netstead/tests/test_workbench_plugins.py
git commit -m "feat(workbench): WorkbenchPlugin/ActionSpec spec, HOST_API compatibility, install checks"
```

---

### Task 6: Discovery from entry points

**Files:**
- Create: `packages/netstead/netstead/workbench/plugins/discovery.py`
- Modify: `packages/netstead/netstead/workbench/plugins/__init__.py`
- Test: `packages/netstead/tests/test_workbench_plugins.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
# ---------------------------------------------------------------------------- discovery

from dataclasses import dataclass  # noqa: E402

from netstead.workbench.plugins.discovery import PluginStatus, discover  # noqa: E402


@dataclass
class FakeEntryPoint:
    name: str
    target: Any  # the factory, or an exception ``load`` raises

    def load(self) -> Any:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


def test_discover_loads_factories_and_isolates_failures():
    eps = [
        FakeEntryPoint("hello", make_hello),
        FakeEntryPoint("broken", ImportError("no module named broken")),
        FakeEntryPoint("not_a_plugin", lambda: 42),
        FakeEntryPoint("misnamed", make_hello),  # factory's plugin id is "hello"
    ]
    plugins, statuses = discover(eps=eps)
    assert [p.id for p in plugins] == ["hello"]
    by_id = {s.id: s for s in statuses}
    assert by_id["broken"].state == "error" and "no module named broken" in by_id["broken"].error
    assert by_id["not_a_plugin"].state == "error" and "WorkbenchPlugin" in by_id["not_a_plugin"].error
    assert by_id["misnamed"].state == "error" and "must equal" in by_id["misnamed"].error


def test_discover_skips_disabled_without_importing_them():
    plugins, statuses = discover(disabled={"broken"}, eps=[FakeEntryPoint("broken", ImportError("x"))])
    assert plugins == [] and statuses == [PluginStatus(id="broken", name="broken", version="", requires_api=None, state="disabled")]
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k discover`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `plugins/discovery.py`**

```python
"""Find installed Workbench plugins via the ``netstead.workbench.plugins`` entry-point group.

Mirrors :mod:`corral.quality.registry`: each entry point loads in isolation, so one broken plugin is
recorded (a :class:`PluginStatus` with ``state="error"``) and skipped, never fatal. Disabled entry
points are skipped *before* they are imported.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass
from importlib.metadata import entry_points
from typing import Any, Literal

from .spec import WorkbenchPlugin

__all__ = ["ENTRY_POINT_GROUP", "PluginState", "PluginStatus", "discover"]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "netstead.workbench.plugins"

PluginState = Literal["loaded", "disabled", "incompatible", "error"]


@dataclass(frozen=True)
class PluginStatus:
    """What happened to one plugin at startup (shown in the Plugins settings section)."""

    id: str
    name: str
    version: str
    requires_api: str | None
    state: PluginState
    error: str | None = None
    frontend: str | None = None  # URL of the plugin's ES module, when it ships one

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy."""
        return asdict(self)


def discover(
    disabled: Collection[str] = (), *, eps: Iterable[Any] | None = None
) -> tuple[list[WorkbenchPlugin], list[PluginStatus]]:
    """Load every plugin entry point except ``disabled`` ones: ``(plugins, statuses of the ones skipped)``.

    ``eps`` replaces the installed entry points (tests). A loaded plugin gets its status when it is
    installed (:meth:`netstead.workbench.session.Session`), which runs the remaining checks.
    """
    found: list[WorkbenchPlugin] = []
    statuses: list[PluginStatus] = []
    for ep in entry_points(group=ENTRY_POINT_GROUP) if eps is None else eps:
        if ep.name in disabled:
            statuses.append(PluginStatus(ep.name, ep.name, "", None, "disabled"))
            continue
        try:
            plugin = ep.load()()
            if not isinstance(plugin, WorkbenchPlugin):
                raise TypeError(f"entry point {ep.name!r} returned {type(plugin).__name__}, not a WorkbenchPlugin")
            if plugin.id != ep.name:
                raise ValueError(f"entry point name {ep.name!r} must equal the plugin id {plugin.id!r}")
        except Exception as exc:  # boundary: third-party code; one bad plugin must not stop the app
            logger.exception("loading workbench plugin %r failed", ep.name)
            statuses.append(PluginStatus(ep.name, ep.name, "", None, "error", f"{type(exc).__name__}: {exc}"))
            continue
        found.append(plugin)
    return found, statuses
```

- [ ] **Step 4: Export `PluginStatus`, `ENTRY_POINT_GROUP` and `discover`**

In `plugins/__init__.py`, add `from .discovery import ENTRY_POINT_GROUP, PluginStatus, discover`. Add the three names to `__all__`, keeping it sorted.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/netstead/netstead/workbench/plugins packages/netstead/tests/test_workbench_plugins.py
git commit -m "feat(workbench): discover plugins from netstead.workbench.plugins entry points"
```

---

### Task 7: `derived_copy` and `NetworkHandle.derived_from`

**Files:**
- Modify: `packages/netstead/netstead/workbench/registry.py`
- Test: `packages/netstead/tests/test_workbench_registry.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_derived_copy_is_copy_on_write(rdu_source):
    from corral.editing import Edit
    from corral.editing.apply import apply_edit
    from netstead import Network
    from netstead.workbench.registry import as_pandas, derived_copy

    base = Network.from_source(rdu_source)
    copy_ = derived_copy(base)
    first = int(as_pandas(base.links)["link_id"].iloc[0])
    apply_edit(copy_, Edit(op="update_rows", table="link", payload={"predicate": lambda t: t.link_id == first, "set": {"lanes": 9}}))
    lanes = lambda net: int(as_pandas(net.links).set_index("link_id").loc[first, "lanes"])  # noqa: E731
    assert lanes(copy_) == 9 and lanes(base) != 9
    assert copy_.dirty_tracker is None and copy_.spec_version == base.spec_version


def test_summary_reports_derived_from(rdu_source):
    from netstead import Network
    from netstead.workbench.registry import NetworkRegistry

    reg = NetworkRegistry()
    handle = reg.add(Network.from_source(rdu_source), source=rdu_source)
    assert handle.summary()["derived_from"] is None
    handle.derived_from = "base"
    assert handle.summary()["derived_from"] == "base"
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_registry.py -q -k "derived"`
Expected: FAIL with `ImportError: derived_copy`.

- [ ] **Step 3: Implement**

In `registry.py`:
- Add `replace` to the `dataclasses` import.
- Add `"derived_copy"` to `__all__`.
- Add `derived_from: str | None = None` to `NetworkHandle` after `lineage`.
- Add `"derived_from": self.derived_from,` to `summary()` after `"lineage"`.
- Add this function after `as_pandas`:

```python
def derived_copy(net: Network) -> Network:
    """A copy of ``net`` whose tables can be edited without touching ``net`` (copy-on-write).

    The :class:`~corral.dataset.Table` wrappers are copied; their expressions are immutable, so
    they are shared, not duplicated. The copy has no sync-state tracker: it was never read from,
    and must never be written back to, the base network's source.
    """
    tables = {name: replace(table) for name, table in net.tables.items()}
    return replace(net, tables=tables, dirty_tracker=None, metadata=dict(net.metadata))
```

- [ ] **Step 4: Run the registry tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_registry.py -q`
Expected: PASS. Any test asserting the exact `summary()` dict needs `"derived_from": None` added.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/registry.py packages/netstead/tests/test_workbench_registry.py
git commit -m "feat(workbench): copy-on-write derived_copy + NetworkHandle.derived_from"
```

---

### Task 8: `Session.mutate` and `Session.derive`

**Files:**
- Modify: `packages/netstead/netstead/workbench/session.py`
- Test: `packages/netstead/tests/test_workbench_session.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def _lanes(handle, link_id):
    return int(handle.links_df().set_index("link_id").loc[link_id, "lanes"])


def _set_lanes(link_id, lanes):
    from corral.editing import Edit

    return Edit(op="update_rows", table="link", payload={"predicate": lambda t: t.link_id == link_id, "set": {"lanes": lanes}})


def test_derive_registers_a_copy_with_lineage(opened):
    base = opened.registry.get("rdu-i40")
    new_id = opened.derive("rdu-i40", label="Preview", note="preview: widen")
    derived = opened.registry.get(new_id)
    assert derived.derived_from == "rdu-i40" and derived.lineage == ["preview: widen"]
    assert opened.active == "rdu-i40" and derived.links_df().shape == base.links_df().shape


def test_mutate_applies_bumps_and_never_touches_the_base(opened):
    first = int(opened.registry.get("rdu-i40").links_df()["link_id"].iloc[0])
    before = _lanes(opened.registry.get("rdu-i40"), first)
    new_id = opened.derive(None, label="Preview", note="preview")
    results = opened.mutate(new_id, [_set_lanes(first, 9)], note="lanes=9")
    derived = opened.registry.get(new_id)
    assert results[0].diff.rows_changed == 1 and derived.version == 1
    assert _lanes(derived, first) == 9 and _lanes(opened.registry.get("rdu-i40"), first) == before
    assert derived.lineage == ["preview", "lanes=9"]


def test_mutate_is_all_or_nothing(opened):
    from corral.editing import Edit, UnsupportedEditOp

    handle = opened.registry.get("rdu-i40")
    first = int(handle.links_df()["link_id"].iloc[0])
    before = _lanes(handle, first)
    with pytest.raises(UnsupportedEditOp):
        opened.mutate("rdu-i40", [_set_lanes(first, 9), Edit(op="explode", table="link")], note="bad")
    assert handle.version == 0 and _lanes(handle, first) == before and handle.lineage == []


def test_mutate_unknown_network_is_an_action_error(opened):
    with pytest.raises(ActionError, match="unknown network"):
        opened.mutate("nope", [], note="x")
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_session.py -q -k "derive or mutate"`
Expected: FAIL with `AttributeError: 'Session' object has no attribute 'derive'`.

- [ ] **Step 3: Implement**

In `session.py`:
- Add `from collections.abc import Sequence`.
- Add `from corral.editing import Edit, EditResult` and `from corral.editing.apply import apply_edit, reverse_edit`.
- Add `derived_copy` to the `.registry` import.
- Add these methods after `add_network`:

```python
    def mutate(self, net_id: str | None, edits: Sequence[Edit], *, note: str) -> list[EditResult]:
        """Apply corral ``edits`` to a network's roadway, in order and all-or-nothing; return their results.

        A failing edit reverses the ones already applied and re-raises. On success the network's
        ``version`` is bumped (dropping its caches), ``note`` is appended to its lineage, and ``state``
        is published. Not an Action itself: the (plugin) Action that calls it is what history records.
        """
        with self._lock:
            handle = self._handle(net_id)
            applied: list[EditResult] = []
            try:
                for edit in edits:
                    applied.append(apply_edit(handle.roadway, edit))
            except Exception:
                for result in reversed(applied):
                    reverse_edit(handle.roadway, result)
                raise
            handle.bump()
            handle.lineage.append(note)
            self.events.publish({"type": "state", "state": self.state()})
        return applied

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network as a new one (a preview, a scenario); return its id.

        The base is never touched by edits to the copy. The copy records ``derived_from`` and inherits
        the base's lineage plus ``note``. The active network does not change.
        """
        with self._lock:
            base = self._handle(net_id)
            handle = self.registry.add(derived_copy(base.roadway), source=base.source, label=label)
            handle.derived_from = base.id
            handle.lineage = [*base.lineage, note]
            self.events.publish({"type": "state", "state": self.state()})
        return handle.id
```

- [ ] **Step 4: Run the session tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/session.py packages/netstead/tests/test_workbench_session.py
git commit -m "feat(workbench): Session.mutate (atomic corral edits + lineage) and Session.derive (COW networks)"
```

---

### Task 9: `Host`, the plugin's view of the session

**Files:**
- Create: `packages/netstead/netstead/workbench/plugins/host.py`
- Modify: `packages/netstead/netstead/workbench/plugins/__init__.py`
- Test: `packages/netstead/tests/test_workbench_plugins.py` (append; these tests run against `Session` in Task 10, so this task only adds the module and its unit-level tests)

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------- host settings

from netstead.workbench.plugins.host import PluginSettingsError, validate_plugin_settings  # noqa: E402


def test_validate_plugin_settings_defaults_and_values():
    assert validate_plugin_settings(HelloSettings, {}, "hello").prefix == "Hello"
    assert validate_plugin_settings(HelloSettings, {"prefix": "Hi"}, "hello").prefix == "Hi"


def test_validate_plugin_settings_never_echoes_values():
    with pytest.raises(PluginSettingsError) as info:
        validate_plugin_settings(HelloSettings, {"prefix": "s3cr3t-value", "bogus": "s3cr3t-value"}, "hello")
    assert "plugins.hello.bogus" in str(info.value) and "s3cr3t" not in str(info.value)
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k plugin_settings`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `plugins/host.py`**

```python
"""``Host``: everything a plugin may touch in a Workbench session (the plugin API, :data:`HOST_API`).

A plugin never sees the :class:`~netstead.workbench.session.Session`; it gets one ``Host`` bound to
its own id. Handlers run under the session lock, so ``Host`` methods are safe to call from them;
``submit_job`` work runs on its own thread, and the ``mutate``/``derive`` it calls take the lock.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ..paths import resolve_allowed

if TYPE_CHECKING:
    from corral.editing import Edit, EditResult

    from ..actions import BaseAction
    from ..jobs import JobContext
    from ..registry import NetworkHandle
    from ..session import Session
    from .spec import WorkbenchPlugin

__all__ = ["Host", "PluginSettingsError", "validate_plugin_settings"]


class PluginSettingsError(ValueError):
    """A plugin's ``[plugins.<id>]`` settings failed its model (the message never carries a value)."""


def validate_plugin_settings(model: type[BaseModel], data: dict[str, Any], plugin_id: str) -> BaseModel:
    """Validate ``data`` with ``model``; raise :class:`PluginSettingsError` naming keys, never values."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        where = ", ".join(f"plugins.{plugin_id}." + ".".join(str(p) for p in e["loc"]) + f" ({e['msg']})" for e in errors)
        message = f"invalid settings for plugin {plugin_id!r}: {where}"
    raise PluginSettingsError(message)  # outside ``except``: no __context__ carrying the input


class Host:
    """One plugin's handle on the session (see the cookbook page "Write a Workbench plugin")."""

    def __init__(self, session: Session, plugin: WorkbenchPlugin) -> None:
        """Bind ``plugin`` to ``session``."""
        self._session = session
        self._plugin = plugin

    @property
    def plugin_id(self) -> str:
        """This plugin's id (its namespace)."""
        return self._plugin.id

    @property
    def settings(self) -> BaseModel | None:
        """This plugin's ``[plugins.<id>]`` settings, validated by its ``settings_model`` (``None`` if it has none)."""
        model = self._plugin.settings_model
        if model is None:
            return None
        return validate_plugin_settings(model, self._session.settings.plugins.get(self.plugin_id, {}), self.plugin_id)

    @property
    def active(self) -> str | None:
        """The active network's id."""
        return self._session.active

    @property
    def selection(self) -> dict[str, Any] | None:
        """A copy of the shared selection payload (the same dict ``/api/state`` carries)."""
        # Not via ``state()``: that calls every plugin's ``state(host)``, which may read this property.
        with self._session._lock:
            return copy.deepcopy(self._session.selection)

    def network(self, net_id: str | None = None) -> NetworkHandle:
        """The handle for ``net_id`` (default: active). Read it; change it only via :meth:`mutate`."""
        with self._session._lock:  # Host is the one sanctioned friend of Session
            return self._session._handle(net_id)

    def mutate(self, net_id: str | None, edits: Sequence[Edit], *, note: str) -> list[EditResult]:
        """Apply corral edits all-or-nothing; lineage gets ``"<plugin id>: <note>"``."""
        return self._session.mutate(net_id, edits, note=f"{self.plugin_id}: {note}")

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network (a preview or scenario) and return its id."""
        return self._session.derive(net_id, label=label, note=f"{self.plugin_id}: {note}")

    def has_action(self, action_type: str) -> bool:
        """Whether any installed plugin (or core) registered ``action_type``: soft cross-plugin dependencies."""
        return self._session.actions.has(action_type)

    def dispatch(self, action: BaseAction | dict[str, Any]) -> Any:
        """Dispatch another Action (any plugin's or core's); it is recorded in history like any other."""
        return self._session.dispatch(action)

    def publish(self, name: str, payload: Any = None) -> None:
        """Send the browser a ``plugin`` event ``{plugin, name, payload}`` over the session's SSE stream."""
        self._session.events.publish({"type": "plugin", "plugin": self.plugin_id, "name": name, "payload": payload})

    def submit_job(self, label: str, fn: Callable[[JobContext], Any]) -> str:
        """Run ``fn(ctx)`` on a background job (progress and cancel in the jobs panel); return the job id."""
        return self._session.jobs.submit(f"{self.plugin_id}.job", label, fn).id

    def writable(self, path: str | Path) -> Path:
        """Resolve ``path`` for writing inside ``io.allowed_roots`` (raises ``PathNotAllowed`` outside it)."""
        return resolve_allowed(path, self._session.settings)
```

- [ ] **Step 4: Confirm the sandbox helper**

Run: `grep -n "^def resolve_allowed" packages/netstead/netstead/workbench/paths.py`
Expected: one match. `Host.writable` reuses it rather than adding a new sandbox check.

- [ ] **Step 5: Export `Host`**

In `plugins/__init__.py`, add `from .host import Host` and put `"Host"` in `__all__`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/netstead/netstead/workbench/plugins packages/netstead/tests/test_workbench_plugins.py
git commit -m "feat(workbench): Host — the plugin API surface (settings, read, mutate, derive, dispatch, events, jobs, sandbox)"
```

---

### Task 10: The session installs plugins and merges their state and settings

**Files:**
- Modify: `packages/netstead/netstead/workbench/session.py`
- Test: `packages/netstead/tests/test_workbench_plugins.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------- session install

from netstead.select.parse import StubParser  # noqa: E402
from netstead.workbench.actions import SetSetting  # noqa: E402
from netstead.workbench.session import ActionError, Session  # noqa: E402
from pydantic import ValidationError  # noqa: E402


@pytest.fixture
def make_session(tmp_path, isolated_env):
    def build(*plugins: WorkbenchPlugin, **overrides: Any) -> Session:
        return Session(
            project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=list(plugins), overrides=overrides
        )

    return build


def _status(session: Session, plugin_id: str):
    return next(s for s in session.plugin_status if s.id == plugin_id)


def test_plugin_action_dispatches_records_and_replays(make_session):
    session = make_session(make_hello())
    assert session.dispatch(Greet(name="Ada")) == "Hello, Ada!"
    assert session.dispatch({"type": "hello.greet", "name": "Bo"}) == "Hello, Bo!"
    entry = session.history[-1]
    assert entry.python == "app.do(Greet(name='Bo'))" and entry.imports == f"from {Greet.__module__} import Greet"
    assert _status(session, "hello").state == "loaded"


def test_plugin_state_is_merged_under_plugins(make_session):
    session = make_session(make_hello())
    session.dispatch(Greet(name="Ada"))
    assert session.state()["plugins"] == {"hello": {"greeted": ["Ada"]}}


def test_plugin_settings_come_from_the_layers(make_session):
    session = make_session(make_hello(), **{"plugins.hello.prefix": "Hi"})
    assert session.dispatch(Greet(name="Ada")) == "Hi, Ada!"


def test_invalid_plugin_settings_at_start_mark_the_plugin_error(make_session):
    session = make_session(make_hello(), **{"plugins.hello.bogus": 1})
    assert _status(session, "hello").state == "error" and "plugins.hello.bogus" in _status(session, "hello").error
    assert not session.actions.has("hello.greet")


def test_set_setting_validates_plugin_settings(make_session):
    session = make_session(make_hello())
    with pytest.raises(ActionError, match="plugin 'hello'"):
        session.dispatch(SetSetting(key="plugins.hello.prefix", value=3))
    assert session.dispatch(SetSetting(key="plugins.hello.prefix", value="Hey"))["value"] == "Hey"
    assert session.dispatch(Greet(name="Ada")) == "Hey, Ada!"
    assert session.dispatch(SetSetting(key="plugins.hello.prefix", value=None))["value"] is None
    assert session.dispatch(Greet(name="Ada")) == "Hello, Ada!"


def test_plugin_secrets_are_refused_like_any_setting():
    with pytest.raises(ValidationError):
        SetSetting(key="plugins.hello.api_key", value="x")


@pytest.mark.parametrize(
    ("plugin", "overrides", "state"),
    [
        (make_hello(), {"app.disabled_plugins": ["hello"]}, "disabled"),
        (make_hello(requires_api="2.0"), {}, "incompatible"),
        (make_hello(id="Bad Id"), {}, "error"),
    ],
)
def test_plugins_that_do_not_install(make_session, plugin, overrides, state):
    session = make_session(plugin, **overrides)
    assert session.plugin_status[0].state == state and not session.actions.has("hello.greet")


def test_failing_on_load_installs_nothing(make_session):
    def boom(host):
        raise RuntimeError("cannot start")

    session = make_session(make_hello(on_load=boom))
    assert _status(session, "hello").state == "error" and "cannot start" in _status(session, "hello").error
    assert not session.actions.has("hello.greet") and "hello" not in session.plugins


def test_failing_state_is_reported_not_raised(make_session):
    def bad_state(host):
        raise RuntimeError("oops")

    session = make_session(make_hello(state=bad_state))
    assert session.state()["plugins"]["hello"] == {"error": "RuntimeError: oops"}


def test_duplicate_plugin_ids_keep_the_first(make_session):
    session = make_session(make_hello(), make_hello())
    assert [s.state for s in session.plugin_status] == ["loaded", "error"]


class Shout(BaseAction):
    type: Literal["loud.shout"] = "loud.shout"
    name: str


def test_plugins_call_each_other_through_actions(make_session):
    def shout(host, action: Shout) -> str:
        if not host.has_action("hello.greet"):
            return action.name.upper()
        return host.dispatch(Greet(name=action.name)).upper()

    loud = WorkbenchPlugin(id="loud", name="Loud", version="0.1", requires_api=HOST_API, actions=(ActionSpec(Shout, shout),))
    assert make_session(loud).dispatch(Shout(name="ada")) == "ADA"
    both = make_session(make_hello(), loud)
    assert both.dispatch(Shout(name="ada")) == "HELLO, ADA!"
    assert [e.action["type"] for e in both.history] == ["hello.greet", "loud.shout"]  # inner first


class Widen(BaseAction):
    type: Literal["edit.widen"] = "edit.widen"
    mutates: ClassVar[bool] = True
    link_id: int


def test_a_plugin_previews_an_edit_on_a_derived_network(make_session, rdu_source):
    from corral.editing import Edit
    from netstead.workbench.actions import OpenNetwork

    def widen(host, action: Widen) -> str:
        preview = host.derive(None, label="Preview", note="preview")
        edit = Edit(op="update_rows", table="link", payload={"predicate": lambda t: t.link_id == action.link_id, "set": {"lanes": 9}})
        host.mutate(preview, [edit], note=f"widen {action.link_id}")
        return preview

    editor = WorkbenchPlugin(id="edit", name="Edit", version="0.1", requires_api=HOST_API, actions=(ActionSpec(Widen, widen),))
    session = make_session(editor)
    session.dispatch(OpenNetwork(source=rdu_source))
    first = int(session.registry.get("rdu-i40").links_df()["link_id"].iloc[0])
    preview = session.dispatch(Widen(link_id=first))
    summary = next(n for n in session.state()["networks"] if n["id"] == preview)
    assert summary["derived_from"] == "rdu-i40" and summary["lineage"] == ["edit: preview", f"edit: widen {first}"]
    assert session.registry.get("rdu-i40").version == 0
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k "not discover and not problems and not api_compatible and not plugin_settings_"`
Expected: FAIL with `TypeError: Session.__init__() got an unexpected keyword argument 'plugins'`.

- [ ] **Step 3: Implement in `session.py`**

1. **Imports.**
   - Add `import functools`.
   - Add `from collections.abc import Iterable`.
   - Add `from .plugins.discovery import PluginStatus, discover`.
   - Add `from .plugins.host import Host, PluginSettingsError, validate_plugin_settings`.
   - Add `from .plugins.spec import HOST_API, WorkbenchPlugin, api_compatible, problems`.
   - Add `replace` to the `dataclasses` import, which already has `asdict, dataclass, field, replace`.
2. **New keyword.** Add `plugins: Iterable[WorkbenchPlugin] | None = None,` to `__init__`'s keyword arguments. Document it in the docstring: "``plugins``: plugins to install (default ``None``: discover the installed ``netstead.workbench.plugins`` entry points)."
3. **Install call.** Add this at the end of `__init__`:

```python
        #: Installed plugins by id, and the :class:`Host` each one was given.
        self.plugins: dict[str, WorkbenchPlugin] = {}
        self._hosts: dict[str, Host] = {}
        #: One status per plugin seen at startup: loaded, disabled, incompatible or error (with the reason).
        self.plugin_status: list[PluginStatus] = []
        self._install_plugins(plugins)
```

4. **Install methods.** Add these after `_build_llm`:

```python
    def _install_plugins(self, plugins: Iterable[WorkbenchPlugin] | None) -> None:
        """Install ``plugins`` (default: discover installed entry points); record a status for each."""
        disabled = set(self.settings.app.disabled_plugins)
        if plugins is None:
            plugins, self.plugin_status = discover(disabled)
        for plugin in plugins:
            self.plugin_status.append(self._install(plugin, disabled))

    def _install(self, plugin: WorkbenchPlugin, disabled: set[str]) -> PluginStatus:
        """Check ``plugin``, run its ``on_load``, then register its Actions; never raises."""
        frontend = f"/plugins/{plugin.id}/{plugin.frontend}" if plugin.static_dir is not None else None
        status = PluginStatus(plugin.id, plugin.name, plugin.version, plugin.requires_api, "loaded", frontend=frontend)
        if plugin.id in disabled:
            return replace(status, state="disabled", frontend=None)
        if not api_compatible(plugin.requires_api):
            reason = f"needs plugin API {plugin.requires_api}; this netstead provides {HOST_API}"
            return replace(status, state="incompatible", error=reason, frontend=None)
        errors = problems(plugin, self.actions, self.plugins)
        host = Host(self, plugin)
        if not errors:
            try:
                host.settings  # noqa: B018  (validates [plugins.<id>] now, so a bad file is reported at startup)
                if plugin.on_load is not None:
                    plugin.on_load(host)
            except PluginSettingsError as exc:
                errors.append(str(exc))
            except Exception as exc:  # boundary: third-party code must not stop the session
                logger.exception("workbench plugin %r failed to load", plugin.id)
                errors.append(f"{type(exc).__name__}: {exc}")
        if errors:
            return replace(status, state="error", error="; ".join(errors), frontend=None)
        for spec in plugin.actions:
            self.actions.register(spec.model)
            self._handlers[spec.model.action_type()] = functools.partial(spec.handler, host)
        self.plugins[plugin.id] = plugin
        self._hosts[plugin.id] = host
        return status

    def _plugin_state(self) -> dict[str, Any]:
        """Each plugin's contributed state; a failing ``state()`` is logged and shown, never raised."""
        out: dict[str, Any] = {}
        for plugin_id, plugin in self.plugins.items():
            if plugin.state is None:
                continue
            try:
                out[plugin_id] = copy.deepcopy(plugin.state(self._hosts[plugin_id]))
            except Exception as exc:  # boundary: third-party code
                logger.exception("workbench plugin %r state() failed", plugin_id)
                out[plugin_id] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    def _check_plugin_settings(self, settings: Settings) -> None:
        """Raise :class:`ActionError` if ``settings`` breaks any installed plugin's settings model."""
        for plugin_id, plugin in self.plugins.items():
            if plugin.settings_model is not None:
                try:
                    validate_plugin_settings(plugin.settings_model, settings.plugins.get(plugin_id, {}), plugin_id)
                except PluginSettingsError as exc:
                    raise ActionError(str(exc)) from None
```

5. **`state()`.** Add `"plugins": self._plugin_state(),` to the returned dict.
6. **`_do_set_setting`.** Check plugin settings before anything is persisted or applied, and let a plugin key go back to "unset". Replace the body of the `try:` block with:

```python
        try:
            if action.scope == "session":
                overrides = {**self._overrides, action.key: action.value}
                loaded = load_settings(project_dir=self.project_dir, overrides=overrides, environ=self._environ)
                self._check_plugin_settings(loaded.settings)
                self._overrides = overrides
            else:
                if key.startswith("plugins."):  # check first: a saved file a plugin rejects would break startup
                    preview = load_settings(
                        project_dir=self.project_dir,
                        overrides={**self._overrides, action.key: action.value},
                        environ=self._environ,
                    )
                    self._check_plugin_settings(preview.settings)
                save_setting(
                    action.key, action.value, scope=action.scope, project_dir=self.project_dir, environ=self._environ
                )
                loaded = load_settings(project_dir=self.project_dir, overrides=self._overrides, environ=self._environ)
            value = _value_or_unset(loaded.settings, action.key)
        except SettingsError as exc:
            raise ActionError(str(exc)) from exc
```

Then add this module-level helper next to `_follows_endpoint`:

```python
def _value_or_unset(settings: Settings, key: str) -> Any:
    """``get_value`` for ``key``; a removed plugin setting (no model default at this layer) reads as ``None``."""
    try:
        return get_value(settings, key)
    except SettingsError:
        if key.strip().lower().startswith("plugins."):
            return None
        raise
```

- [ ] **Step 4: Run all the plugin and workbench tests**

Run: `uv run pytest packages/netstead/tests -k workbench -q`
Expected: PASS. `test_replay...` (`replay.state() == session.state()`) still passes, because both sides get `"plugins": {}`.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/session.py packages/netstead/tests/test_workbench_plugins.py
git commit -m "feat(workbench): sessions install plugins (checks, on_load, actions), merge plugin state, validate plugin settings"
```

---

### Task 11: A real entry point, end to end

**Files:**
- Test: `packages/netstead/tests/test_workbench_plugins.py` (append)

- [ ] **Step 1: Write the test.** It installs a throwaway distribution by putting a `.dist-info` folder on `sys.path`, so the real `importlib.metadata` path runs.

```python
_FAKE_PLUGIN = '''
from typing import Literal
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin


class Ping(BaseAction):
    type: Literal["fake.ping"] = "fake.ping"


def plugin():
    return WorkbenchPlugin(id="fake", name="Fake", version="0.1", requires_api=HOST_API,
                           actions=(ActionSpec(Ping, lambda host, action: "pong"),))
'''


def test_installed_entry_point_is_discovered_by_default(tmp_path, isolated_env, monkeypatch):
    site = tmp_path / "site"
    dist = site / "netstead_fake_plugin-0.1.dist-info"
    dist.mkdir(parents=True)
    (site / "netstead_fake_plugin.py").write_text(_FAKE_PLUGIN, encoding="utf-8")
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: netstead-fake-plugin\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text(
        "[netstead.workbench.plugins]\nfake = netstead_fake_plugin:plugin\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(__import__("sys").modules, "netstead_fake_plugin", raising=False)

    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser())  # plugins=None: discover
    assert _status(session, "fake").state == "loaded"
    assert session.dispatch({"type": "fake.ping"}) == "pong"
    assert session.history[-1].imports == "from netstead_fake_plugin import Ping"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k installed_entry_point`
Expected: PASS, since Tasks 6 and 10 already implement this path. If it fails, the bug is in `discover` or `_install_plugins`. Fix it there, not in the test.

- [ ] **Step 3: Commit**

```bash
git add packages/netstead/tests/test_workbench_plugins.py
git commit -m "test(workbench): plugin discovered through a real entry point"
```

---

### Task 12: Serve plugins: `GET /api/plugins`, routers and static files

**Files:**
- Modify: `packages/netstead/netstead/workbench/server.py`
- Test: `packages/netstead/tests/test_workbench_plugin_routes.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_plugin_routes.py`:

```python
"""HTTP surface of Workbench plugins: status listing, plugin routers, plugin static files, plugin actions."""

from typing import Literal

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


def _router(host) -> APIRouter:
    router = APIRouter()

    @router.get("/whoami")
    def whoami() -> dict:
        return {"plugin": host.plugin_id}

    return router


def _broken_router(host) -> APIRouter:
    raise RuntimeError("router exploded")


@pytest.fixture
def client(tmp_path, isolated_env):
    static = tmp_path / "hello_static"
    static.mkdir()
    (static / "main.js").write_text("export function activate(wb) {}\n", encoding="utf-8")
    hello = WorkbenchPlugin(
        id="hello", name="Hello", version="0.1", requires_api=HOST_API,
        actions=(ActionSpec(Greet, lambda host, a: f"Hello, {a.name}!"),), router=_router, static_dir=static,
    )
    broken = WorkbenchPlugin(id="broken", name="Broken", version="0.1", requires_api=HOST_API, router=_broken_router)
    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[hello, broken])
    return TestClient(build_app(session))


def test_plugins_listing(client):
    body = client.get("/api/plugins").json()
    assert body["host_api"] == HOST_API
    by_id = {p["id"]: p for p in body["plugins"]}
    assert by_id["hello"]["state"] == "loaded" and by_id["hello"]["frontend"] == "/plugins/hello/main.js"
    assert by_id["broken"]["state"] == "error" and "router exploded" in by_id["broken"]["error"]


def test_plugin_router_and_static_are_mounted(client):
    assert client.get("/api/plugins/hello/whoami").json() == {"plugin": "hello"}
    r = client.get("/plugins/hello/main.js")
    assert r.status_code == 200 and "activate" in r.text


def test_plugin_action_over_http(client):
    r = client.post("/api/actions", json={"type": "hello.greet", "name": "Ada"})
    assert r.status_code == 200 and r.json()["result"] == "Hello, Ada!"
    assert r.json()["entry"]["imports"].endswith("import Greet")
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugin_routes.py -q`
Expected: FAIL with 404s.

- [ ] **Step 3: Implement in `server.py`**

Add `import logging`, `from dataclasses import replace`, `from .plugins import HOST_API`, and `logger = logging.getLogger(__name__)`. In `build_app`, after the `llm_router` include and before `@app.get("/")`, add:

```python
    _mount_plugins(app, session)

    @app.get("/api/plugins")
    def plugins() -> dict:
        return {"host_api": HOST_API, "plugins": [s.to_dict() for s in session.plugin_status]}
```

Add this module-level function:

```python
def _mount_plugins(app: FastAPI, session: Session) -> None:
    """Mount each loaded plugin's router (``/api/plugins/<id>``) and static dir (``/plugins/<id>``).

    A router factory that raises marks that plugin ``error`` in ``session.plugin_status``; its Actions
    stay registered (Python and replays still work), but the browser won't load its front end.
    """
    for index, status in enumerate(session.plugin_status):
        plugin = session.plugins.get(status.id)
        if plugin is None or status.state != "loaded":
            continue
        try:
            if plugin.router is not None:
                app.include_router(plugin.router(session._hosts[plugin.id]), prefix=f"/api/plugins/{plugin.id}")
            if plugin.static_dir is not None:
                app.mount(f"/plugins/{plugin.id}", StaticFiles(directory=plugin.static_dir), name=f"plugin-{plugin.id}")
        except Exception as exc:  # boundary: third-party code must not stop the app
            logger.exception("mounting workbench plugin %r failed", plugin.id)
            session.plugin_status[index] = replace(status, state="error", error=f"{type(exc).__name__}: {exc}", frontend=None)
```

- [ ] **Step 4: Run the server and plugin route tests**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugin_routes.py packages/netstead/tests/test_workbench_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/netstead/netstead/workbench/server.py packages/netstead/tests/test_workbench_plugin_routes.py
git commit -m "feat(workbench): serve plugins — /api/plugins, plugin routers, plugin static files"
```

---

### Task 13: The `hello` example plugin package

**Files:**
- Create: `examples/workbench-plugin-hello/pyproject.toml`
- Create: `examples/workbench-plugin-hello/netstead_hello/__init__.py`
- Create: `examples/workbench-plugin-hello/netstead_hello/static/main.js`
- Create: `examples/workbench-plugin-hello/README.md`
- Test: `packages/netstead/tests/test_workbench_plugins.py` (append)

- [ ] **Step 1: Write the failing test.** It imports the example from source, with no install needed.

```python
def test_hello_example_plugin_works(tmp_path, isolated_env, monkeypatch):
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / "examples" / "workbench-plugin-hello"
    monkeypatch.syspath_prepend(str(example))
    import netstead_hello

    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[netstead_hello.plugin()])
    assert session.dispatch({"type": "hello.greet", "name": "Ada"}) == "Hello, Ada!"
    assert session.state()["plugins"]["hello"] == {"greeted": 1}
    assert (netstead_hello.plugin().static_dir / "main.js").is_file()
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k hello_example`
Expected: FAIL with `ModuleNotFoundError: netstead_hello`.

- [ ] **Step 3: Create the package**

`examples/workbench-plugin-hello/pyproject.toml`:

```toml
[project]
name = "netstead-hello"
version = "0.1.0"
description = "A minimal netstead Workbench plugin: one Action, one setting, state, a route, and a front-end module."
requires-python = ">=3.11"
dependencies = ["netstead"]

[project.entry-points."netstead.workbench.plugins"]
hello = "netstead_hello:plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["netstead_hello"]
```

`examples/workbench-plugin-hello/netstead_hello/__init__.py`:

```python
"""A minimal netstead Workbench plugin, to copy from (see the "Write a Workbench plugin" cookbook page)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, Host, WorkbenchPlugin
from pydantic import BaseModel, ConfigDict


class Greet(BaseAction):
    """Greet someone (recorded in history like any Action; replays as ``app.do(Greet(name=...))``)."""

    type: Literal["hello.greet"] = "hello.greet"
    name: str


class HelloSettings(BaseModel):
    """``[plugins.hello]`` in netstead.toml."""

    model_config = ConfigDict(extra="forbid")
    prefix: str = "Hello"


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin whose state lives in this closure (one per session)."""
    greeted = {"count": 0}

    def greet(host: Host, action: Greet) -> str:
        greeted["count"] += 1
        host.publish("greeted", {"name": action.name})
        return f"{host.settings.prefix}, {action.name}!"

    def router(host: Host) -> APIRouter:
        api = APIRouter()

        @api.get("/count")
        def count() -> dict[str, Any]:
            return {"greeted": greeted["count"]}

        return api

    return WorkbenchPlugin(
        id="hello",
        name="Hello",
        version="0.1.0",
        requires_api=HOST_API,
        actions=(ActionSpec(Greet, greet),),
        router=router,
        static_dir=Path(__file__).parent / "static",
        settings_model=HelloSettings,
        state=lambda host: {"greeted": greeted["count"]},
    )
```

`examples/workbench-plugin-hello/netstead_hello/static/main.js`. The browser loader that calls this arrives in Part 2; until then the file is just served.

```javascript
// Loaded by the Workbench front end (Part 2): register UI against the host object `wb`.
export function activate(wb) {
  wb.registerCommand?.({
    id: "hello.greet",
    title: "Say hello",
    contexts: ["palette"],
    run: () => wb.api.dispatch({ type: "hello.greet", name: "world" }),
  });
}
```

`examples/workbench-plugin-hello/README.md`:

````markdown
# netstead-hello

A minimal netstead Workbench plugin. Install it next to netstead, and `netstead app` loads it:

```bash
uv pip install -e examples/workbench-plugin-hello
```

The plugin adds the following:

- the `hello.greet` Action
- the `[plugins.hello] prefix` setting
- state under `plugins.hello`
- `GET /api/plugins/hello/count`
- a front-end module at `/plugins/hello/main.js`

To turn it off without uninstalling it, set `[app] disabled_plugins = ["hello"]`.
````

- [ ] **Step 4: Run the example test**

Run: `uv run pytest packages/netstead/tests/test_workbench_plugins.py -q -k hello_example`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add examples/workbench-plugin-hello packages/netstead/tests/test_workbench_plugins.py
git commit -m "docs(examples): netstead-hello, a minimal Workbench plugin package"
```

---

### Task 14: The authoring guide

**Files:**
- Create: `packages/netstead/docs/cookbook/workbench-plugins.md`
- Modify: `packages/netstead/mkdocs.yml` (Cookbook nav)
- Modify: `packages/netstead/docs/cookbook/index.md` (link)

- [ ] **Step 1: Write `workbench-plugins.md`.** Every Python block is illustrative, so mark each one `<!-- doctest: skip -->` (the convention `test_documented_python_contract` honours). Only reference symbols that exist, because `test_documented_api_contract` checks dotted names. Cover these sections, in this order:

  1. **What a plugin is.** It is a pip package with an entry point in `netstead.workbench.plugins`, named after the plugin id. Plugins are full-trust, in-process code, like pytest plugins. Repeat the warning that `--host 0.0.0.0` exposes the app on the LAN.
  2. **The UX contract.** Copy the eight principles from `docs/design/2026-10-05-workbench-plugins-design.md` §"UX principles", condensed to one or two lines each. Keep the full noun rules: namespaced and exposed, projected onto core nouns, stable `{plugin}:{kind}/{id}` ids, and cross-plugin interaction by Action.
  3. **A walk through `examples/workbench-plugin-hello`.** Show the entry point, `BaseAction` subclasses with `type="<id>.<name>"`, `ActionSpec(model, handler)` and the handler signature `(host, action)`, `settings_model` with `[plugins.<id>]`, `state`, `router`, `static_dir` and `frontend`.
  4. **The `Host` reference.** Include `settings`, `active`, `selection`, `network`, `mutate` (all-or-nothing, lineage), `derive` (copy-on-write preview or scenario), `has_action` / `dispatch` (soft dependencies, history order), `publish`, `submit_job` and `writable`.
  5. **Testing a plugin.** Show `Session(plugins=[plugin()], project_dir=tmp_path, environ={...})` in a pytest fixture, then `session.dispatch(...)`, `session.state()["plugins"]` and `session.plugin_status`.
  6. **Startup outcomes.** Cover the `loaded`, `disabled`, `incompatible` and `error` states and where they show (`GET /api/plugins`).
  7. **Versioning.** `HOST_API` is `major.minor`, and `requires_api` matches when the major is the same and the host's minor is at least as new. The API is provisional until netstead v1.0.

- [ ] **Step 2: Add the page to the navigation.** In `mkdocs.yml`, under `- Cookbook:`, right after `- Explore in the Workbench: cookbook/workbench.md`, add:

```yaml
      - Write a Workbench plugin: cookbook/workbench-plugins.md
```

Add a matching bullet to `docs/cookbook/index.md` next to the Workbench entry.

- [ ] **Step 3: Run the docs contract tests**

Run: `uv run pytest packages/netstead/tests -q -k "documented"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/netstead/docs packages/netstead/mkdocs.yml
git commit -m "docs(netstead): Write a Workbench plugin (contract, Host reference, testing, versioning)"
```

---

### Task 15: Full verification

**Files:** none (fixes only).

- [ ] **Step 1: Lint and format**

```bash
uv run ruff format packages/netstead examples/workbench-plugin-hello
uv run ruff check --fix packages/netstead examples/workbench-plugin-hello
```

Expected: clean.

- [ ] **Step 2: Import contracts**

Run: `uv run lint-imports`
Expected: all contracts kept. Workbench core must not import any plugin package or `netstead.map`.

- [ ] **Step 3: Run the full suites, since this is about to merge**

```bash
uv run pytest packages/netstead/tests --doctest-modules packages/netstead/netstead -q
uv run pytest packages/corral/tests --doctest-modules packages/corral/corral -q
```

Expected: PASS, apart from any failures noted in Task 0.

- [ ] **Step 4: Manual check in a browser**

```bash
uv pip install -e examples/workbench-plugin-hello
uv run netstead app
```

1. In the in-app browser, open `http://127.0.0.1:8850/api/plugins`. Expected: `hello` with state `loaded` and frontend `/plugins/hello/main.js`.
2. POST `{"type":"hello.greet","name":"Ada"}` to `/api/actions` from the page console, using `fetch` with a JSON body. Expected: `"Hello, Ada!"`.
3. In the History panel, "copy all" shows `from netstead_hello import Greet`.
4. Set `[app] disabled_plugins = ["hello"]` in `netstead.toml` and restart. Expected: `/api/plugins` shows `disabled`, and posting the action returns 422.
5. Uninstall the example afterwards: `uv pip uninstall netstead-hello`.

- [ ] **Step 5: Commit any fixes, then hand off**

Use superpowers:finishing-a-development-branch. Open the PR against `main`, or against `feat/workbench-p1b` if P1b hasn't merged yet.

---

## Self-review against the spec

| Spec item | Where it's covered |
|---|---|
| Entry-point discovery with isolation | Tasks 6, 10 and 11 |
| `WorkbenchPlugin` / `ActionSpec` | Task 5 |
| Version check | Tasks 5 and 10 |
| `app.disabled_plugins` | Tasks 4 and 10 |
| Action registry, with core Actions on it | Tasks 1 and 2 (one handler table; core preparing and jobs stay core-only) |
| Namespaced types | Task 5 |
| Copy as Python with real imports | Tasks 1, 2 and 3 |
| Plugin settings `[plugins.<id>]`, validated, secrets refused | Tasks 4, 9 and 10 |
| Plugin state merged under `plugins.<id>` | Task 10 |
| `Host`: `networks` / `selection` / `mutate` / `derive` / `actions.has` / `dispatch` / `events.publish` / jobs / `paths.writable` / `settings` | Tasks 8 and 9. `subscribe` is deferred (see "Deviations"). |
| Derived networks and the dirty marker (`derived_from`) | Tasks 7 and 8 |
| Routers at `/api/plugins/<id>`, static files at `/plugins/<id>/`, `GET /api/plugins` | Task 12 |
| Example plugin and authoring guide, including the UX principles and the noun rules | Tasks 13 and 14 |
| Front-end slots, `wb`, `schemaForm`, Plugins settings UI | Part 2 plan (after P1b) |
| Trust model documented | Task 14 |
