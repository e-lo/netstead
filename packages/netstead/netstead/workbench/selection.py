"""Turn a :class:`~netstead.select.result.SelectionResult` into the workbench's JSON selection payload."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

from netstead.select.emit import STRICT_FORMS, load_schema, segment_problem, to_fragment, to_projectcard
from netstead.select.errors import SelectError
from netstead.select.idsystems import ID_SYSTEMS, MappedIds, map_ids
from netstead.select.intent import SelectionIntent
from netstead.select.parse import intent_from_payload, payload_from_intent
from netstead.select.result import AnchorMatch, SelectionResult
from netstead.viz.styling import json_scalar

from .registry import NetworkHandle

__all__ = [
    "MAX_CANDIDATES",
    "export_options",
    "export_selection",
    "facility_forms",
    "fragment_problems",
    "id_captions",
    "payload_facility_forms",
    "payload_keeping_links",
    "selection_payload",
    "selection_warnings",
    "unparsed_payload",
]

logger = logging.getLogger(__name__)

#: Candidate nodes listed per anchor: enough to choose from, bounded for a pathological match.
MAX_CANDIDATES = 10


def facility_forms(result: Any) -> dict[str, Any]:
    """A resolved selection as ProjectCard facility objects, for plugins (``host.selection["projectcard"]``).

    ``resolved`` lists the link ids (``model_link_id``); ``query`` is the re-resolvable selector (name, ref, ...);
    ``picked`` says the selection was made by id (a click or "Set as selection"), not by a query.
    """
    forms = {
        "picked": bool(result.intent.link_ids),
        "resolved": to_projectcard(result),
        "query": to_projectcard(result, form="query"),
    }
    return json.loads(json.dumps(forms, default=json_scalar))  # numpy ids -> plain JSON


def payload_facility_forms(payload: dict[str, Any]) -> dict[str, Any] | None:
    """:func:`facility_forms` rebuilt from a selection payload (``None`` unless it is resolved).

    The payload keeps what the forms need (its link ids, a query's ``intent``, the anchors' nodes, placed on
    the map or not), so the shared state does not carry the id list three more times; ``host.selection`` adds
    the forms on read. A payload the forms cannot be rebuilt from is logged and gives ``None``: a plugin reading
    the selection must not fail because of it.
    """
    if payload.get("status") != "resolved":
        return None
    try:
        return _payload_facility_forms(payload)
    except Exception:  # boundary: an odd payload (an old intent, a malformed anchor) only loses the forms
        logger.exception("rebuilding the selection's facility forms failed")
        return None


def _result_from_payload(payload: dict[str, Any]) -> SelectionResult:
    """The resolved :class:`SelectionResult` a payload describes (its ids, its query's intent, its anchors' nodes)."""
    if payload.get("intent") is not None:
        intent = intent_from_payload(payload["intent"], payload.get("utterance") or "")
    else:
        intent = SelectionIntent(link_ids=list(payload["link_ids"]))
    matches = {
        a["role"]: AnchorMatch(
            anchor=a.get("anchor", ""), node_id=a["node_id"], kind=a.get("kind", ""), detail=a.get("detail", "")
        )
        for a in [*(payload.get("anchors") or []), *(payload.get("anchors_unplaced") or [])]
    }
    return SelectionResult(
        "resolved",
        intent,
        link_ids=list(payload["link_ids"]),
        from_match=matches.get("from"),
        to_match=matches.get("to"),
    )


def _payload_facility_forms(payload: dict[str, Any]) -> dict[str, Any]:
    return facility_forms(_result_from_payload(payload))


def _mapped(payload: dict[str, Any], handle: NetworkHandle | None, id_system: str) -> MappedIds | None:
    """The payload's ids in ``id_system`` (``None`` for GMNS, the ids it already has).

    Raises ``ValueError`` when the network lacks the system's columns or a selected link or anchor has no id there.
    """
    if id_system == "gmns":
        return None
    if handle is None:
        raise ValueError(f"the network is not open, so the selection cannot be written in {id_system!r} ids")
    result = _result_from_payload(payload)
    nodes = [m.node_id if m is not None else None for m in (result.from_match, result.to_match)]
    try:
        return map_ids(id_system, result.link_ids, nodes[0], nodes[1], handle.links_df(), handle.nodes_df())
    except SelectError as exc:
        raise ValueError(str(exc)) from None


def export_selection(
    payload: dict[str, Any],
    *,
    what: str,
    form: str,
    id_system: str = "gmns",
    handle: NetworkHandle | None = None,
) -> dict[str, Any]:
    """A resolved selection payload as a GMNS fragment or a ProjectCard facility object, in ``form``.

    ``form`` is ``segment`` or ``link_list`` (the Find tab's two), or the older ``resolved`` / ``query``. The
    Find tab's forms can be written in ``id_system`` (``gmns``, ``osm``, ``gers``; ``handle`` is the network, to
    look the ids up in).

    Raises ``ValueError`` when the payload is not resolved, for a query form on a selection made by ids or with a
    stale pin, for a segment of a selection with no named facility, or for ids the network does not have.
    """
    if payload.get("status") != "resolved":
        raise ValueError("only a resolved selection can be exported; pin its ambiguous anchors first")
    if form == "query" and payload.get("intent") is None:
        raise ValueError("a selection made by ids has no query form; export the resolved form")
    stale = _stale_roles(payload)
    if form in ("query", "segment") and stale:  # the query no longer gives these links: it would export something else
        raise ValueError(
            f"the {' and '.join(stale)} anchor's pinned node is no longer on the facility, so the {form} form would "
            f"not give these links; re-pin the {' and '.join(stale)} anchor first, or export the link list"
        )
    if id_system != "gmns" and form not in STRICT_FORMS:
        raise ValueError(f"the {form!r} form is written in GMNS ids only; use segment or link_list")
    result = _result_from_payload(payload)
    ids = _mapped(payload, handle, id_system)
    try:
        content = (
            to_fragment(result, form=form, ids=ids)
            if what == "fragment"
            else to_projectcard(result, form=form, ids=ids)
        )
    except SelectError as exc:
        raise ValueError(str(exc)) from None
    return json.loads(json.dumps(content, default=json_scalar))


def export_options(payload: dict[str, Any], handle: NetworkHandle | None, *, what: str) -> dict[str, Any]:
    """What the Find tab may offer for a resolved selection: ``forms`` and ``id_systems``, each ``{key: reason|None}``.

    A ``None`` reason means the choice works; otherwise it is the tooltip that says why not. Id systems are judged
    for the selection itself (a system whose column exists but is empty for these links is unavailable) and for
    ``what`` (ProjectCard has no key for GERS ids).
    """
    result = _result_from_payload(payload)
    forms: dict[str, str | None] = {"segment": segment_problem(result), "link_list": None}
    if forms["segment"] is None and _stale_roles(payload):
        forms["segment"] = "A pinned anchor is no longer on the facility; re-pin it, or use Link list"
    systems: dict[str, str | None] = {}
    for key, system in ID_SYSTEMS.items():
        try:
            _mapped(payload, handle, key)
        except ValueError as exc:
            systems[key] = str(exc)
            continue
        systems[key] = (
            "ProjectCard has no key for GERS ids" if what == "projectcard" and system.pc_link_key is None else None
        )
    return {"forms": forms, "id_systems": systems}


def id_captions(payload: dict[str, Any], handle: NetworkHandle | None, id_system: str, form: str) -> list[str]:
    """Notes on ids shared by several links in ``id_system`` (e.g. ``"3 links share OSM way 5847010"``).

    Only a link list is written in those ids; a segment names its facility, so it has nothing to note.
    """
    if form == "segment":
        return []
    mapped = _mapped(payload, handle, id_system)
    return [] if mapped is None else mapped.captions()


def _stale_roles(payload: dict[str, Any]) -> list[str]:
    """The anchors (``from``/``to``) whose pinned node is in the payload's ``stale_pins``."""
    stale, intent = payload.get("stale_pins") or [], payload.get("intent") or {}
    return [
        role for role in ("from", "to") if intent.get(f"{role}_node") is not None and intent[f"{role}_node"] in stale
    ]


