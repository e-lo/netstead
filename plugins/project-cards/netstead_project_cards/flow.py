"""Steps the Action handlers and the routes share: which network, which facility, is it a valid card, and errors."""

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
    "is_open",
    "refuse_transit",
    "selection_facility",
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


def is_open(host: Host, net_id: str) -> bool:
    """Whether a network with this id is open in the session."""
    try:
        host.network(net_id)
    except ActionError:
        return False
    return True


def target_network(host: Host, state: CardsState, net_id: str | None) -> str:
    """The network a change goes to: ``net_id``, else the active one; an open preview stands for its base."""
    net = net_id or host.active
    if net is None:
        raise CardError("no network is open")
    preview = state.preview
    if preview is not None and net == preview["net_id"]:
        net = preview["base"]
    if not is_open(host, net):
        raise CardError(f"there is no open network {net!r}")
    return net


def selection_facility(host: Host, net: str, *, use_selection: bool, saved: str | None) -> tuple[Selection, list[str]]:
    """The facility the shared (or a saved) selection gives, and its warnings.

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
    if payload.get("net_id") != net:
        raise CardError(f"{where} is on network {payload.get('net_id')!r}, not {net!r}")
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
    host: Host, net: str, change: NetworkChange, use_selection: bool, saved: str | None
) -> tuple[NetworkChange, list[str]]:
    """``change`` with its targets taken from the shared (or a saved) selection when asked, and its warnings."""
    if not use_selection and saved is None:
        return change, []
    if change.type not in SELECTABLE:
        raise CardError(f"{change.type} does not take a selection")
    selection, warnings = selection_facility(host, net, use_selection=use_selection, saved=saved)
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
