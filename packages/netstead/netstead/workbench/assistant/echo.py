"""The assistant's echo: plain words for the user, and a values-free summary for the model's conversation.

:func:`describe` and :func:`result_text` are for the person at the keyboard (they may name columns and values).
:func:`model_echo` is what goes back to the model as the conversation's history: Action types, statuses and counts,
never a record value or an error message, so no network data reaches a remote model through it (Decision 15).
"""

from __future__ import annotations

from typing import Any

from ..actions import BaseAction

__all__ = ["describe", "model_echo", "result_detail", "result_text"]

_OPS = {
    "eq": "=",
    "ne": "≠",
    "lt": "<",
    "lte": "≤",
    "gt": ">",
    "gte": "≥",
    "in": "in",
    "contains": "contains",
    "isnull": "is empty",
    "notnull": "is not empty",
}
#: Step statuses as the model's summary words them (others are used as they are: ``done``, ``failed``).
_STATUS_WORDS = {"drafted": "drafted for approval"}


def _plural(n: int, word: str) -> str:
    """``n word`` with an ``s`` unless ``n`` is 1.

    >>> _plural(1, "link"), _plural(3, "link")
    ('1 link', '3 links')
    """
    return f"{n} {word}" + ("" if n == 1 else "s")


def describe(action: BaseAction) -> str:
    """One step in plain words, for the draft and the conversation (the ``app.do`` line sits beside it)."""
    a: Any = action  # each branch reads its own Action's fields
    t = action.type
    if t == "select":
        return f"select “{a.utterance}”" if a.utterance else f"select {_plural(len(a.link_ids or []), 'link')}"
    if t == "style":
        return _describe_style(a)
    if t == "filter":
        if not a.where:
            return "clear the link filter"
        conds = [f"{c.col} {_OPS[c.op]}" + ("" if c.op in ("isnull", "notnull") else f" {c.val}") for c in a.where]
        return "show only links where " + " and ".join(conds)
    if t == "navigate":
        if a.record:
            return f"fly to {a.record.table} {a.record.id}"
        return (
            "zoom to the selection" if a.to_selection else "zoom to the network" if a.to_network else "zoom to an area"
        )
    if t == "edit_cells":
        values = ", ".join(f"{k} = {v}" for k, v in a.values.items())
        return f"set {values} on {_plural(len(a.ids), a.table)}"
    if t == "delete_rows":
        return f"delete {_plural(len(a.ids), a.table)} (and the rows that depend on them)"
    if t == "pin_anchor":
        return f"pin the {a.role} anchor to node {a.node_id}" if a.node_id is not None else f"unpin the {a.role} anchor"
    if t in ("save_selection", "use_selection", "delete_selection"):
        return f"{t.split('_')[0]} the selection “{a.name}”"
    if t == "set_setting":
        return f"set the setting {a.key}"  # the value is in the app.do line beside it
    return t.replace("_", " ").replace(".", ": ")


def _describe_style(a: Any) -> str:
    bits = []
    if a.color_by:
        bits.append("stop colouring links by a column" if a.color_by == "none" else f"colour links by {a.color_by}")
    if a.width_by:
        bits.append("size links by lane count" if a.width_by == "none" else f"size links by {a.width_by}")
    return " and ".join(bits) or "change the map style"


def result_detail(entry: Any) -> dict[str, Any]:
    """What may be said about a recorded step without values: a selection's status and size, a filter's count."""
    result = entry.result if isinstance(entry.result, dict) else {}
    if not entry.ok:
        return {}
    if entry.action["type"] in ("select", "pin_anchor", "use_selection") and "status" in result:
        return {"status": result["status"], "links": len(result.get("link_ids", []))}
    if entry.action["type"] == "filter" and result:
        return {"links": result.get("count")}
    return {}


def result_text(entry: Any) -> str:
    """The user's line for a recorded step: what happened, or why it failed."""
    if not entry.ok:
        return f"failed: {entry.error}"
    d = result_detail(entry)
    if d.get("status") == "ambiguous":
        return f"ambiguous: {_plural(d['links'], 'link')}; pin an anchor in the Selection panel"
    if "status" in d:
        return f"{d['status'].replace('_', ' ')}: {_plural(d['links'], 'link')}"
    if d.get("links") is not None:
        return f"{_plural(d['links'], 'link')} match"
    return "done"


def model_echo(steps: list[dict[str, Any]]) -> str:
    """The conversation line the model sees for a turn's steps: Action types, statuses and counts, never values.

    Each step is ``{"type", "status", "detail"}``; only ``detail``'s ``status`` and ``links`` are read.

    >>> model_echo([{"type": "style", "status": "done"}, {"type": "filter", "status": "done", "detail": {"links": 1}}])
    'done: style; done: filter (1 link)'
    """
    out = []
    for s in steps:
        d = s.get("detail") or {}
        bits = [str(d["status"]).replace("_", " ")] if "status" in d else []
        if isinstance(d.get("links"), int):
            bits.append(_plural(d["links"], "link"))
        label = _STATUS_WORDS.get(s["status"], s["status"])
        out.append(f"{label}: {s['type']}" + (f" ({', '.join(bits)})" if bits else ""))
    return "; ".join(out)
