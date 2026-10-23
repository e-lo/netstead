"""Pure geometry helpers for the Overture converter (no external deps).

Overture segments carry a LineString geometry plus a ``connectors[]`` array
whose ``at`` values are **normalized linear references** in ``[0, 1]`` along
that geometry. Splitting a segment into GMNS links therefore means slicing the
LineString between consecutive ``at`` values — which is what :func:`slice_line`
does. The remaining helpers format WKT, parse it back, and measure geodesic
length (shared concern with the OSM converter; kept here so the Overture
subpackage stays self-contained and needs no geometry-library dependency).

All coordinates are ``(lon, lat)`` tuples in EPSG:4326. The ``at`` fraction is
interpreted against the geometry's own planar (2-D) length, matching Overture's
definition of the linear reference.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

__all__ = [
    "line_length_m",
    "parse_wkt_linestring",
    "slice_line",
    "wkt_linestring",
]

# Mean Earth radius (IUGG), metres — used for geodesic (haversine) length.
_EARTH_RADIUS_M = 6371008.8

Coord = tuple[float, float]


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Return the great-circle distance between two lon/lat points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def line_length_m(coords: Sequence[Coord]) -> float:
    """Return the geodesic length of a ``(lon, lat)`` polyline, in metres.

    Args:
        coords: Ordered ``(lon, lat)`` vertices.

    Returns:
        The summed great-circle length in metres (``0.0`` for < 2 points).

    Examples:
        >>> round(line_length_m([(0.0, 0.0), (0.0, 1.0)]))
        111195
    """
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in pairwise(coords):
        total += _haversine_m(lon1, lat1, lon2, lat2)
    return total


def wkt_linestring(coords: Sequence[Coord]) -> str:
    """Format a ``(lon, lat)`` coordinate sequence as a WKT ``LINESTRING``.

    Args:
        coords: Ordered ``(lon, lat)`` vertices.

    Returns:
        A ``LINESTRING (lon lat, ...)`` WKT string.

    Examples:
        >>> wkt_linestring([(0.0, 0.0), (0.0, 1.0)])
        'LINESTRING (0.0 0.0, 0.0 1.0)'
    """
    points = ", ".join(f"{lon} {lat}" for lon, lat in coords)
    return f"LINESTRING ({points})"


def parse_wkt_linestring(wkt: str) -> list[Coord]:
    """Parse a WKT ``LINESTRING`` into ``(lon, lat)`` tuples.

    Only the ``LINESTRING`` type is supported (the sole geometry type Overture
    road segments use); any other geometry raises :class:`ValueError`.

    Args:
        wkt: A WKT ``LINESTRING`` string (case-insensitive keyword).

    Returns:
        The ordered list of ``(lon, lat)`` vertices.

    Raises:
        ValueError: If ``wkt`` is not a parseable ``LINESTRING``.

    Examples:
        >>> parse_wkt_linestring("LINESTRING (0 0, 0 1.5)")
        [(0.0, 0.0), (0.0, 1.5)]
    """
    text = wkt.strip()
    upper = text.upper()
    if not upper.startswith("LINESTRING"):
        raise ValueError(f"expected a WKT LINESTRING, got {wkt[:32]!r}")
    inner = text[text.index("(") + 1 : text.rindex(")")]
    coords: list[Coord] = []
    for pair in inner.split(","):
        lon_str, lat_str = pair.split()
        coords.append((float(lon_str), float(lat_str)))
    return coords


def _cumulative_fractions(coords: Sequence[Coord]) -> list[float]:
    """Return per-vertex cumulative planar-length fractions in ``[0, 1]``.

    Planar (2-D euclidean) length is used — not geodesic — because Overture's
    ``at`` linear reference is defined against the geometry's own 2-D length.
    """
    segment_lengths = [math.dist(a, b) for a, b in pairwise(coords)]
    total = sum(segment_lengths)
    if total == 0:
        # Degenerate (all vertices coincide): spread fractions uniformly so
        # interpolation stays well-defined rather than dividing by zero.
        n = len(coords)
        return [i / (n - 1) for i in range(n)] if n > 1 else [0.0]
    fractions = [0.0]
    running = 0.0
    for length in segment_lengths:
        running += length
        fractions.append(running / total)
    return fractions


def _interpolate(coords: Sequence[Coord], fractions: Sequence[float], at: float) -> Coord:
    """Return the ``(lon, lat)`` point at linear-reference ``at`` in ``[0, 1]``."""
    if at <= 0:
        return coords[0]
    if at >= 1:
        return coords[-1]
    for i in range(1, len(fractions)):
        if fractions[i] >= at:
            lo, hi = fractions[i - 1], fractions[i]
            span = hi - lo
            t = 0.0 if span == 0 else (at - lo) / span
            (lon0, lat0), (lon1, lat1) = coords[i - 1], coords[i]
            return (lon0 + t * (lon1 - lon0), lat0 + t * (lat1 - lat0))
    return coords[-1]


def slice_line(coords: Sequence[Coord], at_start: float, at_end: float) -> list[Coord]:
    """Return the sub-LineString of ``coords`` between two ``at`` fractions.

    The returned polyline starts at the interpolated ``at_start`` point, keeps
    every original vertex strictly between the two fractions, and ends at the
    interpolated ``at_end`` point — the faithful geometry of the Overture
    sub-segment between two consecutive connectors.

    Args:
        coords: The full segment's ordered ``(lon, lat)`` vertices.
        at_start: Linear reference of the slice start, in ``[0, 1]``.
        at_end: Linear reference of the slice end, in ``[0, 1]``.

    Returns:
        The sliced ``(lon, lat)`` vertices (always at least the two endpoints).

    Raises:
        ValueError: If fewer than two coordinates are given, or
            ``at_start >= at_end``.

    Examples:
        >>> slice_line([(0.0, 0.0), (0.0, 2.0)], 0.0, 0.5)
        [(0.0, 0.0), (0.0, 1.0)]
    """
    if len(coords) < 2:
        raise ValueError("slice_line requires at least two coordinates")
    if at_start >= at_end:
        raise ValueError(f"at_start ({at_start}) must be < at_end ({at_end})")
    fractions = _cumulative_fractions(coords)
    start_pt = _interpolate(coords, fractions, at_start)
    end_pt = _interpolate(coords, fractions, at_end)
    middle = [coords[i] for i in range(len(coords)) if at_start < fractions[i] < at_end]
    return [start_pt, *middle, end_pt]
