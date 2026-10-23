"""Network types (``drive``/``walk``/``bike``/``all``): one name or several, as the OSM and Overture builders take them.

A build with several types keeps a way or segment if *any* of them allows it (the union of their allow-lists). An
empty allow-list (``all``) means "no restriction", so it makes the whole union unrestricted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: One network type, or several to build as their union.
NetworkTypes = str | Sequence[str]


def as_types(network_type: NetworkTypes) -> tuple[str, ...]:
    """The distinct network types in ``network_type``, in order (a bare string is a one-element tuple).

    Raises:
        ValueError: no network type given.

    Examples:
        >>> as_types("drive")
        ('drive',)
        >>> as_types(["bike", "drive", "bike"])
        ('bike', 'drive')
    """
    types = (network_type,) if isinstance(network_type, str) else tuple(dict.fromkeys(network_type))
    if not types:
        raise ValueError("network_type needs at least one type")
    return types


def allowed_union(filters: Mapping[str, Sequence[str] | None], network_type: NetworkTypes) -> frozenset[str]:
    """The allowed values for ``network_type`` under ``filters``; empty means "no restriction".

    Raises:
        ValueError: a type is not a key of ``filters``, or none was given.

    Examples:
        >>> sorted(allowed_union({"a": ["x"], "b": ["y"], "all": []}, ["a", "b"]))
        ['x', 'y']
        >>> allowed_union({"a": ["x"], "all": []}, ["a", "all"])
        frozenset()
    """
    allowed: set[str] = set()
    unrestricted = False
    for name in as_types(network_type):
        if name not in filters:
            raise ValueError(f"unknown network_type {name!r}; expected one of {sorted(filters)}")
        values = filters[name] or []
        unrestricted = unrestricted or not values
        allowed.update(values)
    return frozenset() if unrestricted else frozenset(allowed)
