"""What the assistant tells the model about the session, gated by the same privacy rules as selection.

Structural facts (network ids, labels and sizes, column names, the selection's status and size, validation counts by
severity, saved selection names, the map style's columns, the filter's size, the pending edit count) are always sent.
Network content (street names, selected ids, issue counts by rule, category values) is sent only where
``llm.quality.grounding`` is on for the provider and the endpoint really is local (or the user said ``on``). Project
notes follow ``project_context``. Table rows, tool results and issue messages are never sent.

Every part sits inside a ``<tag>…</tag>`` fence whose ``</`` are escaped, so text from a network or a project can
never close it. The whole state stays under ``assistant.context_max_chars``: the grounded extras give way first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netstead.llm.context import assistant_context, find_project_context, read_capped
from netstead.viz.styling import styleable_columns

from ..paths import allowed_roots

if TYPE_CHECKING:
    from netstead.llm import ProviderRegistry

    from ..registry import NetworkHandle
    from ..session import Session

__all__ = ["MAX_CATEGORY_VALUES", "MAX_GROUNDED_IDS", "Grounding", "assistant_disclosure", "build_grounding"]

#: Selected link ids, and values per categorical column, the grounded tier sends at most.
MAX_GROUNDED_IDS = 50
MAX_CATEGORY_VALUES = 10
#: Marks a context that left something out to fit ``assistant.context_max_chars``.
TRUNCATED = "[truncated] (some details were left out to fit assistant.context_max_chars)"
#: The grounded extras in ``facts``, in the order they give way when the state is too long: (where, key).
_SHED = ((None, "categories"), ("selection", "link_ids"), ("validation", "codes"))
#: The least room worth giving a part cut to fit (below it, the part is left out).
_MIN_PART = 200


@dataclass(frozen=True)
class Grounding:
    """The cacheable ``context`` (guide, then state), the guide on its own (for size checks), and the tiers used."""

    context: str
    guide: str
    grounded: bool = False
    notes: bool = False


def _fence(tag: str, value: Any) -> str:
    r"""``value`` (JSON unless a string) inside ``<tag>…</tag>``; ``</`` becomes ``<\/`` so it can't close the fence.

    >>> _fence("x", ["</x> hi"])
    '<x>["<\\/x> hi"]</x>'
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return f"<{tag}>{text.replace('</', chr(60) + chr(92) + '/')}</{tag}>"


def _tiers(session: Session, registry: ProviderRegistry, provider: str, endpoint_local: bool) -> tuple[bool, bool]:
    """``(grounded, notes)``: whether network content, and project notes, may go to ``provider`` now."""
    from ..session import follows_endpoint  # deferred: session.py is this package's caller

    quality = session.settings.llm.quality
    grounded = registry.grounding_on(provider) and follows_endpoint(quality.grounding, endpoint_local)
    notes = registry.project_context_on(provider) and follows_endpoint(quality.project_context, endpoint_local)
    return grounded, notes


def _facts(session: Session, state: dict[str, Any], active: NetworkHandle | None) -> dict[str, Any]:
    """The structural facts, always sent (no record values: counts, names of columns and networks, statuses)."""
    act = state["active"] or ""
    sel = state["selection"]
    flt = state["filters"].get(act)
    issues = state["issues"].get(act)
    return {
        "networks": [
            {"id": n["id"], "label": n["label"], "links": n["links"], "nodes": n["nodes"], "active": n["id"] == act}
            for n in state["networks"]
        ],
        "link_columns": [str(c) for c in active.links_df().columns] if active is not None else [],
        "selection": (
            {"status": sel["status"], "links": len(sel["link_ids"]), "utterance": sel["utterance"]} if sel else None
        ),
        "saved_selections": [s["name"] for s in state["saved_selections"]],
        "filter": {"links": flt["count"]} if flt else None,
        "style": {"color_by": state["style"].get("color_by"), "width_by": state["style"].get("width_by")},
        "pending_edits": (state["edits"].get(act) or {}).get("pending", 0),
        "validation": {"stale": issues["stale"], "counts": issues["counts"]} if issues else None,
    }


def _ground(session: Session, facts: dict[str, Any], state: dict[str, Any], active: NetworkHandle) -> None:
    """Add the grounded extras to ``facts``: selected ids, issue counts by rule, and categorical values."""
    sel = state["selection"]
    if facts["selection"] is not None and sel is not None:
        facts["selection"]["link_ids"] = list(sel["link_ids"][:MAX_GROUNDED_IDS])
    if facts["validation"] is not None and (issue_set := session.issue_set(active.id)) is not None:
        facts["validation"]["codes"] = issue_set.facets()["code"]
    links = active.links_df()
    facts["categories"] = {
        str(c["name"]): [str(v) for v in links[c["name"]].dropna().value_counts().index[:MAX_CATEGORY_VALUES]]
        for c in styleable_columns(links)
        if c["kind"] == "categorical"
    }


