"""Decode a link-geometry cell (WKT ``str`` **or** WKB ``bytes``) to a shapely geometry.

The binary-geometry migration (geometry ADR, 2026-10-01) makes WKB bytes the
canonical in-memory encoding, but CSV-sourced data still arrives as WKT text and
older callers may hold either. The shapely-dependent consumers (``clean``,
``indexes.spatial``, ``quality.rules``, ``scope``) need full shapely geometries
— for ``STRtree`` / ``simplify`` / ``length`` / ``unary_union`` — not just the
coordinate pairs that the dep-free :func:`netstead._wkt.linestring_points` yields.
This one helper lets every such site accept both encodings without each
re-implementing the dispatch.

``shapely`` is imported lazily inside the function: callers are already
shapely-gated (it ships in the ``clean`` extra), and keeping the import out of
module scope means ``import netstead._geom`` stays cheap and dependency-free.
"""

from __future__ import annotations

from typing import Any

__all__ = ["shapely_from_any"]


def shapely_from_any(geom: object) -> Any | None:
    """Return a shapely geometry from a WKT ``str`` or WKB ``bytes`` cell.

    Dispatches on type: ``bytes`` / ``bytearray`` / ``memoryview`` → ``from_wkb``;
    a non-empty ``str`` → ``from_wkt``. ``None``, empty/whitespace strings, and
    anything unparseable (truncated WKB, malformed WKT, a non-geometry value)
    return ``None`` rather than raising — matching the existing "skip rows whose
    geometry won't parse" behavior at the call sites.
    """
    if geom is None:
        return None
    # Local import: callers are shapely-gated; keep module import dependency-free.
    from shapely import from_wkb, from_wkt

    try:
        if isinstance(geom, (bytes, bytearray, memoryview)):
            return from_wkb(bytes(geom))
        if isinstance(geom, str):
            if not geom.strip():
                return None
            return from_wkt(geom)
    except Exception:  # pragma: no cover - shapely raises broadly on bad input
        return None
    return None
