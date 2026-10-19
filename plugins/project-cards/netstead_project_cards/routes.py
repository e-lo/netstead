"""The plugin's HTTP routes (mounted at ``/api/plugins/project_cards``): what the browser's forms need to know.

``GET /form`` describes the form for a change type on the shared (or a saved) selection: the targets, each column
with its current value, and the options. ``GET /commit-check`` returns the live edit warnings, the schema problems and
the draft as YAML, for the commit dialog. Both only read; every change is an Action. They run outside the session
lock: each Host call takes it, and the draft state has its own (:class:`~netstead_project_cards.state.CardsState`).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from netstead.workbench.plugins import ActionError, Host

from .card import DraftCard
from .errors import CardError, not_supported
from .flow import SELECTABLE, selection_facility, selection_networks, target_network
from .lower import KEYS, rows_by_key
from .mapping import FieldMap
from .state import CardsState
from .types import ROADWAY_TYPES, TRANSIT_TYPES, Selection
from .validate import card_problems

__all__ = ["build_router", "column_schema", "current_values", "next_id"]

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


def next_id(frame: Any, pk: str) -> int | None:
    """One more than the largest integer key in ``frame`` (``None`` for text keys or an empty table)."""
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


def _form(
    host: Host, state: CardsState, net: str, change_type: str, use_selection: bool, saved: str | None
) -> dict[str, Any]:
    handle = host.network(net)
    head = {"type": change_type, "supported": True, "net_id": net}
    if change_type not in SELECTABLE:  # roadway_addition: new records, no selection
        links, nodes = handle.links_df(), handle.nodes_df()
        return {
            **head,
            "link": _record_schema(links, LINK_REQUIRED),
            "node": _record_schema(nodes, NODE_REQUIRED),
            "options": ADDITION_OPTIONS,
            "next_ids": {"link_id": next_id(links, KEYS["link"]), "node_id": next_id(nodes, KEYS["node"])},
        }
    if not use_selection and saved is None:
        raise CardError(f"{change_type} acts on a selection: pass use_selection=true or saved=<name>")
    nets = selection_networks(host, state, net)
    selection, warnings = selection_facility(host, nets, use_selection=use_selection, saved=saved)
    ids = selection.target_ids()
    targets = {
        "table": selection.table,
        "count": len(ids),
        "picked": selection.ids is not None,
        "label": _label(selection),
        "warnings": warnings,
    }
    if change_type == "roadway_deletion":
        return {**head, "targets": targets, "options": DELETION_OPTIONS}
    frame = handle.links_df()
    existing, mixed = current_values(frame, KEYS["link"], ids)
    return {
        **head,
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
            return _form(host, state, net, type, use_selection, saved)
        except (CardError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @api.get("/commit-check")
    def commit_check(net_id: str | None = None, project: str = "") -> dict[str, Any]:
        try:
            net = target_network(host, state, net_id)
            warnings = host.edit_warnings(net)
        except (CardError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        card = state.card(net) or DraftCard()
        if project:
            card = card.model_copy(update={"project": project})
        problems = card_problems(card.to_card(mapping)) if card.changes else ["the draft card has no changes"]
        return {
            "net_id": net,
            "changes": len(card.changes),
            "warnings": warnings,
            "problems": problems,
            "yaml": card.to_yaml(mapping),
        }

    return api
