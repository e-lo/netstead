"""Network types (``roads`` / ``paths``): one name or several, as the OSM and Overture builders take them.

A build with several types keeps a way or segment if *any* of them allows it (the union of their allow-lists).

The retired names still replay: ``drive`` means ``roads``; ``walk``, ``bike`` and ``all`` mean ``roads`` + ``paths``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: One network type, or several to build as their union.
NetworkTypes = str | Sequence[str]

#: The network types a build accepts.
VALID_TYPES: tuple[str, ...] = ("roads", "paths")

#: Retired names -> what they now build (recorded actions, old settings and scripts still use them).
LEGACY_TYPES: dict[str, tuple[str, ...]] = {
    "drive": ("roads",),
    "walk": ("roads", "paths"),
    "bike": ("roads", "paths"),
    "all": ("roads", "paths"),
}


def as_types(network_type: NetworkTypes) -> tuple[str, ...]:
    """The distinct network types in ``network_type``, in order, with legacy names mapped.

    A bare string is a one-element tuple. Names are not checked here (see :func:`normalize_types` and
    :func:`allowed_union`).

    Raises:
        ValueError: no network type given.

    Examples:
        >>> as_types("roads")
        ('roads',)
        >>> as_types(["paths", "roads", "paths"])
        ('paths', 'roads')
        >>> as_types("drive")
        ('roads',)
        >>> as_types("bike")
        ('roads', 'paths')
    """
    names = (network_type,) if isinstance(network_type, str) else tuple(network_type)
    if not names:
        raise ValueError("network_type needs at least one type")
    mapped = (new for name in names for new in LEGACY_TYPES.get(name, (name,)))
    return tuple(dict.fromkeys(mapped))


def normalize_types(network_type: NetworkTypes) -> list[str]:
    """:func:`as_types`, checked against :data:`VALID_TYPES`.

    Raises:
        ValueError: no type given, or a name that is neither valid nor legacy (the message lists the valid ones).

    Examples:
        >>> normalize_types("walk")
        ['roads', 'paths']
    """
    types = as_types(network_type)
    for name in types:
        if name not in VALID_TYPES:
            legacy = ", ".join(LEGACY_TYPES)
            raise ValueError(f"unknown network type {name!r}; expected one of {list(VALID_TYPES)} (or legacy {legacy})")
    return list(types)


def allowed_union(filters: Mapping[str, Sequence[str]], network_type: NetworkTypes) -> frozenset[str]:
    """The allowed values for ``network_type`` under ``filters``.

    Raises:
        ValueError: a type is not a key of ``filters``, or none was given.

    Examples:
        >>> sorted(allowed_union({"a": ["x"], "b": ["y"]}, ["a", "b"]))
        ['x', 'y']
    """
    allowed: set[str] = set()
    for name in as_types(network_type):
        if name not in filters:
            raise ValueError(f"unknown network_type {name!r}; expected one of {sorted(filters)}")
        allowed.update(filters[name])
    return frozenset(allowed)
