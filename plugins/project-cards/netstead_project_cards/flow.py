"""Steps the Action handlers share (and the routes the next release adds will): network, facility, card, errors."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from netstead.workbench.plugins import ActionError, Host, NotSupportedYet
from pydantic import ValidationError

from .card import DraftCard
from .errors import CardError, CardNotSupported, not_supported
from .mapping import FieldMap
from .state import CardsState
from .types import TRANSIT_TYPES, NetworkChange, RoadwayDeletion, RoadwayPropertyChange, Selection, incomplete
from .validate import card_problems

__all__ = [
    "SELECTABLE",
    "as_action_errors",
    "check_card_shape",
    "complete_change",
    "open_handle",
    "refuse_transit",
    "selection_facility",
    "selection_networks",
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


def target_network(host: Host, state: CardsState, net_id: str | None) -> str:
    """The network a change goes to: ``net_id``, else the active one; an open preview stands for its base.

    The preview stands for its base only while both are the networks it was made on
    (:meth:`~netstead_project_cards.state.Preview.stands_for_base`): once the base is closed (even if a network with
    its id was opened since), a change aimed at the preview is refused rather than sent to another network.
    Its draft is reconciled with the network first (:meth:`CardsState.sync`), so every handler starts from a draft
    that describes the network.
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
    state.sync(net, handle)
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
        return change.model_copy(update={"links": selection.target_ids()}), warnings
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
