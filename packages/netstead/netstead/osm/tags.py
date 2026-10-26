"""OSM tag transforms, GMNS field mapping, and per-network-type highway filters.

The OSM-to-GMNS field mapping and the per-``network_type`` ``highway`` filters
live in maintained YAML data files under ``mappings/`` (loaded here), so
updating *which* OSM tag feeds *which* GMNS field — or which road classes count
as drivable/walkable/bikeable — is a data edit, not a code change. Value-level
logic that cannot live in data (unit parsing, ``oneway`` interpretation) lives
in the :data:`TRANSFORMS` registry and :func:`oneway_direction` below.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cache
from importlib import resources
from typing import Any

import yaml

from netstead.network_types import NetworkTypes, allowed_union

__all__ = [
    "TRANSFORMS",
    "accepts_highway",
    "allowed_highways",
    "apply_mapping",
    "direct",
    "load_field_mappings",
    "load_network_filters",
    "oneway_direction",
    "parse_int",
    "parse_speed",
]

_KMH_PER_MPH = 1.60934

# ``oneway`` tag values, lower-cased. OSM uses several spellings.
_ONEWAY_FORWARD = frozenset({"yes", "true", "1"})
_ONEWAY_BACKWARD = frozenset({"-1", "reverse"})
_ONEWAY_BOTH = frozenset({"no", "false", "0"})
# Highway classes that imply oneway=yes unless tagged otherwise.
_IMPLIED_ONEWAY_HIGHWAY = frozenset({"motorway", "motorway_link"})


# ---------------------------------------------------------------------------
# Named transforms — referenced by name from osm_to_gmns.yaml
# ---------------------------------------------------------------------------


def direct(value: Any) -> Any:
    """Return ``value`` unchanged (identity transform for verbatim fields).

    Args:
        value: The raw OSM tag value (or ``None`` when the tag is absent).

    Returns:
        The value unchanged.

    Examples:
        >>> direct("Main St")
        'Main St'
        >>> direct(None) is None
        True
    """
    return value


def parse_int(value: Any) -> int | None:
    """Parse an integer OSM tag value, returning ``None`` when not a clean int.

    OSM lane/count tags are occasionally ambiguous (``"3;2"`` for a way whose
    lane count differs by direction); those are treated as unknown rather than
    guessed.

    Args:
        value: Raw OSM tag value or ``None``.

    Returns:
        The integer value, or ``None`` if absent / not a plain integer.

    Examples:
        >>> parse_int("2")
        2
        >>> parse_int("3;2") is None
        True
        >>> parse_int(None) is None
        True
    """
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_speed(value: Any) -> float | None:
    """Parse an OSM ``maxspeed`` value into a speed in **mph**.

    OSM ``maxspeed`` defaults to km/h when unit-less; an explicit ``mph``
    suffix is honoured. The result is normalized to mph (US GMNS convention)
    and rounded to one decimal.

    Args:
        value: Raw ``maxspeed`` value (e.g. ``"30 mph"``, ``"50"``,
            ``"50 km/h"``) or ``None``.

    Returns:
        Speed in mph, or ``None`` if absent / not numeric.

    Examples:
        >>> parse_speed("30 mph")
        30.0
        >>> parse_speed("walk") is None
        True
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    is_mph = "mph" in text
    number = text.replace("mph", "").replace("km/h", "").replace("kph", "").strip()
    try:
        speed = float(number)
    except ValueError:
        return None
    if is_mph:
        return round(speed, 1)
    # Unit-less or km/h -> OSM default is km/h -> convert to mph.
    return round(speed / _KMH_PER_MPH, 1)


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "direct": direct,
    "parse_int": parse_int,
    "parse_speed": parse_speed,
}
"""Registry of named transforms referenced by ``transform:`` in the mapping YAML."""


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def oneway_direction(osm_tags: Mapping[str, Any]) -> str:
    """Classify a way's travel direction from its OSM tags.

    Returns one of ``"forward"`` (travel only node-order direction),
    ``"backward"`` (reverse of node order), or ``"both"`` (two-way). An
    explicit ``oneway`` tag wins; otherwise oneway is implied for motorways
    and roundabouts.

    Args:
        osm_tags: The way's tag mapping.

    Returns:
        ``"forward"``, ``"backward"``, or ``"both"``.

    Examples:
        >>> oneway_direction({"oneway": "yes"})
        'forward'
        >>> oneway_direction({"junction": "roundabout"})
        'forward'
        >>> oneway_direction({})
        'both'
    """
    raw = osm_tags.get("oneway")
    if raw is not None:
        value = str(raw).strip().lower()
        if value in _ONEWAY_FORWARD:
            return "forward"
        if value in _ONEWAY_BACKWARD:
            return "backward"
        if value in _ONEWAY_BOTH:
            return "both"
    if str(osm_tags.get("junction", "")).lower() in {"roundabout", "circular"}:
        return "forward"
    if osm_tags.get("highway") in _IMPLIED_ONEWAY_HIGHWAY:
        return "forward"
    return "both"


