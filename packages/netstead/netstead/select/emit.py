"""Emit a resolved SelectionResult as a validated GMNS selection fragment.

Four forms, all valid ProjectCard-style facility selections. The first two are the Workbench's Find tab; each
carries only its own fields (no ``notes``, nothing from the other form):

* ``form="segment"`` — the facility by ``name``/``ref`` plus the ``from``/``to`` anchors (and any ``modes`` or
  attribute conditions), re-resolvable on another version of the network.
* ``form="link_list"`` — only the resolved link ids.

Both take an optional :class:`~netstead.select.idsystems.MappedIds` to write the ids as OSM or GERS ids instead of
GMNS ids. The two older forms, which plugins read through ``host.selection["projectcard"]``, are unchanged:

* ``form="resolved"`` (default) — the concrete resolved link ids
  (``links.link_id``), portable and unambiguous.
* ``form="query"`` — the re-resolvable *query* (``all`` / ``name`` / ``ref`` /
  explicit ids, plus ``modes`` and attribute conditions as extra fields),
  mirroring ProjectCard ``select_links``.

Both carry the resolved segment ``from``/``to`` node ids when present. The
fragment is keyed by GMNS-native ``link_id``/``node_id``; :func:`to_projectcard`
adapts to ProjectCard's ``model_link_id``/``model_node_id``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from .errors import SelectError
from .idsystems import ID_SYSTEMS, MappedIds
from .result import SelectionResult

__all__ = ["STRICT_FORMS", "load_schema", "segment_problem", "to_fragment", "to_projectcard", "validate_fragment"]

_EMITTABLE = {"resolved"}
#: The Find tab's two forms, each one-or-the-other.
STRICT_FORMS = ("segment", "link_list")


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Load and cache the GMNS selection JSON schema."""
    text = resources.files(__package__).joinpath("schema/gmns_selection.schema.json").read_text()
    return json.loads(text)


def _query_links(intent, id_key: str) -> dict[str, Any]:
    """Build a ProjectCard-style ``links`` query object from the intent."""
    links: dict[str, Any] = {}
    if intent.select_all:
        links["all"] = True
    elif intent.link_ids:
        links[id_key] = list(intent.link_ids)
    elif intent.facility is not None:
        if intent.facility.names():
            links["name"] = list(intent.facility.names())
        if intent.facility.refs():
            links["ref"] = list(intent.facility.refs())
    if intent.modes:
        links["modes"] = list(intent.modes)
    for col, val in (intent.conditions or {}).items():  # extra attribute AND-conditions
        links[col] = val
    if not intent.ignore_missing:
        links["ignore_missing"] = False
    return links


def segment_problem(result: SelectionResult) -> str | None:
    """Why ``result`` has no segment form (``None`` when it has: a named facility)."""
    facility = result.intent.facility
    if result.intent.select_all or result.intent.link_ids or facility is None:
        return "This selection has no named segment; use Link list"
    if not (facility.names() or facility.refs()):
        return "This selection has no named segment; use Link list"
    return None


def _segment_links(intent) -> dict[str, Any]:
    """The facility (``name`` / ``ref``), modes and attribute conditions of a segment, without ids or ``all``."""
    links: dict[str, Any] = {}
    if intent.facility.names():
        links["name"] = list(intent.facility.names())
    if intent.facility.refs():
        links["ref"] = list(intent.facility.refs())
    if intent.modes:
        links["modes"] = list(intent.modes)
    links.update(intent.conditions or {})
    if not intent.ignore_missing:
        links["ignore_missing"] = False
    return links


def _anchor_nodes(result: SelectionResult, ids: MappedIds | None) -> tuple[Any, Any]:
    """The from/to node ids, in the mapped id system when ``ids`` is given."""
    if ids is not None:
        return ids.from_node, ids.to_node
    nodes = [
        m.node_id if m is not None and m.node_id is not None else None for m in (result.from_match, result.to_match)
    ]
    return nodes[0], nodes[1]


def _strict_fragment(result: SelectionResult, form: str, ids: MappedIds | None) -> dict[str, Any]:
    system = ids.system if ids is not None else ID_SYSTEMS["gmns"]
    if form == "link_list":
        link_ids = ids.link_ids if ids is not None else list(result.link_ids)
        return {"links": {system.link_col: link_ids}}
    problem = segment_problem(result)
    if problem:
        raise SelectError(problem)
    frag: dict[str, Any] = {"links": _segment_links(result.intent)}
    for role, node in zip(("from", "to"), _anchor_nodes(result, ids), strict=True):
        if node is not None:
            frag[role] = {system.node_col: node}
    return frag


