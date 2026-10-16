"""Turn a :class:`~netstead.select.result.SelectionResult` into the workbench's JSON selection payload."""

from __future__ import annotations

import json
import logging
from typing import Any

from netstead.select.emit import load_schema, to_fragment, to_projectcard
from netstead.select.intent import SelectionIntent
from netstead.select.parse import intent_from_payload, payload_from_intent
from netstead.select.result import AnchorMatch, SelectionResult
from netstead.viz.styling import json_scalar

from .registry import NetworkHandle

__all__ = [
    "MAX_CANDIDATES",
    "export_selection",
    "facility_forms",
    "fragment_problems",
    "payload_facility_forms",
    "selection_payload",
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


def export_selection(payload: dict[str, Any], *, what: str, form: str) -> dict[str, Any]:
    """A resolved selection payload as a GMNS fragment or a ProjectCard facility object, in ``form``.

    Raises ``ValueError`` when the payload is not resolved, or for ``form="query"`` on a selection made by ids.
    """
    if payload.get("status") != "resolved":
        raise ValueError("only a resolved selection can be exported; pin its ambiguous anchors first")
    if form == "query" and payload.get("intent") is None:
        raise ValueError("a selection made by ids has no query form; export the resolved form")
    result = _result_from_payload(payload)
    content = to_fragment(result, form=form) if what == "fragment" else to_projectcard(result, form=form)
    return json.loads(json.dumps(content, default=json_scalar))


def fragment_problems(fragment: dict[str, Any]) -> list[str] | None:
    """Why ``fragment`` fails the GMNS selection schema (``[]`` when valid; ``None`` without ``jsonschema``)."""
    try:
        from jsonschema.validators import validator_for  # the [nl] extra
    except ImportError:
        return None
    schema = load_schema()
    return [error.message for error in validator_for(schema)(schema).iter_errors(fragment)]


def selection_payload(
    handle: NetworkHandle, result: Any, *, utterance: str | None = None, parsed_by: dict[str, Any] | None = None
) -> dict[str, Any]:
    """JSON-safe selection: status, link ids, located anchors, fragment, diagnostics, and who parsed it.

    ``anchors`` are the from/to nodes the map can draw (with ``lon``/``lat``); ``anchors_unplaced`` are those
    whose node has no coordinates (same keys, no ``lon``/``lat``), kept for the facility forms.

    Each anchor also carries its ``anchor`` text, ``confidence``, ``pinned``, and up to :data:`MAX_CANDIDATES`
    ``candidates`` (``{node_id, lon, lat}``; no ``lon``/``lat`` without coordinates): the other nodes an ambiguous
    anchor could be, for :class:`~netstead.workbench.actions.PinAnchor`.

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
    }