def selection_warnings(payload: dict[str, Any]) -> list[str]:
    """What an export (or a plugin) should be told about a selection whose pin has gone stale; ``[]`` otherwise.

    After an edit takes a pinned node off its facility, the selection keeps its previous links (the ids rule) and
    its ``resolved`` status, but its query no longer gives those links.
    """
    return [
        f"the {role} anchor's pinned node {payload['intent'][f'{role}_node']} is no longer on the facility: these "
        f"are the links selected before the edit, not what the query gives now; re-pin the {role} anchor"
        for role in _stale_roles(payload)
    ]


def fragment_problems(fragment: dict[str, Any]) -> list[str] | None:
    """Why ``fragment`` fails the GMNS selection schema (``[]`` when valid; ``None`` without ``jsonschema``)."""
    try:
        from jsonschema.validators import validator_for  # the [nl] extra
    except ImportError:
        return None
    schema = load_schema()
    return [error.message for error in validator_for(schema)(schema).iter_errors(fragment)]


def payload_keeping_links(
    handle: NetworkHandle,
    payload: dict[str, Any],
    link_ids: list[Any],
    *,
    diagnostics: list[str],
    stale_pins: list[Any],
) -> dict[str, Any]:
    """``payload`` (a query selection) narrowed to ``link_ids``, with its status, intent, pins and anchors' nodes.

    What an edit leaves of a pinned selection whose pinned node is no longer on its facility: the click rule's
    links, not a lost selection, and the pin stays recorded so the user can see it and re-pin. ``stale_pins``
    (the pinned nodes that left the facility) goes on the payload, so the panel and an export can say so.
    """
    result = replace(
        _result_from_payload(payload), status=payload["status"], link_ids=list(link_ids), diagnostics=diagnostics
    )
    out = selection_payload(handle, result, utterance=payload["utterance"], parsed_by=payload.get("parsed_by"))
    out["stale_pins"] = [json_scalar(n) for n in stale_pins]
    return out


