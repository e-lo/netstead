"""Area resolution + OpenStreetMap fetching via Nominatim (geocode) and Overpass.

This module is the only one in :mod:`netstead.osm` that touches the network. It
turns a user-supplied area into a bbox or boundary polygon, builds an Overpass
QL query for the requested ``network_type``, fetches the raw OSM elements, and
parses them into the ``(nodes, ways)`` contract :mod:`netstead.osm.convert`
consumes.

Conventions:
    * **bbox** is ``(west, south, east, north)`` in EPSG:4326.
    * a **point** is ``(lat, lon)`` (osmnx convention).
    * a **place** is a free-text string resolved through Nominatim.

External-service etiquette: every request sends a descriptive ``User-Agent``
and retries with backoff on transient Overpass/Nominatim status codes. Large
extracts should target a self-hosted Overpass endpoint (``endpoint=``) and the
public Nominatim instance must not be used for bulk geocoding. OSM data is
ODbL-licensed — attribute it in derived products.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

import requests

from netstead.network_types import NetworkTypes

from . import tags

__all__ = [
    "NOMINATIM_URL",
    "OVERPASS_URL",
    "USER_AGENT",
    "build_overpass_query",
    "fetch_network_elements",
    "fetch_osm",
    "geocode_area",
    "geocode_candidates",
    "parse_overpass_elements",
    "point_buffer_bbox",
    "resolve_area",
]

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "netstead-osm/1.0 (+https://github.com/e-lo/netstead)"

# Metres per degree of latitude (spherical approximation; good enough for
# turning a buffer distance into a bounding box).
_M_PER_DEG_LAT = 111320.0

# Overpass/Nominatim status codes worth retrying (server-side / rate limit).
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


def point_buffer_bbox(lat: float, lon: float, buffer_m: float) -> tuple[float, float, float, float]:
    """Return a ``(west, south, east, north)`` bbox around a point.

    Args:
        lat: Latitude of the center point.
        lon: Longitude of the center point.
        buffer_m: Half-width of the box in metres (added on every side).

    Returns:
        The bounding box as ``(west, south, east, north)`` in degrees.

    Examples:
        >>> w, s, e, n = point_buffer_bbox(0.0, 0.0, 111320.0)
        >>> round(e, 3), round(n, 3)
        (1.0, 1.0)
    """
    dlat = buffer_m / _M_PER_DEG_LAT
    cos_lat = math.cos(math.radians(lat))
    dlon = buffer_m / (_M_PER_DEG_LAT * cos_lat) if cos_lat else dlat
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def parse_overpass_elements(
    elements: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    """Split a raw Overpass ``elements`` list into node coords and ways.

    Args:
        elements: The ``elements`` array from an Overpass JSON response.

    Returns:
        ``(nodes, ways)`` where ``nodes`` maps ``osm_node_id -> (lon, lat)``
        and ``ways`` is a list of ``{"id", "nodes", "tags"}`` dicts.

    Examples:
        >>> els = [
        ...     {"type": "node", "id": 1, "lat": 42.0, "lon": -71.0},
        ...     {"type": "way", "id": 9, "nodes": [1], "tags": {"highway": "residential"}},
        ... ]
        >>> nodes, ways = parse_overpass_elements(els)
        >>> nodes[1]
        (-71.0, 42.0)
        >>> ways[0]["id"]
        9
    """
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []
    for element in elements:
        kind = element.get("type")
        if kind == "node":
            nodes[element["id"]] = (element["lon"], element["lat"])
        elif kind == "way":
            ways.append(
                {
                    "id": element["id"],
                    "nodes": element.get("nodes", []),
                    "tags": element.get("tags", {}),
                }
            )
    return nodes, ways


def build_overpass_query(
    *,
    bbox: tuple[float, float, float, float] | None = None,
    polygon: Sequence[tuple[float, float]] | None = None,
    network_type: NetworkTypes = "drive",
    timeout: int = 180,
    out: Literal["body", "count"] = "body",
) -> str:
    """Build an Overpass QL query for ``highway`` ways within an area.

    Exactly one of ``bbox`` or ``polygon`` must be supplied. The way filter is
    derived from the ``network_type`` allow-list (see
    :func:`netstead.osm.tags.allowed_highways`); ``all`` applies no class filter.
    With ``out="body"`` the query recurses to member nodes (``(._;>;)``) so
    whole ways are returned; ``out="count"`` asks only for the number of
    matching ways (one ``count`` element), the cheap pre-query a build
    estimate uses.

    Args:
        bbox: ``(west, south, east, north)`` bounding box.
        polygon: Boundary as a sequence of ``(lat, lon)`` vertices.
        network_type: One of ``drive``/``walk``/``bike``/``all``.
        timeout: Overpass server-side timeout, seconds.
        out: ``"body"`` for the full ways + nodes, ``"count"`` for the way count only.

    Returns:
        The Overpass QL query string.

    Raises:
        ValueError: If neither ``bbox`` nor ``polygon`` is given.

    Examples:
        >>> "out:json" in build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1))
        True
    """
    if bbox is None and polygon is None:
        raise ValueError("build_overpass_query requires either a bbox or a polygon")

    allowed = tags.allowed_highways(network_type)
    hw_classes = "|".join(sorted(allowed))
    way_filter = f'["highway"~"^({hw_classes})$"]' if allowed else '["highway"]'

    if polygon is not None:
        poly_str = " ".join(f"{lat} {lon}" for lat, lon in polygon)
        area_filter = f'(poly:"{poly_str}")'
    else:
        west, south, east, north = bbox  # type: ignore[misc]
        area_filter = f"({south},{west},{north},{east})"

    if out == "count":
        return f"[out:json][timeout:{timeout}];way{way_filter}{area_filter};out count;"
    return f"[out:json][timeout:{timeout}];way{way_filter}{area_filter};(._;>;);out body;"


def _with_retry(call: Callable[[], Any], *, retries: int, sleep: Callable[[float], None]) -> Any:
    """Invoke ``call`` (returning an HTTP response), retrying on transient codes."""
    response = None
    for attempt in range(retries + 1):
        response = call()
        if response.status_code in _RETRYABLE_STATUS and attempt < retries:
            sleep(1.0 * (2**attempt))
            continue
        break
    response.raise_for_status()
    return response


def fetch_osm(
    overpass_query: str,
    *,
    endpoint: str = OVERPASS_URL,
    session: Any = None,
    user_agent: str = USER_AGENT,
    timeout: int = 180,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """POST an Overpass query and return the ``elements`` array.

    Args:
        overpass_query: A complete Overpass QL query string.
        endpoint: Overpass API endpoint URL.
        session: An object exposing ``post(url, data, headers, timeout)``.
            Defaults to the :mod:`requests` module.
        user_agent: ``User-Agent`` header value.
        timeout: Per-request timeout, seconds.
        retries: Number of retries on transient status codes.
        sleep: Sleep function used between retries (injectable for tests).

    Returns:
        The list of OSM elements from the response.
    """
    http = session or requests
    response = _with_retry(
        lambda: http.post(endpoint, data=overpass_query, headers={"User-Agent": user_agent}, timeout=timeout),
        retries=retries,
        sleep=sleep,
    )
    return response.json().get("elements", [])


def _geojson_to_latlon(geojson: Mapping[str, Any]) -> list[tuple[float, float]] | None:
    """Convert a GeoJSON Polygon/MultiPolygon outer ring to ``(lat, lon)`` vertices."""
    geom_type = geojson.get("type")
    coords = geojson.get("coordinates")
    if not coords:
        return None
    if geom_type == "Polygon":
        ring = coords[0]
    elif geom_type == "MultiPolygon":
        ring = coords[0][0]
    else:
        return None
    return [(lat, lon) for lon, lat in ring]


def geocode_area(
    place: str,
    *,
    base_url: str = NOMINATIM_URL,
    session: Any = None,
    user_agent: str = USER_AGENT,
    timeout: int = 30,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Geocode a place name to a bbox and (when available) a boundary polygon.

    Args:
        place: Free-text place query (city, county, address, ...).
        base_url: Nominatim search endpoint.
        session: An object exposing ``get(url, params, headers, timeout)``.
            Defaults to the :mod:`requests` module.
        user_agent: ``User-Agent`` header value (required by Nominatim policy).
        timeout: Per-request timeout, seconds.
        retries: Number of retries on transient status codes.
        sleep: Sleep function used between retries (injectable for tests).

    Returns:
        ``{"bbox": (west, south, east, north), "polygon": [(lat, lon), ...] | None}``.

    Raises:
        LookupError: If the place could not be geocoded.
    """
    http = session or requests
    params = {"q": place, "format": "json", "polygon_geojson": 1, "limit": 1}
    response = _with_retry(
        lambda: http.get(base_url, params=params, headers={"User-Agent": user_agent}, timeout=timeout),
        retries=retries,
        sleep=sleep,
    )
    data = response.json()
    if not data:
        raise LookupError(f"could not geocode place {place!r}")
    first = data[0]
    if "boundingbox" not in first:
        # Nominatim occasionally returns a hit without a bounding box (very
        # broad / abstract entries). Treat as ungeocodable rather than letting
        # a bare KeyError escape.
        raise LookupError(f"could not geocode place {place!r}: result has no boundingbox")
    south, north, west, east = (float(v) for v in first["boundingbox"])
    polygon = None
    geojson = first.get("geojson")
    if geojson:
        polygon = _geojson_to_latlon(geojson)
    return {"bbox": (west, south, east, north), "polygon": polygon}


