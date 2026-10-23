"""What the panel's read-only Inspect view shows for a catalog card or a network's draft (``GET /card``).

The card's metadata, each change as the change model has it (in GMNS names) with the link and node ids it aims at
(an addition's are the records it adds), whether each change applies to the open network as it is now, and the card
as text: the file itself for a catalog card (read through ``host.readable``), the YAML a commit would write for the
draft. Nothing is mutated and nothing is stored: a catalog card's check here is not kept on its catalog entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from netstead.workbench.plugins import ActionError, Host

from .card import DraftCard, read_card_file
from .catalog import applicability, card_targets
from .errors import CardError
from .flow import catalog_entry, target_network
from .lower import KEYS
from .mapping import FieldMap
from .state import CardsState
from .types import NetworkChange, RoadwayAddition

__all__ = ["inspect_card"]


def inspect_card(
    host: Host, state: CardsState, mapping: FieldMap, *, card_id: str | None, draft: bool, net_id: str | None
) -> dict[str, Any]:
    """The Inspect view of the catalog card ``card_id``, or (``draft``) of the network's draft card.

    A catalog card is read from its file again (it may have changed since the catalog was scanned) and checked
    against the network (``net_id``, else the active one) when one is open. The draft's changes are already on its
    network, so they are reported as applied rather than checked.

    Raises:
        CardError: neither or both of ``card_id`` and ``draft``, an empty draft, or a file that cannot be read.
        ActionError: no catalog is open, or it has no card ``card_id``; the file is outside ``io.allowed_roots``
            (``PathNotAllowed``) or gone.
    """
    if (card_id is None) == (not draft):
        raise CardError("inspect a catalog card (card_id) or the draft (draft=true), not both")
    if draft:
        net = target_network(host, state, net_id)
        card = state.card(net)
        if card is None:
            raise CardError(f"the draft card on {net} has no changes")
        source = state.source(net) or {"card_id": None, "path": None}  # the file the draft was loaded from (Edit)
        head = {"source": "draft", "card_id": source["card_id"], "path": source["path"], "net_id": net}
        return {**head, **_card(card), "changes": _changes(card, None), "applies": None, "yaml": card.to_yaml(mapping)}
    entry = catalog_entry(state, card_id or "")
    path = host.readable(entry.path)
    card = read_card_file(path, mapping)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # read_card_file read it a moment ago: a race, or not UTF-8
        raise CardError(f"could not read {entry.path}: {exc}") from exc
    net, check = _check(host, state, net_id, card)
    head = {"source": "catalog", "card_id": entry.card_id, "path": str(path), "net_id": net}
    return {
        **head,
        **_card(card),
        "changes": _changes(card, check),
        "applies": check["ok"] if check else None,
        "yaml": text,
    }


def _check(
    host: Host, state: CardsState, net_id: str | None, card: DraftCard
) -> tuple[str | None, dict[str, Any] | None]:
    """The open network (``None``: none is) and the card's applicability on it, read once."""
    try:
        net = target_network(host, state, net_id)
    except (CardError, ActionError):
        return None, None
    handle = host.network(net)
    return net, applicability(card, handle.links_df(), handle.nodes_df())


def _card(card: DraftCard) -> dict[str, Any]:
    return {
        "project": card.project,
        "tags": list(card.tags),
        "dependencies": card.dependencies.model_dump(),
        "notes": card.notes,
    }


def _changes(card: DraftCard, check: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Each change as the model has it, with ``targets`` and its ``check`` (``None`` for the draft's)."""
    out = []
    for i, change in enumerate(card.changes):
        out.append(
            {
                "index": i,
                **change.model_dump(mode="json"),
                "targets": _targets(change),
                "check": check["changes"][i] if check is not None else None,
            }
        )
    return out


def _targets(change: NetworkChange) -> dict[str, list[Any]]:
    """The link and node ids a change aims at, for the map: an addition's are the records it adds."""
    if isinstance(change, RoadwayAddition):
        return {
            "links": [r[KEYS["link"]] for r in change.links],
            "nodes": [r[KEYS["node"]] for r in change.nodes],
        }
    return card_targets(DraftCard(changes=[change]))