def selection_payload(
    handle: NetworkHandle, result: Any, *, utterance: str | None = None, parsed_by: dict[str, Any] | None = None
) -> dict[str, Any]:
    """JSON-safe selection: status, link ids, located anchors, fragment, diagnostics, and who parsed it.

    ``anchors`` are the from/to nodes the map can draw (with ``lon``/``lat``); ``anchors_unplaced`` are those
    whose node has no coordinates (same keys, no ``lon``/``lat``), kept for the facility forms.

    Each anchor also carries its ``anchor`` text, ``confidence``, ``pinned``, and up to :data:`MAX_CANDIDATES`
    ``candidates`` (``{node_id, lon, lat}``; no ``lon``/``lat`` without coordinates): the other nodes an ambiguous
    anchor could be, for :class:`~netstead.workbench.actions.PinAnchor`.

    ``stale_pins`` lists pinned nodes no longer on the facility (set by :func:`payload_keeping_links`; ``[]`` here).

    ``parsed_by`` is the parser's ``describe()`` (provider/model/mode, never a key); ``None`` for a
    selection by explicit link ids. ``intent`` is a query's parsed intent as tool arguments
    (:func:`~netstead.select.parse.payload_from_intent`), so an edit can resolve it again; ``None`` for a selection
    made by ids.
    """
    node_xy = handle.node_xy()

    def placed(node_id: Any) -> dict[str, Any]:
        spot = {"node_id": json_scalar(node_id)}
        if node_id in node_xy:
            spot["lon"], spot["lat"] = node_xy[node_id]
        return spot

    anchors, unplaced = [], []
    for role, match in (("from", result.from_match), ("to", result.to_match)):
        if not match or match.node_id is None:
            continue
        anchor = {
            "role": role,
            "node_id": json_scalar(match.node_id),
            "kind": match.kind,
            "detail": match.detail,
            "anchor": match.anchor,
            "confidence": round(float(match.confidence), 3),
            "pinned": match.kind == "pinned",
            "candidates": [placed(c) for c in match.candidates[:MAX_CANDIDATES]],
        }
        if match.node_id in node_xy:
            lon, lat = node_xy[match.node_id]
            anchors.append({**anchor, "lon": lon, "lat": lat})
        else:  # no coordinates to draw it at, but the facility forms still need its from/to node
            unplaced.append(anchor)
    return {
        "net_id": handle.id,
        "component": "roadway",
        "status": result.status,
        "utterance": utterance,
        "intent": payload_from_intent(result.intent) if utterance is not None else None,
        "link_ids": [json_scalar(i) for i in result.link_ids],
        "anchors": anchors,
        "anchors_unplaced": unplaced,
        "fragment": to_fragment(result) if result.status == "resolved" else None,
        "diagnostics": list(result.diagnostics),
        "parsed_by": parsed_by,
        "stale_pins": [],
    }


def unparsed_payload(
    handle: NetworkHandle, utterance: str, error: Exception, *, parsed_by: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A ``not_found`` selection for an utterance the parser could not read."""
    return {
        "net_id": handle.id,
        "component": "roadway",
        "status": "not_found",
        "utterance": utterance,
        "intent": None,
        "link_ids": [],
        "anchors": [],
        "anchors_unplaced": [],
        "fragment": None,
        "diagnostics": [f"could not parse: {error}"],
        "parsed_by": parsed_by,
        "stale_pins": [],
    }