def geocode_candidates(
    q: str,
    *,
    limit: int = 8,
    base_url: str = NOMINATIM_URL,
    session: Any = None,
    user_agent: str = USER_AGENT,
    timeout: int = 30,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    polygon_threshold: float = 0.001,
) -> list[dict[str, Any]]:
    """Search Nominatim and return up to ``limit`` candidate areas for the user to choose from.

    Unlike :func:`geocode_area` (first hit only, raises on a miss), this returns every hit that has a
    bounding box, in Nominatim's ranking order, and an empty list on a miss. Outlines are simplified
    server-side (``polygon_threshold``, in degrees) so they stay small enough to preview and to store
    on a recorded action.

    Args:
        q: Free-text place query.
        limit: Maximum number of candidates (Nominatim caps this at 40).
        base_url: Nominatim search endpoint.
        session: An object exposing ``get(url, params, headers, timeout)``. Defaults to :mod:`requests`.
        user_agent: ``User-Agent`` header value (required by Nominatim policy).
        timeout: Per-request timeout, seconds.
        retries: Number of retries on transient status codes.
        sleep: Sleep function used between retries (injectable for tests).
        polygon_threshold: Outline simplification tolerance, degrees (``0.001`` is roughly 100 m).

    Returns:
        ``[{"display_name", "type", "bbox": (west, south, east, north), "polygon": [(lat, lon), ...] | None}]``.
    """
    http = session or requests
    params = {
        "q": q,
        "format": "json",
        "polygon_geojson": 1,
        "polygon_threshold": polygon_threshold,
        "limit": limit,
    }
    response = _with_retry(
        lambda: http.get(base_url, params=params, headers={"User-Agent": user_agent}, timeout=timeout),
        retries=retries,
        sleep=sleep,
    )
    candidates = []
    for hit in response.json():
        if "boundingbox" not in hit:
            continue
        south, north, west, east = (float(v) for v in hit["boundingbox"])
        geojson = hit.get("geojson")
        candidates.append(
            {
                "display_name": hit.get("display_name", ""),
                "type": hit.get("type", ""),
                "bbox": (west, south, east, north),
                "polygon": _geojson_to_latlon(geojson) if geojson else None,
            }
        )
    return candidates


