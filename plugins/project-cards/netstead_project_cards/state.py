"""The plugin's own nouns: one draft card per network, the cards committed this session, and the open preview.

It lives in the plugin factory's closure (one per Workbench session) and is changed only by the plugin's Action
handlers, after every Host call they make has succeeded: a failing handler's network changes are rolled back by the
Workbench, and this state must not have moved. The routes read it outside the session lock, so it has its own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .card import DraftCard
from .lower import Applied, describe
from .types import CHANGE_TYPES, ROADWAY_TYPES, NetworkChange

__all__ = ["CardsState", "Draft"]


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

    def snapshot(self, is_open: Callable[[str], bool]) -> dict[str, Any]:
        """JSON-safe state for ``/api/state`` (``plugins.project_cards``); drops drafts and previews of closed networks.

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
