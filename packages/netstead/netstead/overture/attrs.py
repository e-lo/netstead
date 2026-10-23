"""Overture property transforms, GMNS field mapping, and network-type filters.

Mirror of :mod:`netstead.osm.tags` for the Overture source. Which Overture
segment property feeds which GMNS field — and which ``class`` values count as
drivable / walkable / bikeable — lives in maintained YAML under ``mappings/``,
so retargeting a field is a data edit, not a code change. Value-level logic that
cannot be data (scoped-rule reduction, unit normalization, direction inference)
lives in the :data:`TRANSFORMS` registry and :func:`overture_direction` below.

Several Overture attributes are **arrays of scoped rules** (``{..., between:
[s, e], when: {...}}``) rather than flat tags, so a transform receives the whole
array and reduces it to a scalar. The reduction policy (documented per
transform) is: prefer the unscoped / whole-segment rule, else the first rule.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import cache
from importlib import resources
from typing import Any

import yaml

__all__ = [
    "TRANSFORMS",
    "accepts_class",
    "allowed_classes",
    "apply_mapping",
    "direct",
    "lane_count",
    "load_field_mappings",
    "load_network_filters",
    "max_speed_mph",
    "overture_direction",
    "primary_ref",
    "resolve_path",
]

_KMH_PER_MPH = 1.60934
# Overture speed-limit unit spellings, lower-cased.
_MPH_UNITS = frozenset({"mph", "mi/h"})
_KMH_UNITS = frozenset({"km/h", "kmh", "kph"})
# Access-restriction access types that forbid travel.
_DENY_ACCESS = frozenset({"denied", "no"})


# ---------------------------------------------------------------------------
# Property-path resolution
# ---------------------------------------------------------------------------


def resolve_path(record: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted ``path`` into a (possibly nested) Overture record.

    Each dot descends one mapping level; a missing or non-mapping intermediate
    short-circuits to ``None`` (so ``names.primary`` is safe even when the
    segment has no ``names``).

    Args:
        record: The Overture segment record (nested dicts / lists).
        path: A dotted key path, e.g. ``"names.primary"`` or ``"class"``.

    Returns:
        The resolved value, or ``None`` if any step is absent.

    Examples:
        >>> resolve_path({"names": {"primary": "Main St"}}, "names.primary")
        'Main St'
        >>> resolve_path({"names": None}, "names.primary") is None
        True
    """
    current: Any = record
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


# ---------------------------------------------------------------------------
# Named transforms — referenced by name from overture_to_gmns.yaml
# ---------------------------------------------------------------------------


def direct(value: Any) -> Any:
    """Return ``value`` unchanged (identity transform for verbatim fields).

    Args:
        value: The resolved property value (or ``None`` when absent).

    Returns:
        The value unchanged.

    Examples:
        >>> direct("residential")
        'residential'
        >>> direct(None) is None
        True
    """
    return value


def _pick_rule(rules: Any) -> Mapping[str, Any] | None:
    """Return the governing rule from a scoped-rule array.

    Policy: the first rule with no ``between`` sub-range (i.e. it applies to the
    whole segment) wins; absent that, the first rule; ``None`` if empty.
    """
    if not isinstance(rules, Sequence) or isinstance(rules, str | bytes):
        return None
    mappings = [r for r in rules if isinstance(r, Mapping)]
    if not mappings:
        return None
    for rule in mappings:
        if rule.get("between") is None:
            return rule
    return mappings[0]


