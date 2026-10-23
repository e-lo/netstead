"""Read a local OSM extract (``.osm`` XML or an Overpass JSON export) into the ``(nodes, ways)`` contract.

The Overpass path filters ``highway`` ways on the server; a local file holds whatever its exporter
kept, so this reader applies the same ``network_type`` allow-list itself via
:func:`netstead.osm.tags.accepts_highway`, and keeps only ways that carry a ``highway`` tag.

Ways that reference a node missing from the file (typical where an extract cuts a road at its
edge) are dropped, with one warning naming how many, rather than failing the whole import.

Stdlib only (``xml.etree.ElementTree.iterparse`` + ``json``): no new dependency. ``.pbf`` is not
supported yet. ``.osm`` XML is streamed with bounded memory (each top-level element is cleared
off the tree as it's consumed); ``.json`` (Overpass JSON) exports are fully buffered in memory by
stdlib ``json``, so prefer ``.osm`` XML for very large extracts.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from netstead.network_types import NetworkTypes

from . import tags
from .query import parse_overpass_elements

__all__ = ["SUPPORTED_SUFFIXES", "read_osm_file"]

logger = logging.getLogger(__name__)

#: File suffixes :func:`read_osm_file` understands.
SUPPORTED_SUFFIXES = (".osm", ".json")

#: Markers that identify a DTD/entity declaration; real ``.osm`` exports never carry one, and
#: entity expansion (e.g. a "billion laughs" file) is a classic XML memory-exhaustion attack.
_DTD_MARKERS = ("<!DOCTYPE", "<!ENTITY")

#: How many leading bytes of a ``.osm`` file to scan for a DTD/entity declaration.
_DTD_SCAN_BYTES = 4096

#: Direct children of ``<osm>`` that are cleared from the tree once consumed, to keep memory
#: bounded regardless of file size.
_TOP_LEVEL_TAGS = frozenset({"node", "way", "relation"})


def _reject_dtd(path: Path) -> None:
    """Raise if ``path`` declares a DTD or entity in its first few KB (no stdlib-only way to parse one safely)."""
    with open(path, "rb") as fh:
        head = fh.read(_DTD_SCAN_BYTES).decode("utf-8", errors="ignore")
    if any(marker in head for marker in _DTD_MARKERS):
        raise ValueError("DTD/entity declarations are not allowed in .osm files")


def _parse_xml(path: Path) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    _reject_dtd(path)
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []
    context = ET.iterparse(path, events=("start", "end"))
    _root_event, root = next(context)
    for event, elem in context:
        if event != "end" or elem.tag not in _TOP_LEVEL_TAGS:
            continue
        if elem.tag == "node":
            nodes[int(elem.attrib["id"])] = (float(elem.attrib["lon"]), float(elem.attrib["lat"]))
        elif elem.tag == "way":
            ways.append(
                {
                    "id": int(elem.attrib["id"]),
                    "nodes": [int(nd.attrib["ref"]) for nd in elem.iter("nd")],
                    "tags": {t.attrib["k"]: t.attrib["v"] for t in elem.iter("tag")},
                }
            )
        # Detach the consumed element (and every sibling processed so far) from the root so
        # memory stays bounded by one element's worth of content, not the whole file.
        elem.clear()
        root.clear()
    return nodes, ways


def _parse_json(path: Path) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise ValueError(f"{path} is not an Overpass JSON export (no top-level 'elements' list)")
    return parse_overpass_elements(data["elements"])


def read_osm_file(
    path: str | Path, *, network_type: NetworkTypes = "drive"
) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    """Read ``path`` and return ``(nodes, ways)`` for :func:`netstead.osm.convert.build_node_link_tables`.

    Args:
        path: An ``.osm`` XML file or an Overpass ``[out:json]`` export (``.json``).
        network_type: One of the keys in ``osm_network_filters.yaml`` (``drive``/``walk``/``bike``/``all``).

    Returns:
        ``(nodes, ways)``: ``{osm_node_id: (lon, lat)}`` for every node in the file, and the
        ``{"id", "nodes", "tags"}`` ways that pass the ``network_type`` filter and are complete.

    Raises:
        ValueError: Unsupported suffix, malformed content, or unknown ``network_type``.
        FileNotFoundError: ``path`` does not exist.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported OSM file {path.name!r}; expected one of {', '.join(SUPPORTED_SUFFIXES)}")
    try:
        nodes, ways = _parse_xml(path) if suffix == ".osm" else _parse_json(path)
    except (ET.ParseError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f"could not parse {path.name}: {exc}") from exc
    tags.allowed_highways(network_type)  # fail fast on an unknown network_type, even for an empty file
    kept = [w for w in ways if "highway" in w["tags"] and tags.accepts_highway(network_type, w["tags"]["highway"])]
    complete = [w for w in kept if all(n in nodes for n in w["nodes"])]
    if len(complete) < len(kept):
        logger.warning(
            "%s: dropped %d way(s) that reference nodes missing from the file", path.name, len(kept) - len(complete)
        )
    return nodes, complete
