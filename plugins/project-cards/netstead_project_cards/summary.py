"""The session summary: plain text (Markdown-friendly) saying what cards this session applied, drafted and did.

Pure: :func:`build_summary` takes what the handler read from the Host and the plugin's state, and returns the text
the ``project_cards.session_summary`` Action returns and ``GET /summary.txt`` serves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["build_summary"]

_REPLAY_HINT = (
    "Replay: copy the session as Python from the Workbench's History panel (GET /api/history), "
    "or apply the cards again with app.do(ApplyCard(card_id=...)) after project_cards.open_catalog."
)


def build_summary(
    network: dict[str, Any] | None,
    applied: list[dict[str, Any]],
    draft: dict[str, Any] | None,
    history: list[dict[str, Any]],
    now: datetime,
) -> str:
    """The summary text.

    ``network`` is ``{name, source, spec_version}`` (``None``: no network open); ``applied`` the
    :class:`~netstead_project_cards.state.AppliedCard` dicts; ``draft`` ``{project, changes: [describe, ...]}`` or
    ``None``; ``history`` the ``project_cards.*`` history entries (``{seq, action, ok, error, ts}``), oldest first.
    """
    lines = ["# Project cards: session summary", ""]
    if network is None:
        lines.append("No network open.")
    else:
        lines.append(f"Network: {network.get('name') or '?'}")
        lines.append(f"Source: {network.get('source') or '?'}")
        lines.append(f"Spec version: {network.get('spec_version') or '?'}")
    lines += [f"Date: {now.replace(microsecond=0).isoformat()}", "", "## Applied cards", ""]
    if not applied:
        lines.append("none")
    for a in applied:
        n = a.get("changes", 0)
        edits = ", ".join(str(e) for e in a.get("edit_ids") or []) or "none"
        lines.append(
            f"- {a.get('project') or a.get('card_id')} ({a.get('card_id')}, {a.get('source')}): "
            f"{n} change{'s' if n != 1 else ''}, edit ids {edits}; {a.get('path')}"
        )
    lines += ["", "## Draft card", ""]
    if draft is None or not draft.get("changes"):
        lines.append("none")
    else:
        lines.append(f"{draft.get('project') or '(unnamed)'}:")
        lines.extend(f"- {text}" for text in draft["changes"])
    lines += ["", "## Actions", ""]
    if not history:
        lines.append("none")
    for entry in history:
        when = datetime.fromtimestamp(entry["ts"]).replace(microsecond=0).time().isoformat() if entry.get("ts") else ""
        status = "ok" if entry.get("ok") else f"failed: {entry.get('error')}"
        lines.append(f"- {entry.get('seq')}. {when} {entry['action'].get('type')} {_args(entry['action'])} ({status})")
    lines += ["", _REPLAY_HINT, ""]
    return "\n".join(lines)


def _args(action: dict[str, Any]) -> str:
    """The Action's fields other than ``type``, short enough for one line."""
    parts = [f"{k}={_short(v)}" for k, v in action.items() if k != "type" and v not in (None, False, [], {})]
    return " ".join(parts)


def _short(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