# ---------------------------------------------------------------------------
# Mapping + filter data loading
# ---------------------------------------------------------------------------


def _load_mapping_yaml(filename: str) -> Any:
    """Read and parse a YAML data file shipped under ``netstead/osm/mappings/``."""
    path = resources.files(__package__) / "mappings" / filename
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def load_field_mappings() -> dict[str, dict[str, str]]:
    """Load the OSM-tag -> GMNS-field mapping from ``osm_to_gmns.yaml``.

    Returns:
        Mapping ``{gmns_field: {"osm_tag": str, "transform": str}}``. The
        ``transform`` value is a key into :data:`TRANSFORMS`.

    Examples:
        >>> m = load_field_mappings()
        >>> m["facility_type"]["osm_tag"]
        'highway'
    """
    return _load_mapping_yaml("osm_to_gmns.yaml")


@cache
def load_network_filters() -> dict[str, list[str]]:
    """Load the per-``network_type`` allowed-``highway`` lists from YAML.

    Returns:
        Mapping ``{network_type: [allowed highway values]}``.

    Examples:
        >>> f = load_network_filters()
        >>> "roads" in f and "paths" in f
        True
    """
    return _load_mapping_yaml("osm_network_filters.yaml")


def allowed_highways(network_type: NetworkTypes) -> frozenset[str]:
    """Return the set of OSM ``highway`` values allowed for ``network_type``.

    Args:
        network_type: One of the keys in ``osm_network_filters.yaml``
            (``roads``, ``paths``; legacy ``drive``/``walk``/``bike``/``all`` map onto them).

    Returns:
        A frozenset of allowed ``highway`` values.

    Raises:
        ValueError: If ``network_type`` is not a known key.

    Examples:
        >>> "motorway" in allowed_highways("roads")
        True
        >>> "footway" in allowed_highways("roads")
        False
    """
    return allowed_union(load_network_filters(), network_type)


def accepts_highway(network_type: NetworkTypes, highway: Any) -> bool:
    """Report whether a ``highway`` value belongs to ``network_type``.

    Args:
        network_type: One of the keys in ``osm_network_filters.yaml``.
        highway: The OSM ``highway`` tag value to test.

    Returns:
        ``True`` if the value is in the network's allow-list, else ``False``.

    Raises:
        ValueError: If ``network_type`` is not a known key.

    Examples:
        >>> accepts_highway("roads", "residential")
        True
        >>> accepts_highway("paths", "footway")
        True
    """
    return highway in allowed_highways(network_type)


def apply_mapping(osm_tags: Mapping[str, Any], extra_tags: list[str] | None = None) -> dict[str, Any]:
    """Map a way's OSM tags onto GMNS link fields, with optional passthrough tags.

    Every mapped field is present in the result (``None`` when the source tag
    is absent) so all link records share a uniform column set. Each name in
    ``extra_tags`` is carried through verbatim as its own column (value or
    ``None``).

    Args:
        osm_tags: The way's tag mapping.
        extra_tags: Additional OSM tag keys to carry through unchanged.

    Returns:
        A dict of GMNS link field values plus any requested ``extra_tags``.

    Examples:
        >>> apply_mapping({"highway": "primary", "name": "Broadway"})["facility_type"]
        'primary'
        >>> apply_mapping({"highway": "primary"}, extra_tags=["surface"])["surface"] is None
        True
    """
    mapping = load_field_mappings()
    out: dict[str, Any] = {}
    for field, spec in mapping.items():
        raw = osm_tags.get(spec["osm_tag"])
        out[field] = TRANSFORMS[spec["transform"]](raw)
    for tag in extra_tags or []:
        out[tag] = osm_tags.get(tag)
    return out
