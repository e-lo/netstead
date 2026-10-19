"""Emit a resolved SelectionResult as a validated GMNS selection fragment.

Two forms, both valid ProjectCard-style facility selections:

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

import jsonschema

from .errors import SelectError
from .result import SelectionResult

__all__ = ["load_schema", "to_fragment", "to_projectcard", "validate_fragment"]

_EMITTABLE = {"resolved"}


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


def to_fragment(result: SelectionResult, *, form: str = "resolved") -> dict[str, Any]:
    """Build the GMNS-native selection fragment from a resolved result.

    Args:
        result: The resolved selection to emit.
        form: ``"resolved"`` (concrete ``links.link_id``) or ``"query"``
            (``all``/``name``/``ref`` + modes + conditions, re-resolvable).

    Raises:
        SelectError: if the result is not ``resolved`` (an ambiguous or not-found
            result has no fragment; the Workbench pins its anchors first).
    """
    if result.status not in _EMITTABLE:
        raise SelectError(f"cannot emit a fragment from status {result.status!r}")
    if form == "query":
        frag: dict[str, Any] = {"links": _query_links(result.intent, "link_id")}
    elif form == "resolved":
        frag = {"links": {"link_id": list(result.link_ids)}}
    else:
        raise SelectError(f"unknown emit form {form!r}; expected 'resolved' or 'query'")
    if result.from_match and result.from_match.node_id is not None:
        frag["from"] = {"node_id": result.from_match.node_id}
    if result.to_match and result.to_match.node_id is not None:
        frag["to"] = {"node_id": result.to_match.node_id}
    if result.intent.utterance:
        frag["notes"] = result.intent.utterance
    return frag


def validate_fragment(fragment: dict) -> None:
    """Validate a fragment against the GMNS selection schema. Raises on invalid."""
    jsonschema.validate(fragment, load_schema())


def to_projectcard(result: SelectionResult, *, form: str = "resolved") -> dict[str, Any]:
    """Adapt to a ProjectCard roadway-selection facility object.

    Resolved form maps ``link_id``/``node_id`` to ``model_link_id``/
    ``model_node_id``; query form emits ProjectCard's native ``select_links``
    fields (``all``/``name``/``ref``/``modes`` + conditions) directly.
    ``links.ignore_missing`` is always written: the ProjectCard schema requires it.
    """
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