def resolve_area(
    area: str | Sequence[float],
    *,
    buffer_m: float = 0.0,
    session: Any = None,
    user_agent: str = USER_AGENT,
    timeout: int = 30,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Resolve a user-supplied area to a bbox (and polygon, for places).

    Args:
        area: A place string, a ``(lat, lon)`` point, or a
            ``(west, south, east, north)`` bbox.
        buffer_m: Buffer in metres applied when ``area`` is a point.
        session: HTTP session forwarded to :func:`geocode_area` for places.
        user_agent: ``User-Agent`` forwarded to :func:`geocode_area`.
        timeout: Timeout forwarded to :func:`geocode_area`.
        retries: Retries forwarded to :func:`geocode_area`.
        sleep: Sleep function forwarded to :func:`geocode_area`.

    Returns:
        ``{"bbox": (west, south, east, north), "polygon": [...] | None}``.

    Raises:
        ValueError: If ``area`` is not a string, 2-tuple, or 4-tuple.
    """
    if isinstance(area, str):
        return geocode_area(area, session=session, user_agent=user_agent, timeout=timeout, retries=retries, sleep=sleep)
    if isinstance(area, Sequence) and not isinstance(area, str | bytes):
        values = list(area)
        if len(values) == 4:
            return {"bbox": tuple(float(v) for v in values), "polygon": None}
        if len(values) == 2:
            lat, lon = float(values[0]), float(values[1])
            return {"bbox": point_buffer_bbox(lat, lon, buffer_m), "polygon": None}
    raise ValueError("area must be a place string, a (lat, lon) point, or a (west, south, east, north) bbox")


def fetch_network_elements(
    area: str | Sequence[float],
    *,
    buffer_m: float = 0.0,
    network_type: NetworkTypes = "drive",
    endpoint: str = OVERPASS_URL,
    session: Any = None,
    user_agent: str = USER_AGENT,
    timeout: int = 180,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    """Resolve ``area``, fetch its OSM ``highway`` network, and parse it.

    Args:
        area: Place string, ``(lat, lon)`` point, or
            ``(west, south, east, north)`` bbox.
        buffer_m: Buffer in metres applied when ``area`` is a point.
        network_type: One of ``drive``/``walk``/``bike``/``all``.
        endpoint: Overpass API endpoint URL.
        session: HTTP session (injectable for tests). Defaults to ``requests``.
        user_agent: ``User-Agent`` header value.
        timeout: Per-request timeout, seconds.
        retries: Retries on transient status codes.
        sleep: Sleep function used between retries.

    Returns:
        ``(nodes, ways)`` ready for :func:`netstead.osm.convert.build_node_link_tables`.
    """
    resolved = resolve_area(
        area, buffer_m=buffer_m, session=session, user_agent=user_agent, timeout=timeout, retries=retries, sleep=sleep
    )
    overpass_query = build_overpass_query(
        bbox=resolved["bbox"], polygon=resolved["polygon"], network_type=network_type, timeout=timeout
    )
    elements = fetch_osm(
        overpass_query,
        endpoint=endpoint,
        session=session,
        user_agent=user_agent,
        timeout=timeout,
        retries=retries,
        sleep=sleep,
    )
    return parse_overpass_elements(elements)
