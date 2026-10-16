"""Value types produced by the resolver: AnchorMatch and SelectionResult.

``SelectionResult.status`` is a first-class outcome: ``ambiguous`` and
``not_found`` are normal returns (they feed the future clarify/map step), not
exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intent import SelectionIntent

__all__ = ["STATUSES", "AnchorMatch", "SelectionResult"]

#: Valid SelectionResult.status values.
STATUSES = frozenset({"resolved", "ambiguous", "not_found"})


@dataclass(frozen=True)
class AnchorMatch:
    """How one anchor resolved to a mainline node.

    Attributes:
        anchor: The anchor text from the intent.
        node_id: Resolved mainline node, or ``None`` if unresolved.
        candidates: Ranked alternative node ids when ambiguous.
        confidence: 0..1 heuristic confidence.
        kind: ``gore`` (off-ramp diverge), ``merge`` (on-ramp merge),
            ``surface`` (direct at-grade), ``pinned`` (chosen by the user), or ``unresolved``.
        detail: Human-readable explanation for diagnostics/UX.
    """

    anchor: str
    node_id: int | str | None
    candidates: list = field(default_factory=list)
    confidence: float = 0.0
    kind: str = "unresolved"
    detail: str = ""


@dataclass(frozen=True)
class SelectionResult:
    """Outcome of resolving a SelectionIntent against a network."""

    status: str
    intent: SelectionIntent
    link_ids: list = field(default_factory=list)
    node_path: list = field(default_factory=list)
    from_match: AnchorMatch | None = None
    to_match: AnchorMatch | None = None
    diagnostics: list = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate the status is one of :data:`STATUSES`."""
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} not one of {sorted(STATUSES)}")
