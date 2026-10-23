"""The open catalog as a read-only table in the Workbench's table view (``cards_catalog``, Host API 1.8).

One row per catalog card: its id (the file), project, tags (a comma list, so the table's "any of" filter matches a
tag), change count and types, whether it fits the network it was checked on (``fits``: true, false, or empty when not
checked) and the first reason it does not, how many links it aims at, and when its file was last modified. Core sorts,
filters and pages it; the rows are read again when this plugin publishes (a catalog opened, rescanned, a card checked
or a file changed). Nothing here writes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogEntry, card_targets, fit
from .state import CardsState

__all__ = ["CATALOG_TABLE", "COLUMNS", "catalog_rows"]

#: The table's name in the table view.
CATALOG_TABLE = "cards_catalog"
#: Its columns, in order (the key first).
COLUMNS = (
    "card_id",
    "project",
    "tags",
    "change_count",
    "change_types",
    "fits",
    "reason",
    "links_targeted",
    "modified",
)


def _modified(path: str) -> str | None:
    """The file's modification time as an ISO timestamp (UTC, whole seconds); ``None`` when it cannot be read."""
    try:
        stamp = os.stat(path).st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, UTC).replace(microsecond=0).isoformat()


def _row(entry: CatalogEntry) -> dict[str, Any]:
    status = fit(entry.applicability)
    return {
        "card_id": entry.card_id,
        "project": entry.project,
        "tags": ", ".join(entry.tags),
        "change_count": entry.change_count,
        "change_types": ", ".join(dict.fromkeys(c.type for c in entry.card.changes)),
        "fits": None if status["state"] == "unchecked" else status["state"] == "fits",
        "reason": status["reasons"][0] if status["reasons"] else "",
        "links_targeted": len(card_targets(entry.card)["links"]),
        "modified": _modified(entry.path),
    }


def catalog_rows(state: CardsState) -> list[dict[str, Any]]:
    """The open catalog's cards as table rows (none when no catalog is open), in the listing's order."""
    catalog = state.catalog()
    return [_row(entry) for entry in catalog.cards] if catalog is not None else []
