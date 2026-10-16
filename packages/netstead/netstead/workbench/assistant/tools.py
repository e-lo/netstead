"""The assistant's tool vocabulary: the Actions it may call, as provider-neutral tools, and back again.

The model sees each allowed Action's JSON schema as a tool, flattened and stripped of what it must not set. A tool
call comes back through :func:`to_action`, which raises ``ValueError`` (so the structured-output loop re-prompts)
for anything the assistant may not do. It never invents ids: ``on_selection`` stands for the current selection.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from netstead.llm.secrets import looks_like_secret
from netstead.llm.types import Tool
from netstead.select.parse import INTENT_TOOL, intent_from_payload

from ..actions import BaseAction

if TYPE_CHECKING:
    from ..session import Session

__all__ = [
    "NEVER",
    "PROTECTED_SETTINGS",
    "ToolSpec",
    "ToolView",
    "action_type_of",
    "allowlist_problems",
    "compact_drops",
    "lenient_arguments",
    "offerable",
    "to_action",
    "tool_name",
    "tool_specs",
    "tool_view",
    "warn_allowlist",
]

logger = logging.getLogger(__name__)

#: Actions never offered, whatever the settings say (paths the model would invent; unsaved edits thrown away).
NEVER = frozenset({"open_network", "close_network"})
#: Setting sections the assistant may never change: keys, endpoints, its own limits, the sandbox, and the sections
#: whose endpoint URLs would receive a bbox or a viewport (``osm``, ``overture``, ``viz``).
PROTECTED_SETTINGS = (
    "llm",
    "select",
    "assistant",
    "app",
    "io",
    "credentials",
    "plugins",
    "osm",
    "overture",
    "viz",
)
#: Fields hidden from the model, per Action type: it never approves or accepts on the user's behalf.
_HIDDEN = {"build_network": frozenset({"approved"}), "save_network": frozenset({"accept_warnings"})}
#: The Actions whose ``ids`` the model may give as ``on_selection: true`` instead.
_ON_SELECTION_ACTIONS = ("edit_cells", "delete_rows")
_ON_SELECTION = {
    "type": "boolean",
    "description": "Act on the links of the current selection (table link) instead of listing ids.",
}
_DRAFTED = " (drafted for the user to approve)"
#: The drafted tools a compact tool set keeps (``assistant.compact_tools``): the everyday edits. The rest (saving a
#: copy, the opt-ins, a plugin's mutating Actions) need a path or a judgement a small model rarely gets right.
_COMPACT_DRAFTED = frozenset({"edit_cells", "delete_rows", "undo_edit"})
#: One example for the tools small models call with the wrong shape (measured: ``navigate(link=170)``,
#: ``style(show=…)``, a request's second part dropped), appended to the tool's description.
_EXAMPLES = {
    "navigate": ' Give exactly one target, e.g. {"record": {"table": "link", "id": 170}} or {"to_selection": true}.',
    "style": ' E.g. {"color_by": "lanes"} or {"width_by": "lanes"}.',
    "save_selection": ' Call it after select when the user also names the selection ("select X, then save it as Y").',
    "edit_cells": (
        ' E.g. "set the speed limit to 25 on the selection" is {"on_selection": true, "values": {"free_speed": 25}}.'
    ),
}
#: Read-only tools whose every field means "unchanged" when left out (a partial update), so a malformed optional field
#: may be dropped as well as an unknown one (:func:`lenient_arguments`).
_PARTIAL_UPDATE = frozenset({"style"})
#: The select tool's extra field: the selection's name in the user's words (``Select.utterance``).
_UTTERANCE = {"type": "string", "description": "A short phrase naming the selection, in the user's words."}


@dataclass(frozen=True)
class ToolSpec:
    """One Action offered as a tool: the tool, the Action type, whether it is drafted, and its plugin (or None)."""

    tool: Tool
    action_type: str
    mutates: bool
    plugin: str | None


@dataclass(frozen=True)
class ToolView:
    """What :func:`to_action` reads from the session, taken at one moment: the active network and the selection."""

    active: str | None
    selection: dict[str, Any] | None
    networks: frozenset[str] | None = None  # the open networks' ids (None: not checked)


def tool_view(session: Session) -> ToolView:
    """A consistent snapshot of the session for :func:`to_action` (one critical section, a copied selection)."""
    with session.consistent():  # the active id, the open networks and the selection must come from the same moment
        return ToolView(session.active, copy.deepcopy(session.selection), frozenset(session.registry.ids()))


def tool_name(action_type: str) -> str:
    """The tool name for ``action_type`` (tool names allow only ``[A-Za-z0-9_-]``).

    >>> tool_name("hello.greet"), tool_name("style")
    ('hello__greet', 'style')
    """
    return action_type.replace(".", "__")


def action_type_of(name: str) -> str:
    """The Action type a tool name stands for (the inverse of :func:`tool_name`).

    >>> action_type_of("hello__greet"), action_type_of("edit_cells")
    ('hello.greet', 'edit_cells')
    """
    return name.replace("__", ".")


#: Allowlist problems already logged (each is logged once per process, however often the tools are listed).
_WARNED: set[str] = set()


def warn_allowlist(session: Session) -> list[str]:
    """Log, once each, the names in ``assistant.actions`` / ``assistant.plugins`` this session doesn't know.

    A plugin can be uninstalled after it was opted in, so an unknown name is warned about, never fatal. Returns the
    problems (logged or not), for the Settings panel.
    """
    section = session.settings.assistant
    problems = allowlist_problems(session, section.actions, section.plugins)
    for problem in problems:
        if problem not in _WARNED:
            _WARNED.add(problem)
            logger.warning("settings: %s (ignored)", problem)
    return problems


def tool_specs(session: Session, *, warn: bool = True, compact: bool = False) -> list[ToolSpec]:
    """The tools this session's assistant may call, in registry order (settings ``assistant.actions`` / ``plugins``).

    A core Action is offered when ``assistant.actions`` lists it; a plugin's when ``assistant.plugins`` lists the
    plugin. Neither ever offers :data:`NEVER`, nor an Action that sets ``assistant = False``. ``warn=False`` skips
    the allowlist check, for a caller that has just made it (:func:`warn_allowlist`). ``compact`` offers fewer of
    the allowed tools (:func:`compact_drops`); it never adds one.
    """
    settings = session.settings.assistant
    if warn:
        warn_allowlist(session)
    dropped = compact_drops(session) if compact else frozenset()
    specs = []
    for action_type in session.actions.types():
        model = session.actions.model(action_type)
        owner = session.action_owner(action_type)
        allowed = owner in settings.plugins if owner else action_type in settings.actions
        if not offerable(action_type, model) or not allowed:
            continue
        if action_type in dropped or (compact and model.mutates and action_type not in _COMPACT_DRAFTED):
            continue
        try:
            tool = _tool(action_type, model)
        except Exception:  # boundary: a plugin's model may not describe itself; the others are still offered
            logger.exception("no assistant tool for workbench action %r", action_type)
            continue
        specs.append(ToolSpec(tool, action_type, model.mutates, owner))
    return specs


def compact_drops(session: Session) -> frozenset[str]:
    """The read-only tools a compact tool set leaves out because they can't apply now.

    Switching networks with one open, reusing a saved selection with none saved, pinning an anchor with nothing
    selected: a small model offered them calls them anyway (measured: "open my network from the desktop" became
    ``set_active_network`` with no id).
    """
    with session.consistent():
        out = set()
        if len(session.registry.ids()) < 2:
            out.add("set_active_network")
        if not session.saved:
            out.add("use_selection")
        if not session.selection:
            out.add("pin_anchor")
    return frozenset(out)


def offerable(action_type: str, model: type[BaseAction]) -> bool:
    """Whether an Action may be offered to the assistant at all (never :data:`NEVER`, nor ``assistant = False``)."""
    return action_type not in NEVER and model.assistant


def _tool(action_type: str, model: type[BaseAction]) -> Tool:
    if action_type == "select":  # the selection parser's own tool, byte for byte, plus the utterance
        schema = copy.deepcopy(INTENT_TOOL["input_schema"])
        schema["properties"] = {"utterance": dict(_UTTERANCE), **schema["properties"]}
        schema["required"] = ["utterance"]
        return Tool("select", INTENT_TOOL["description"] + " Also give a short utterance naming it.", schema)
    doc = (model.__doc__ or action_type).strip().split("\n\n", 1)[0].replace("\n", " ")
    description = doc + (_DRAFTED if model.mutates else "") + _EXAMPLES.get(action_type, "")
    schema = _flat(model.model_json_schema())
    hidden = {"type", *model.replay_omits, *_HIDDEN.get(action_type, ())}
    schema["properties"] = {k: v for k, v in schema.get("properties", {}).items() if k not in hidden}
    required = [r for r in schema.get("required", []) if r not in hidden]
    if action_type in _ON_SELECTION_ACTIONS:
        schema["properties"]["on_selection"] = dict(_ON_SELECTION)
        required = [r for r in required if r != "ids"]
    schema.pop("required", None)
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return Tool(tool_name(action_type), description, schema)


def _flat(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with its ``$defs`` inlined and every ``title`` annotation dropped (small models need it flat).

    >>> _flat({"title": "A", "properties": {"title": {"title": "T", "type": "string"}, "b": {"$ref": "#/$defs/B"}},
    ...        "$defs": {"B": {"title": "B", "type": "integer"}}})
    {'properties': {'title': {'type': 'string'}, 'b': {'type': 'integer'}}}
    """
    defs = schema.get("$defs", {})

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 40:
            raise ValueError("schema nests too deeply to inline")
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].rsplit("/", 1)[-1]]
                return walk({**target, **{k: v for k, v in node.items() if k != "$ref"}}, depth + 1)
            # A ``title`` annotation is a string; a *property* named ``title`` (a dict) is kept.
            return {
                k: walk(v, depth + 1)
                for k, v in node.items()
                if k != "$defs" and not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [walk(v, depth + 1) for v in node]
        return node

    return walk(schema)


def lenient_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``arguments`` without the keys a read-only tool may ignore, and what was left out (key names, never values).

    Small models add keys a tool doesn't have (``style(show=…)``, ``navigate(link=…)``). For a read-only tool these
    are dropped: an unknown key always, and a malformed optional field too on a partial update (``style``, where a
    field left out means "unchanged"). A mutating tool is never touched: what a draft would do is exactly what the
    model asked, or the call goes back for repair. Nor is a call left with no arguments at all (it goes back too).

    >>> spec = ToolSpec(Tool("navigate", "", {"properties": {"to_selection": {"type": "boolean"}}}), "navigate",
    ...                 False, None)
    >>> lenient_arguments(spec, {"to_selection": True, "zoom": 3})
    ({'to_selection': True}, ['zoom (not a field of navigate)'])
    """
    if spec.mutates:
        return arguments, []
    schema = spec.tool.input_schema
    props = schema.get("properties", {})
    required = set(schema.get("required", ()))
    kept: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in arguments.items():
        name = str(key)[:40]
        if key not in props:
            ignored.append(f"{name} (not a field of {spec.tool.name})")
        elif spec.action_type in _PARTIAL_UPDATE and key not in required and not _fits(value, props[key]):
            ignored.append(f"{name} (not valid)")
        else:
            kept[key] = value
    if not ignored or (arguments and not kept):
        return arguments, []
    return kept, ignored


def _fits(value: Any, schema: dict[str, Any]) -> bool:
    from jsonschema import Draft202012Validator  # the [nl] extra, as in netstead.llm.structured

    return Draft202012Validator(schema).is_valid(value)


def to_action(
    session: Session, spec: ToolSpec, arguments: dict[str, Any], *, view: ToolView | None = None
) -> BaseAction:
    """The Action a tool call stands for; ``ValueError`` (never echoing a value) for anything the assistant can't do.

    ``select`` becomes ``Select(utterance=…, query=…)``; ``on_selection: true`` becomes the selection's link ids
    (refused when nothing is selected, or when the selection is on another network); a ``set_setting`` under
    :data:`PROTECTED_SETTINGS` is refused. ``view`` is the session as the call should see it (default: a snapshot
    taken now, under the session lock), so the active network and the selection always agree.
    """
    args = dict(arguments)
    view = view if view is not None else tool_view(session)
    net_id = args.get("net_id")
    if isinstance(net_id, str) and view.networks is not None and net_id not in view.networks:
        raise ValueError(f"no open network is called {net_id[:60]!r}; leave net_id out for the active network")
    if spec.action_type == "select":
        if "from_node" in args or "to_node" in args:  # a model can't know node ids; the user pins in the panel
            raise ValueError("select can't pin nodes; use pin_anchor")
        utterance = str(args.pop("utterance", "")).strip()
        if not utterance:
            raise ValueError("select needs a short utterance naming the selection")
        try:
            intent_from_payload(args, utterance)  # IntentError is a ValueError: repaired like a schema error
        except (AttributeError, TypeError):
            raise ValueError("select: a field has the wrong shape (facility is an object)") from None
        return _parse(session, {"type": "select", "utterance": utterance, "query": args})
    if args.pop("on_selection", False):
        _fill_selection_ids(view, args)
    if spec.action_type == "set_setting":
        key = str(args.get("key", "")).strip()
        section = key.lower().split(".", 1)[0]
        if section in PROTECTED_SETTINGS:
            named = f"{section}.* settings" if looks_like_secret(key) else key
            raise ValueError(f"the assistant can't change {named} (a protected setting); ask the user")
    return _parse(session, {"type": spec.action_type, **args})


def _fill_selection_ids(view: ToolView, args: dict[str, Any]) -> None:
    """Put the selection's link ids and network in ``args`` (``on_selection``), or raise ``ValueError``."""
    if "ids" in args:
        raise ValueError("give ids or on_selection, not both")
    if args.get("table", "link") != "link":
        raise ValueError("on_selection works on the link table only")
    sel = view.selection
    if not sel or not sel["link_ids"]:
        raise ValueError("on_selection: nothing is selected; select links first")
    target = args.get("net_id") or view.active
    if sel["net_id"] != target:
        raise ValueError(f"on_selection: the selection is on {sel['net_id']!r}, not {target!r}")
    args["ids"] = list(sel["link_ids"])
    args["net_id"] = sel["net_id"]  # the ids belong to that network: approving later must edit it, whatever is active


def _parse(session: Session, data: dict[str, Any]) -> BaseAction:
    try:
        return session.actions.parse(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        # In the registry's union, ``loc[0]`` is the discriminator tag (``style``); a lone model has none.
        skip = 1 if len(session.actions.types()) > 1 else 0
        where = "; ".join(f"{'.'.join(str(p) for p in e['loc'][skip:]) or '(top level)'}: {e['msg']}" for e in errors)
        raise ValueError(where) from None


def allowlist_problems(session: Session, actions: list[str], plugins: list[str]) -> list[str]:
    """What is wrong with ``assistant.actions`` / ``assistant.plugins`` for this session (empty when nothing).

    An unknown Action type, one that is never offered, a plugin's Action listed among the core ones, and an
    unknown plugin id would otherwise be ignored without a word.
    """
    problems = []
    known_plugins = {status.id for status in session.plugin_status} | set(session.plugins)
    for action_type in actions:
        name = repr(action_type[:60])
        if not session.actions.has(action_type):
            problems.append(
                f"assistant.actions: {name} is never offered to the assistant"
                if action_type in NEVER
                else f"assistant.actions: there is no Action named {name}"
            )
        elif (owner := session.action_owner(action_type)) is not None:
            problems.append(
                f"assistant.actions: {name} belongs to the plugin {owner!r}; opt the plugin in with assistant.plugins"
            )
        elif not offerable(action_type, session.actions.model(action_type)):  # NEVER, or ``assistant = False``
            problems.append(f"assistant.actions: {name} is never offered to the assistant")
    for plugin_id in plugins:
        if plugin_id not in known_plugins:
            problems.append(f"assistant.plugins: there is no plugin with the id {plugin_id[:60]!r}")
    return problems
