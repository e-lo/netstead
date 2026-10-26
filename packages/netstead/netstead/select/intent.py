"""SelectionIntent — the structured, validated request the parser emits.

Mirrors the capabilities of a ProjectCard ``roadway.selection.select_links``
facility selection (plus an optional segment ``from``/``to`` and an NL-only
``direction`` convenience):

* a **primary selector** — one of ``select_all`` / ``link_ids`` / a ``facility``
  by ``name`` and/or ``ref``;
* optional **modes** and **conditions** (attribute AND-filters, e.g.
  ``{"lanes": [2, 3]}``) applied on top;
* optional **from/to anchors** to cut a segment along the facility.

The LLM (or stub) produces a :class:`SelectionIntent`; it never produces ids
directly except the explicit ``link_ids`` passthrough. Resolution happens in
:mod:`netstead.select.resolve`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import IntentError

__all__ = ["DIRECTIONS", "Facility", "SelectionIntent"]

#: Recognized travel directions on a facility.
DIRECTIONS = frozenset({"EB", "WB", "NB", "SB"})


def _as_tuple(value) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


@dataclass(frozen=True)
class Facility:
    """The named route to select along.

    ``ref`` (route numbers, e.g. ``"I 40"``) and ``name`` (street names) each
    accept a single string or a list (OR-matched, mirroring ProjectCard).
    ``direction`` is one of :data:`DIRECTIONS` or ``None``.
    """

    ref: Any = None
    name: Any = None
    direction: str | None = None

    def refs(self) -> tuple:
        """Route refs as a tuple (empty when unset)."""
        return _as_tuple(self.ref)

    def names(self) -> tuple:
        """Street names as a tuple (empty when unset)."""
        return _as_tuple(self.name)


@dataclass(frozen=True)
class SelectionIntent:
    """A validated, structured selection request (ProjectCard-select-links parity).

    Attributes:
        facility: Route to select along (``name``/``ref``); ``None`` when using
            ``select_all`` or ``link_ids``.
        from_anchor / to_anchor: Optional human anchor descriptions bounding a
            *segment*; omit both to select the whole facility.
        select_all: Select every link (then narrowed by modes/conditions).
        link_ids: Explicit resolved link ids (passthrough selection).
        modes: Optional mode filter (e.g. ``["drive", "bike"]``).
        conditions: Attribute AND-filters ``{column: value | [values]}``
            (e.g. ``{"lanes": [2, 3]}``) — mirrors ProjectCard's extra fields.
        ignore_missing: Carried through to the emitted selection.
        utterance: The cleaned natural-language request (for ``notes``).
        from_node / to_node: A node the user pinned the anchor to (``PinAnchor``); the resolver uses it instead of
            ranking candidates. Never produced by a parser.
    """

    facility: Facility | None = None
    from_anchor: str | None = None
    to_anchor: str | None = None
    select_all: bool = False
    link_ids: Any = None
    modes: Any = None
    conditions: dict = field(default_factory=dict)
    ignore_missing: bool = True
    utterance: str | None = None
    from_node: Any = None
    to_node: Any = None

    def __post_init__(self) -> None:
        """Validate that at least one primary selector is present."""
        has_facility = bool(self.facility and (self.facility.refs() or self.facility.names()))
        has_ids = bool(self.link_ids)
        if not (has_facility or has_ids or self.select_all):
            raise IntentError("selection requires one of: facility name/ref, link_ids, or select_all")
        if self.facility is not None:
            d = self.facility.direction
            if d is not None and d not in DIRECTIONS:
                raise IntentError(f"direction {d!r} not one of {sorted(DIRECTIONS)}")
        for label, anchor in (("from_anchor", self.from_anchor), ("to_anchor", self.to_anchor)):
            if anchor is not None and not anchor.strip():
                raise IntentError(f"{label} must be a non-empty string when provided")
        for role in ("from", "to"):
            if getattr(self, f"{role}_node") is not None and not getattr(self, f"{role}_anchor"):
                raise IntentError(f"{role}_node pins {role}_anchor: give the anchor too")
        if not isinstance(self.conditions, dict):
            raise IntentError("conditions must be a dict of {column: value | [values]}")
