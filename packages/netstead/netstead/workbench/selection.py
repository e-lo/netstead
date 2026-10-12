"""Turn a :class:`~netstead.select.result.SelectionResult` into the workbench's JSON selection payload."""

from __future__ import annotations

import json
from typing import Any

from netstead.select.emit import to_fragment, to_projectcard
from netstead.select.parse import payload_from_intent
from netstead.viz.styling import json_scalar

from .registry import NetworkHandle

__all__ = ["facility_forms", "selection_payload", "unparsed_payload"]


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


def selection_payload(
    handle: NetworkHandle, result: Any, *, utterance: str | None = None, parsed_by: dict[str, Any] | None = None
) -> dict[str, Any]:
    """JSON-safe selection: status, link ids, located anchors, fragment, diagnostics, and who parsed it.

    ``parsed_by`` is the parser's ``describe()`` (provider/model/mode, never a key); ``None`` for a
    selection by explicit link ids. ``projectcard`` is the :func:`facility_forms` of a resolved selection
    (``None`` otherwise). ``intent`` is a query's parsed intent as tool arguments
    (:func:`~netstead.select.parse.payload_from_intent`), so an edit can resolve it again; ``None`` for a selection
    made by ids.
    """
    node_xy = handle.node_xy()
    anchors = []
    for role, match in (("from", result.from_match), ("to", result.to_match)):
        if match and match.node_id is not None and match.node_id in node_xy:
            lon, lat = node_xy[match.node_id]
            anchors.append(
                {
                    "role": role,
                    "node_id": json_scalar(match.node_id),
                    "lon": lon,
                    "lat": lat,
                    "kind": match.kind,
                    "detail": match.detail,
                }
            )
    return {
        "net_id": handle.id,
        "component": "roadway",
        "status": result.status,
        "utterance": utterance,
        "intent": payload_from_intent(result.intent) if utterance is not None else None,
        "link_ids": [json_scalar(i) for i in result.link_ids],
        "anchors": anchors,
        "fragment": to_fragment(result) if result.status == "resolved" else None,
        "projectcard": facility_forms(result) if result.status == "resolved" else None,
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
        "fragment": None,
        "projectcard": None,
        "diagnostics": [f"could not parse: {error}"],
        "parsed_by": parsed_by,
    }