def max_speed_mph(value: Any) -> float | None:
    """Reduce an Overture ``speed_limits`` array to a single speed in **mph**.

    Picks the whole-segment rule (see :func:`_pick_rule`), reads its
    ``max_speed`` ``{value, unit}``, and normalizes to mph. Overture carries an
    explicit unit, so — unlike OSM — no km/h default guessing is needed.

    Args:
        value: The segment's ``speed_limits`` array (or ``None``).

    Returns:
        Speed in mph rounded to one decimal, or ``None`` when absent/unparseable.

    Examples:
        >>> max_speed_mph([{"max_speed": {"value": 30, "unit": "mph"}}])
        30.0
        >>> max_speed_mph([{"max_speed": {"value": 100, "unit": "km/h"}}])
        62.1
        >>> max_speed_mph(None) is None
        True
    """
    rule = _pick_rule(value)
    if rule is None:
        return None
    max_speed = rule.get("max_speed")
    if not isinstance(max_speed, Mapping):
        return None
    raw = max_speed.get("value")
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        return None
    unit = str(max_speed.get("unit", "")).strip().lower()
    if unit in _KMH_UNITS:
        return round(speed / _KMH_PER_MPH, 1)
    if unit in _MPH_UNITS or not unit:
        # Overture road speeds default to mph only in the US; an explicit unit
        # is the norm. An absent unit is treated as mph (US GMNS convention).
        return round(speed, 1)
    return round(speed, 1)


def primary_ref(value: Any) -> str | None:
    """Return the first route reference (e.g. ``"I-40"``) from a ``routes`` array.

    Args:
        value: The segment's ``routes`` array (or ``None``).

    Returns:
        The first non-empty ``ref`` string, or ``None`` when absent.

    Examples:
        >>> primary_ref([{"ref": "US 1"}, {"ref": "NC 50"}])
        'US 1'
        >>> primary_ref([]) is None
        True
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    for route in value:
        if isinstance(route, Mapping):
            ref = route.get("ref")
            if ref:
                return str(ref)
    return None


def lane_count(value: Any) -> int | None:
    """Best-effort scalar motor-vehicle lane count.

    Overture lane information is less first-class than OSM's ``lanes=`` tag — it
    is typically an array of (direction-/range-scoped) lane rules. Phase 1 takes
    the honest "unknown rather than guess" stance: a plain integer passes
    through, anything scoped/complex/absent yields ``None``. Full lane fidelity
    is a later-phase ``lane`` / ``segment_lane`` concern.

    Args:
        value: The segment's ``lanes`` property (plain int, array, or ``None``).

    Returns:
        The lane count when it is an unambiguous integer, else ``None``.

    Examples:
        >>> lane_count(2)
        2
        >>> lane_count([{"direction": "forward"}]) is None
        True
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "direct": direct,
    "max_speed_mph": max_speed_mph,
    "primary_ref": primary_ref,
    "lane_count": lane_count,
}
"""Registry of named transforms referenced by ``transform:`` in the mapping YAML."""


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def overture_direction(segment: Mapping[str, Any]) -> str:
    """Classify a segment's travel direction from its access restrictions.

    Overture has no single ``oneway`` tag; it denies travel in a heading via
    ``access_restrictions[].when.heading`` (``forward`` / ``backward``). The
    Phase-1 policy starts from "both directions allowed" and drops any heading a
    deny rule forbids. Mode- and time-scoped nuance (``when.mode`` /
    ``when.recognized``) is intentionally ignored here (Phase 2); this is the
    main correctness risk flagged in the scope doc, hence the fixture coverage.

    Args:
        segment: The Overture segment record.

    Returns:
        ``"forward"`` (travel only in connector order), ``"backward"``
        (reverse), or ``"both"``.

    Examples:
        >>> overture_direction({})
        'both'
        >>> seg = {"access_restrictions": [
        ...     {"access_type": "denied", "when": {"heading": "backward"}}]}
        >>> overture_direction(seg)
        'forward'
    """
    allowed = {"forward", "backward"}
    restrictions = segment.get("access_restrictions")
    if isinstance(restrictions, Sequence) and not isinstance(restrictions, str | bytes):
        for rule in restrictions:
            if not isinstance(rule, Mapping):
                continue
            if str(rule.get("access_type", "")).strip().lower() not in _DENY_ACCESS:
                continue
            when = rule.get("when")
            heading = when.get("heading") if isinstance(when, Mapping) else None
            if heading in allowed:
                allowed.discard(heading)
    if allowed == {"forward"}:
        return "forward"
    if allowed == {"backward"}:
        return "backward"
    # Both allowed, or both denied (a fully-closed segment is still emitted as a
    # two-way link rather than silently dropped) -> two-way.
    return "both"