def _state_text(facts: dict[str, Any], cap: int) -> tuple[str, bool]:
    """The state fence under ``cap``, shedding the grounded extras first; ``(text, whether anything was cut)``."""
    cut = False

    def render() -> str:
        return "The Workbench right now:\n" + _fence("workbench_state", facts)

    text = render()
    for where, key in _SHED:
        if len(text) <= cap:
            break
        holder = facts if where is None else facts.get(where)
        if isinstance(holder, dict) and key in holder:
            del holder[key]
            cut, text = True, render()
    if len(text) > cap and len(facts["link_columns"]) > 0:
        facts["link_columns"] = facts["link_columns"][: max(0, len(facts["link_columns"]) // 2)]
        cut, text = True, render()
    if len(text) > cap:  # a last resort (dozens of networks open): cut the text, keep the fence closed
        text = text[: cap - len("</workbench_state>")] + "</workbench_state>"
        cut = True
    return text, cut


def _fit(tag: str, intro: str, body: str, room: int) -> tuple[str | None, bool]:
    """``intro`` + ``body`` fenced in ``tag`` within ``room`` characters; ``(None, True)`` when there isn't room."""
    whole = intro + _fence(tag, body)
    if len(whole) <= room:
        return whole, False
    spare = room - len(intro) - len(f"<{tag}></{tag}>") - 12
    if spare < _MIN_PART:
        return None, True
    return intro + _fence(tag, body[:spare] + " [cut]"), True


def build_grounding(
    session: Session, *, provider: str, endpoint_local: bool, registry: ProviderRegistry | None = None
) -> Grounding:
    """The assistant's context for ``provider``: the shipped guide, then the session state it may see.

    Args:
        session: The session to describe.
        provider: The provider the context goes to (its quality settings decide the tiers).
        endpoint_local: Whether the endpoint that will *actually* be called is on this machine.
        registry: The provider registry the call is made with (default: the session's current one).

    Returns:
        The context, the guide on its own, and which tiers were sent.
    """
    from ..session import vocabulary  # deferred: session.py is this package's caller

    registry = registry if registry is not None else session.llm
    settings = session.settings
    quality, cap = settings.llm.quality, settings.assistant.context_max_chars
    grounded, notes = _tiers(session, registry, provider, endpoint_local)
    guide = assistant_context(quality.assistant_context_max_chars) if quality.assistant_context else ""
    state = session.state()
    active = session.registry.get(state["active"]) if state["active"] else None
    facts = _facts(session, state, active)
    if grounded and active is not None:
        _ground(session, facts, state, active)
    text, cut = _state_text(facts, cap)
    parts = [text]
    if grounded and active is not None:
        names = json.dumps(list(vocabulary(active, quality.grounding_max_names)), ensure_ascii=False)
        intro = "Street names and route numbers in the active network:\n"
        part, more = _fit("network_vocabulary", intro, names, cap - sum(len(p) + 2 for p in parts))
        cut |= more
        parts += [part] if part else []
    if notes and active is not None:
        roots = allowed_roots(settings)  # notes are only read from inside io.allowed_roots
        path = find_project_context(active.source, session.project_dir, roots)
        if path:
            body = "\n" + read_capped(path, quality.project_context_max_chars, roots) + "\n"
            part, more = _fit("project_notes", "", body, cap - sum(len(p) + 2 for p in parts))
            cut |= more
            parts += [part] if part else []
    if cut:
        parts.append(TRUNCATED)
    body = "\n\n".join(parts)
    return Grounding(context="\n\n".join(p for p in (guide, body) if p), guide=guide, grounded=grounded, notes=notes)


def assistant_disclosure(
    session: Session, provider: str, *, endpoint_local: bool, registry: ProviderRegistry | None = None
) -> list[str]:
    """What one question to the assistant sends to ``provider``, in words (the panel's privacy note).

    Built from the same gates as :func:`build_grounding`, so the note can't drift from what is sent.
    """
    registry = registry if registry is not None else session.llm
    grounded, notes = _tiers(session, registry, provider, endpoint_local)
    quality = session.settings.llm.quality
    items = [
        "your question and the recent conversation (as the assistant summarised it: action names and counts)",
        "the tool schemas (Action names and GMNS field names)",
        "the open networks' names and sizes, the active network's column names, the selection's status and size, "
        "validation counts by severity, saved selection names, the map style's columns, the filter's size and the "
        "number of pending edits",
    ]
    if quality.assistant_context:
        items.append("the GMNS assistant guide that ships with netstead")
    if grounded:
        items.append(
            f"up to {quality.grounding_max_names} street names and route numbers, up to {MAX_GROUNDED_IDS} selected "
            f"link ids, issue counts by rule, and up to {MAX_CATEGORY_VALUES} common values of each categorical column"
        )
    if notes:
        items.append("your project notes (NETSTEAD.md, or the ## netstead section of AGENTS.md/CLAUDE.md)")
    items.append("never your network tables, files, or what the actions returned")
    return items
