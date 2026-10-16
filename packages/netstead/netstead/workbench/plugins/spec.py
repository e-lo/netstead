"""What a Workbench plugin declares (:class:`WorkbenchPlugin`) and the checks run before installing it."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..actions import CORE_ACTIONS, ActionRegistry, BaseAction

if TYPE_CHECKING:
    from .host import Host

__all__ = ["HOST_API", "ActionSpec", "WorkbenchPlugin", "api_compatible", "problems"]

#: The plugin API this netstead provides: ``major.minor``. A minor bump only adds; a major bump breaks.
#: Provisional until netstead v1.0 (it may change without a major bump before then).
#: 1.1: the browser host object ``wb`` (workspaces, dock panels, commands, map layers, schema forms).
HOST_API = "1.1"

#: A plugin id: the namespace for its Action types, routes, state, settings and static files (``fullmatch``).
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9_]*")
#: Plugin ids the front end uses for itself: ``"core"`` owns core's workspaces, panels, commands and layers
#: (an owner tag that skips the id prefix check, and whose rollback would remove core's own registrations).
_RESERVED_IDS = frozenset({"core"})
#: Names a replayed script binds from ``netstead.workbench``: a plugin Action class must not rebind one.
_RESERVED_NAMES = frozenset({"Session", *(model.__name__ for model in CORE_ACTIONS)})


@dataclass(frozen=True)
class ActionSpec:
    """One plugin Action: its model and the handler that applies it (``handler(host, action) -> result``).

    The model is a module-level :class:`BaseAction` subclass (importable, so "copy session as Python"
    can write ``from <module> import <Class>``) whose class name no other Action uses. Its field values
    must round-trip through ``repr``: the replay snippet is ``app.do(<Class>(field=<repr(value)>, ...))``.
    """

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
    if not isinstance(plugin.id, str) or not _PLUGIN_ID.fullmatch(plugin.id):
        found.append(f"plugin id {plugin.id!r} must match [a-z][a-z0-9_]*")
    if plugin.id in _RESERVED_IDS:
        found.append(f"plugin id {plugin.id!r} is reserved for the Workbench itself")
    if plugin.id in taken:
        found.append(f"another plugin already uses the id {plugin.id!r}")
    names = _RESERVED_NAMES | {registry.model(t).__name__ for t in registry.types()}
    seen_types: set[str] = set()
    for spec in plugin.actions:
        model = spec.model
        if not (isinstance(model, type) and issubclass(model, BaseAction)):
            found.append(f"{model!r} is not a BaseAction subclass")
            continue
        action_type = model.action_type()
        if action_type is None:
            found.append(f'{model.__name__} needs a `type: Literal["<type>"] = "<type>"` field (one value)')
            continue
        if not action_type.startswith(f"{plugin.id}.") or action_type == f"{plugin.id}.":
            found.append(f"action type {action_type!r} must start with {plugin.id + '.'!r} and then name the action")
        if model.runs_as_job:
            found.append(f"{action_type}: plugin actions can't be job actions; call host.submit_job from the handler")
        if registry.has(action_type):
            found.append(f"action type {action_type!r} is already registered")
        if action_type in seen_types:
            found.append(f"action type {action_type!r} is declared twice")
        seen_types.add(action_type)
        found.extend(_schema_problems(model, action_type))
        found.extend(_replay_problems(model, names))
        names = names | {model.__name__}
    return found


def _schema_problems(model: type[BaseAction], action_type: str) -> list[str]:
    """Why ``model`` has no JSON schema (``GET /api/actions`` and the browser's generated forms need one).

    A field pydantic can validate but not describe (an ``arbitrary_types_allowed`` class, say) fails here,
    at install, rather than in every later request for the Action catalog.
    """
    try:
        model.model_json_schema()
    # boundary: a third-party model (or its custom ``__get_pydantic_json_schema__``) may raise anything
    except Exception as exc:
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
        return [f"{action_type}: its fields have no JSON schema ({reason})"]
    return []


def _replay_problems(model: type[BaseAction], names: Collection[str]) -> list[str]:
    """Why a replayed script couldn't ``from <module> import <name>`` this class unambiguously."""
    found: list[str] = []
    if model.__name__ in names:
        found.append(f"class name {model.__name__!r} is already used by another Action (or Session) in scripts")
    if model.__qualname__ != model.__name__ or model.__module__ == "__main__":
        found.append(f"{model.__qualname__} must be defined at the top level of an importable module")
    return found