# ---------------------------------------------------------------------------
# Mapping + filter data loading
# ---------------------------------------------------------------------------


def _load_mapping_yaml(filename: str) -> Any:
    """Read and parse a YAML data file shipped under ``netstead/overture/mappings/``."""
    path = resources.files(__package__) / "mappings" / filename
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def load_field_mappings() -> dict[str, dict[str, str]]:
    """Load the Overture-property -> GMNS-field mapping from ``overture_to_gmns.yaml``.

    Returns:
        Mapping ``{gmns_field: {"source": str, "transform": str}}``. The
        ``transform`` value is a key into :data:`TRANSFORMS`; ``source`` is a
        dotted property path resolved by :func:`resolve_path`.

    Examples:
        >>> m = load_field_mappings()
        >>> m["facility_type"]["source"]
        'class'
    """
    return _load_mapping_yaml("overture_to_gmns.yaml")


@cache
def load_network_filters() -> dict[str, list[str]]:
    """Load the per-``network_type`` allowed-``class`` lists from YAML.

    Returns:
        Mapping ``{network_type: [allowed class values]}``. An empty list means
        "accept any class" (used by ``all``).

    Examples:
        >>> f = load_network_filters()
        >>> "drive" in f and "all" in f
        True
    """
    return _load_mapping_yaml("overture_network_filters.yaml")


def allowed_classes(network_type: str) -> frozenset[str]:
    """Return the set of Overture ``class`` values allowed for ``network_type``.

    Args:
        network_type: One of the keys in ``overture_network_filters.yaml``
            (``drive``, ``walk``, ``bike``, ``all``).

    Returns:
        A frozenset of allowed ``class`` values. Empty for ``all`` (meaning no
        restriction — see :func:`accepts_class`).

    Raises:
        ValueError: If ``network_type`` is not a known key.

    Examples:
        >>> "motorway" in allowed_classes("drive")
        True
        >>> "footway" in allowed_classes("drive")
        False
    """
    filters = load_network_filters()
    if network_type not in filters:
        raise ValueError(f"unknown network_type {network_type!r}; expected one of {sorted(filters)}")
    return frozenset(filters[network_type] or [])


def accepts_class(network_type: str, class_value: Any) -> bool:
    """Report whether a ``class`` value belongs to ``network_type``.

    An empty allow-list (``all``) accepts any value.

    Args:
        network_type: One of the keys in ``overture_network_filters.yaml``.
        class_value: The Overture ``class`` value to test.

    Returns:
        ``True`` if the value is in the network's allow-list (or the list is
        empty), else ``False``.

    Raises:
        ValueError: If ``network_type`` is not a known key.

    Examples:
        >>> accepts_class("drive", "residential")
        True
        >>> accepts_class("all", "anything")
        True
    """
    allowed = allowed_classes(network_type)
    if not allowed:
        return True
    return class_value in allowed


def apply_mapping(segment: Mapping[str, Any], extra_tags: list[str] | None = None) -> dict[str, Any]:
    """Map a segment's Overture properties onto GMNS link fields.

    Every mapped field is present in the result (``None`` when the source
    property is absent) so all link records share a uniform column set. Each
    name in ``extra_tags`` is a dotted property path carried through as its own
    column (resolved value or ``None``); the column name is the path's last
    segment.

    Args:
        segment: The Overture segment record.
        extra_tags: Additional dotted property paths to carry through.

    Returns:
        A dict of GMNS link field values plus any requested ``extra_tags``.

    Examples:
        >>> apply_mapping({"class": "primary", "names": {"primary": "Broadway"}})["facility_type"]
        'primary'
        >>> apply_mapping({"class": "primary"}, extra_tags=["subclass"])["subclass"] is None
        True
    """
    mapping = load_field_mappings()
    out: dict[str, Any] = {}
    for field, spec in mapping.items():
        raw = resolve_path(segment, spec["source"])
        out[field] = TRANSFORMS[spec["transform"]](raw)
    for path in extra_tags or []:
        column = path.split(".")[-1]
        out[column] = resolve_path(segment, path)
    return out
