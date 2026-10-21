"""Steps the Action handlers and the routes share: network, facility, card, errors, the catalog and the summary."""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from netstead.workbench.plugins import ActionError, Host, NotSupportedYet
from pydantic import ValidationError

from .card import DraftCard, read_card_file
from .catalog import Catalog, CatalogEntry, applicability, scan_catalog
from .errors import CardError, CardNotSupported, not_supported
from .lower import Pending, describe
from .mapping import FieldMap
from .state import CardsState
from .summary import build_summary
from .types import TRANSIT_TYPES, NetworkChange, RoadwayDeletion, RoadwayPropertyChange, Selection, incomplete
from .validate import card_problems

__all__ = [
    "SELECTABLE",
    "as_action_errors",
    "catalog_entry",
    "check_card_shape",
    "check_entries",
    "complete_change",
    "load_catalog",
    "open_handle",
    "pending_seqs",
    "refuse_transit",
    "selection_facility",
    "selection_networks",
    "summary_text",
    "target_network",
]

#: The change types that take their targets from a selection.
SELECTABLE = frozenset({"roadway_property_change", "roadway_deletion"})


def refuse_transit(change: NetworkChange) -> None:
    """Raise ``NotSupportedYet`` for a transit change (declared, so it parses; not applied or written yet)."""
    if change.type in TRANSIT_TYPES:
        raise NotSupportedYet(str(not_supported(change.type)))


@contextmanager
def as_action_errors(prefix: str = "") -> Iterator[None]:
    """Turn the plugin's own errors into the Workbench's: shown to the user, recorded as a failed Action."""
    try:
        yield
    except CardNotSupported as exc:
        raise NotSupportedYet(prefix + str(exc)) from exc
    except CardError as exc:
        raise ActionError(prefix + str(exc)) from exc
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise ActionError(prefix + "; ".join(e["msg"] for e in errors)) from exc


def open_handle(host: Host, net_id: str) -> Any | None:
    """The handle of the open network with this id (``None`` when there is none)."""
    try:
        return host.network(net_id)
    except ActionError:
        return None


def pending_seqs(host: Host, state: CardsState, net_id: str) -> Pending:
    """This plugin's pending edits on the open network ``net_id``, read at one moment, with the drafts' generation.

    The generation is taken before the read, so an undo counted after it is never mistaken for a rolled-back one.
    When the network is not open (it closed after its handle was read), the read knows of no edit (``issued`` 0), so
    reconciling changes nothing; the draft goes with its handle.
    """
    generation = state.generation()
    try:
        pending = host.pending_edits(net_id)
    except ActionError:
        return Pending(frozenset(), 0, generation)
    seqs = frozenset(e["seq"] for e in pending.edits if e["source"] == host.plugin_id)
    return Pending(seqs, pending.issued, generation)


def target_network(host: Host, state: CardsState, net_id: str | None) -> str:
    """The network a change goes to: ``net_id``, else the active one; an open preview stands for its base.

    The preview stands for its base only while both are the networks it was made on
    (:meth:`~netstead_project_cards.state.Preview.stands_for_base`): once the base is closed (even if a network with
    its id was opened since), a change aimed at the preview is refused rather than sent to another network.
    Its draft is reconciled with the network's pending edits first (:meth:`CardsState.sync`), so every handler
    starts from a draft that describes the network.
    """
    net = net_id or host.active
    if net is None:
        raise CardError("no network is open")
    handle = open_handle(host, net)
    preview = state.preview()
    if preview is not None and net == preview.net_id and preview.is_copy(handle):
        net = preview.base
        base = open_handle(host, net)
        if not preview.stands_for_base(handle, base):
            raise CardError("the network this preview was made from is closed")
        handle = base
    if handle is None:
        raise CardError(f"there is no open network {net!r}")
    state.sync(net, handle, pending_seqs(host, state, net))
    return net


def selection_networks(host: Host, state: CardsState, net: str) -> frozenset[str]:
    """The networks a selection may be on to target ``net``: ``net``, and the open preview of ``net``.

    A preview is made active, so a selection made while previewing is on the preview; its link ids are applied to
    the base (a picked link only the preview has fails there, as any missing picked link does).
    """
    preview = state.preview()
    if preview is None or preview.base != net:
        return frozenset({net})
    if not preview.stands_for_base(open_handle(host, preview.net_id), open_handle(host, net)):
        return frozenset({net})
    return frozenset({net, preview.net_id})