def to_fragment(result: SelectionResult, *, form: str = "resolved", ids: MappedIds | None = None) -> dict[str, Any]:
    """Build the GMNS-native selection fragment from a resolved result.

    Args:
        result: The resolved selection to emit.
        form: ``"segment"`` or ``"link_list"`` (see the module doc), or the older ``"resolved"`` (concrete
            ``links.link_id`` with the anchors and notes) / ``"query"`` (``all``/``name``/``ref`` + modes +
            conditions, with the anchors and notes).
        ids: the selection's ids in another id system (``segment`` and ``link_list`` only).

    Raises:
        SelectError: if the result is not ``resolved`` (an ambiguous or not-found
            result has no fragment; the Workbench pins its anchors first).
    """
    if result.status not in _EMITTABLE:
        raise SelectError(f"cannot emit a fragment from status {result.status!r}")
    if form in STRICT_FORMS:
        return _strict_fragment(result, form, ids)
    if form == "query":
        frag: dict[str, Any] = {"links": _query_links(result.intent, "link_id")}
    elif form == "resolved":
        frag = {"links": {"link_id": list(result.link_ids)}}
    else:
        raise SelectError(f"unknown emit form {form!r}; expected 'segment', 'link_list', 'resolved' or 'query'")
    if result.from_match and result.from_match.node_id is not None:
        frag["from"] = {"node_id": result.from_match.node_id}
    if result.to_match and result.to_match.node_id is not None:
        frag["to"] = {"node_id": result.to_match.node_id}
    if result.intent.utterance:
        frag["notes"] = result.intent.utterance
    return frag


def validate_fragment(fragment: dict) -> None:
    """Validate a fragment against the GMNS selection schema. Raises on invalid."""
    # Imported here, not at module level: the CLI imports this module eagerly
    # and must start on an install without the [nl] extra.
    try:
        import jsonschema
    except ImportError as e:
        raise ImportError("netstead.select.emit requires the [nl] extra: pip install 'netstead[nl]'") from e
    jsonschema.validate(fragment, load_schema())


def to_projectcard(result: SelectionResult, *, form: str = "resolved", ids: MappedIds | None = None) -> dict[str, Any]:
    """Adapt to a ProjectCard roadway-selection facility object.

    Resolved form maps ``link_id``/``node_id`` to ``model_link_id``/
    ``model_node_id``; query form emits ProjectCard's native ``select_links``
    fields (``all``/``name``/``ref``/``modes`` + conditions) directly.
    ``links.ignore_missing`` is always written: the ProjectCard schema requires it.

    ``segment`` writes ``name``/``ref`` plus ``from``/``to``; ``link_list`` writes only ``model_link_id`` (or
    ``osm_link_id`` for OSM ids). ProjectCard has no key for GERS ids: those raise :class:`SelectError`.
    """
    if form in STRICT_FORMS:
        return _strict_projectcard(result, form, ids)
    if form == "query":
        pc: dict[str, Any] = {"links": _query_links(result.intent, "model_link_id")}
    else:
        frag = to_fragment(result, form="resolved")
        pc = {"links": {"model_link_id": frag["links"]["link_id"]}}
    # ProjectCard's select_links requires the flag even at its default (the GMNS fragment omits a default true).
    pc["links"]["ignore_missing"] = result.intent.ignore_missing
    if result.from_match and result.from_match.node_id is not None:
        pc["from"] = {"model_node_id": result.from_match.node_id}
    if result.to_match and result.to_match.node_id is not None:
        pc["to"] = {"model_node_id": result.to_match.node_id}
    return pc


def _strict_projectcard(result: SelectionResult, form: str, ids: MappedIds | None) -> dict[str, Any]:
    system = ids.system if ids is not None else ID_SYSTEMS["gmns"]
    if system.pc_link_key is None or system.pc_node_key is None:
        raise SelectError("ProjectCard has no key for GERS ids; use GMNS or OpenStreetMap ids")

    def as_card(value: Any) -> Any:
        return str(value) if system.key == "osm" else value  # ProjectCard's osm ids are strings

    if form == "link_list":
        link_ids = ids.link_ids if ids is not None else list(result.link_ids)
        pc: dict[str, Any] = {"links": {system.pc_link_key: [as_card(i) for i in link_ids]}}
    else:
        problem = segment_problem(result)
        if problem:
            raise SelectError(problem)
        pc = {"links": _segment_links(result.intent)}
        for role, node in zip(("from", "to"), _anchor_nodes(result, ids), strict=True):
            if node is not None:
                pc[role] = {system.pc_node_key: as_card(node)}
    pc["links"]["ignore_missing"] = result.intent.ignore_missing
    return pc