def selection_facility(
    host: Host, nets: frozenset[str], *, use_selection: bool, saved: str | None
) -> tuple[Selection, list[str]]:
    """The facility the shared (or a saved) selection on one of ``nets`` gives, and its warnings.

    A click-pick becomes its link ids with ``ignore_missing`` false (a fix aimed at specific links fails loudly when
    one is gone). A query becomes its query with the link ids it resolved to as provenance, keeping the intent's
    ``ignore_missing`` (true, Wrangler's default, when the query has none) for both the card and the lowering.
    """
    if use_selection:
        payload: dict[str, Any] | None = host.selection
        where = "the selection"
        if payload is None:
            raise CardError("nothing is selected")
    else:
        payload = host.saved_selection(saved or "")
        where = f"the saved selection {saved!r}"
        if payload is None:
            raise CardError(f"there is no saved selection named {saved!r}")
    if payload.get("net_id") not in nets:
        wanted = " or ".join(repr(n) for n in sorted(nets))
        raise CardError(f"{where} is on network {payload.get('net_id')!r}, not {wanted}")
    forms = payload.get("projectcard")
    if payload.get("status") != "resolved" or not forms:
        raise CardError(f"{where} is not resolved; pin its anchors first")
    ids = list(payload["link_ids"])
    if forms["picked"]:
        selection = Selection(ids=ids, ignore_missing=False)
    else:
        query = copy.deepcopy(forms["query"])
        query.setdefault("links", {}).setdefault("ignore_missing", True)  # written explicitly, as the schema requires
        selection = Selection(query=query, resolved=ids)  # its ignore_missing follows the query's
    return selection, list(payload.get("warnings") or [])


def complete_change(
    host: Host, nets: frozenset[str], change: NetworkChange, use_selection: bool, saved: str | None
) -> tuple[NetworkChange, list[str]]:
    """``change`` with its targets taken from the shared (or a saved) selection when asked, and its warnings.

    ``nets`` are the networks the selection may be on (:func:`selection_networks`).
    """
    if not use_selection and saved is None:
        return change, []
    if change.type not in SELECTABLE:
        raise CardError(f"{change.type} does not take a selection")
    selection, warnings = selection_facility(host, nets, use_selection=use_selection, saved=saved)
    if isinstance(change, RoadwayDeletion):
        if change.links or change.nodes:
            raise CardError("give the links to delete or use a selection, not both")
        # A pick's links fail loudly when one is gone; a query's follow its ignore_missing, as a property change does.
        update = {"links": selection.target_ids(), "ignore_missing": selection.ignore_missing}
        return change.model_copy(update=update), warnings
    if not isinstance(change, RoadwayPropertyChange) or change.facility is not None:
        raise CardError("give a facility or use a selection, not both")
    return change.model_copy(update={"facility": selection}), warnings


def check_card_shape(change: NetworkChange, mapping: FieldMap) -> None:
    """Refuse a change that is incomplete, or would not be a valid ProjectCard (``projectcard``), before applying it."""
    if problem := incomplete(change):
        raise CardError(problem)
    problems = card_problems(DraftCard(project="check", changes=[change]).to_card(mapping))
    if problems:
        raise CardError(f"this change would not be a valid ProjectCard: {problems[0]}")


# ---------------------------------------------------------------- the catalog and the summary


def load_catalog(host: Host, path: str, mapping: FieldMap) -> Catalog:
    """Scan the folder at ``path`` (inside ``io.allowed_roots``) as a catalog; nothing is stored.

    Raises ``PathNotAllowed`` outside the roots and ``ActionError`` when there is no folder there.
    """
    folder = host.readable(path)
    if not folder.is_dir():
        raise ActionError(f"{path} is not a folder")
    return scan_catalog(str(folder), host.list_dir, lambda p: read_card_file(p, mapping))


def catalog_entry(state: CardsState, card_id: str) -> CatalogEntry:
    """The open catalog's card ``card_id`` (``ActionError`` when no catalog is open or it has no such card)."""
    catalog = state.catalog()
    if catalog is None:
        raise ActionError("no catalog is open: open a folder of cards first (project_cards.open_catalog)")
    entry = catalog.entry(card_id)
    if entry is None:
        raise ActionError(f"the catalog at {catalog.path} has no card {card_id!r}")
    return entry


def check_entries(host: Host, state: CardsState, net: str, entries: Sequence[CatalogEntry]) -> list[dict[str, Any]]:
    """Compute and store each entry's applicability on the open network ``net`` (read once); return the results.

    Each result is :func:`~netstead_project_cards.catalog.applicability`'s dict plus the ``net_id`` and ``version``
    it was computed on. Nothing is mutated.
    """
    handle = host.network(net)
    version = handle.version  # before the frames (cached per version): a result never claims a newer version
    links, nodes = handle.links_df(), handle.nodes_df()
    results = []
    for entry in entries:
        result = {**applicability(entry.card, links, nodes), "net_id": net, "version": version}
        state.set_applicability(entry, result)
        results.append(result)
    return results


def summary_text(host: Host, state: CardsState, net_id: str | None) -> str:
    """The session summary for ``net_id`` (default: the active network, if any): see :mod:`summary`."""
    net = net_id or host.active
    handle = open_handle(host, net) if net is not None else None
    network = None
    if net is not None and handle is not None:
        about = handle.summary()
        network = {
            "name": about["label"],
            "source": about["source"],
            "spec_version": getattr(handle.roadway, "spec_version", None),
        }
    card = state.card(net) if net is not None else None
    draft = {"project": card.project, "changes": [describe(c) for c in card.changes]} if card is not None else None
    applied = [a.to_dict() for a in state.applied]
    return build_summary(network, applied, draft, host.history("project_cards."), datetime.now(UTC))
