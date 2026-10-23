"""Area resolution + Overture GeoParquet reading via DuckDB (the only I/O module).

This is the only module in :mod:`netstead.overture` that touches external data. It
turns a user-supplied area into an EPSG:4326 bbox, reads the Overture
**transportation** theme (``segment`` + ``connector`` types) straight from
GeoParquet with DuckDB, pushes the bbox + ``class`` predicates down into
``read_parquet`` (so only matching rows cross the wire), and returns the plain
``(segments, connectors)`` records :mod:`netstead.overture.convert` consumes.

Overture stores geometry as WKB in GeoParquet (surfaced by duckdb spatial either
as raw WKB or, for current releases, as a native GEOMETRY column); following the official DuckDB
recipe, geometry is turned into WKT / coordinates with the duckdb **spatial**
extension via ibis builtin-UDF wrappers (no raw SQL — the whole pipeline stays
engine-consistent). Remote ``s3://`` reads additionally need the **httpfs**
extension.

Conventions:
    * **bbox** is ``(west, south, east, north)`` in EPSG:4326.
    * a **point** is ``(lat, lon)`` (osmnx / OSM-source convention).
    * a **place** is a free-text string resolved through the OSM source's
      Nominatim geocoder (lazy import; needs the ``[osm]`` extra).

Attribution: Overture data is **ODbL** and must be attributed as
"© OpenStreetMap contributors, © Overture Maps Foundation" in derived products
(https://docs.overturemaps.org/attribution/). This library reads Overture data;
it does not vendor or redistribute it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb
import ibis.expr.datatypes as dt
from corral.engines import get_engine
from ibis import udf

from netstead.network_types import NetworkTypes

from . import attrs
from ._geo import parse_wkt_linestring

__all__ = [
    "OVERTURE_RELEASE",
    "count_segments",
    "fetch_network_elements",
    "latest_release",
    "overture_data_root",
    "point_buffer_bbox",
    "read_connectors",
    "read_segments",
    "resolve_area",
]

# Pinned, known-good Overture release (date-versioned). Bump this one constant to
# move to a newer release; pinning keeps builds reproducible (a re-run of the same
# area returns the same network). Override per-call with ``overture_release=``.
# Overture releases are retired from the public bucket after a few cycles, so this
# constant will eventually go stale; see s3://overturemaps-us-west-2/release/ for
# the current list, or `latest_release()` below.
OVERTURE_RELEASE = "2026-09-23.1"  # confirmed live on s3://overturemaps-us-west-2/release/ as of 2026-10-02

# Default AWS public bucket base (anonymous read). Override via ``data_root=``
# for the Azure mirror or a local snapshot.
_AWS_BASE = "s3://overturemaps-us-west-2/release"

# Overture transportation subtype we build road networks from (Phase 1).
_ROAD_SUBTYPE = "road"

# Metres per degree of latitude (spherical approximation).
_M_PER_DEG_LAT = 111320.0

# Tolerance (degrees, ~0.1 m) added around the selected segments' extent when
# reading their connectors, so float round-off at the extent edge can't drop one.
_EXTENT_EPS_DEG = 1e-6

# Overture segment property columns the converter / mapping may read. Only those
# actually present in the source are selected (so a trimmed fixture still works).
_SEGMENT_PROPERTY_COLUMNS = (
    "id",
    "subtype",
    "class",
    "subclass",
    "names",
    "routes",
    "speed_limits",
    "access_restrictions",
    "lanes",
    "road_flags",
    "connectors",
)


# ---------------------------------------------------------------------------
# DuckDB spatial extension — ibis builtin UDF wrappers (no pandas round-trip,
# no raw SQL; mirrors corral.dataset.view). Geometry is binary (WKB) across
# the ibis <-> duckdb boundary: older releases (and the committed test fixture)
# store ``geometry`` as raw WKB ``binary``, while current releases' GeoParquet
# metadata makes duckdb spatial surface it as a native ``GEOMETRY`` column, which
# :func:`_wkb` normalizes back to WKB so the rest of the read is encoding-agnostic.
# ---------------------------------------------------------------------------


@udf.scalar.builtin(name="ST_AsWKB")
def _st_as_wkb(geom: dt.geometry) -> dt.binary:  # type: ignore[empty-body]
    """Serialize a duckdb GEOMETRY to WKB."""


def _wkb(column: Any) -> Any:
    """Return ``column`` as WKB ``binary``, whether stored as WKB or as a native GEOMETRY."""
    return _st_as_wkb(column) if column.type().is_geospatial() else column


@udf.scalar.builtin(name="ST_GeomFromWKB")
def _st_geom_from_wkb(wkb: dt.binary) -> dt.binary:  # type: ignore[empty-body]
    """Parse WKB into a duckdb GEOMETRY."""


@udf.scalar.builtin(name="ST_AsText")
def _st_as_text(geom: dt.binary) -> str:  # type: ignore[empty-body]
    """Render a duckdb GEOMETRY as WKT."""


@udf.scalar.builtin(name="ST_X")
def _st_x(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Return the X (longitude) ordinate of a POINT GEOMETRY."""


@udf.scalar.builtin(name="ST_Y")
def _st_y(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Return the Y (latitude) ordinate of a POINT GEOMETRY."""


_SPATIAL_LOADED_ATTR = "_netstead_overture_spatial_loaded"
_HTTPFS_LOADED_ATTR = "_netstead_overture_httpfs_loaded"


def _ensure_extension(backend: Any, name: str, cached_attr: str) -> None:
    """Install + load a duckdb extension on ``backend`` once (cached, no SQL)."""
    if getattr(backend, cached_attr, False):
        return
    raw = getattr(backend, "con", None)
    if raw is None:  # pragma: no cover - protective
        raise RuntimeError(
            "netstead.overture.query requires an ibis duckdb backend; "
            f"got {type(backend).__name__} with no underlying duckdb connection."
        )
    raw.install_extension(name)
    backend.load_extension(name)
    setattr(backend, cached_attr, True)


# ---------------------------------------------------------------------------
# Area resolution
# ---------------------------------------------------------------------------


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


def resolve_area(area: str | Sequence[float], *, buffer_m: float = 0.0) -> tuple[float, float, float, float]:
    """Resolve a user-supplied area to a ``(west, south, east, north)`` bbox.

    A 4-sequence is taken as a bbox; a 2-sequence as a ``(lat, lon)`` point
    expanded by ``buffer_m``; a string is geocoded through the OSM source's
    Nominatim helper (lazy import — needs the ``[osm]`` extra). The bbox-only
    return keeps the Overture read bbox-driven (GeoParquet pushes a bbox
    predicate down; polygon clipping is not a Phase-1 feature).

    Args:
        area: A place string, a ``(lat, lon)`` point, or a
            ``(west, south, east, north)`` bbox.
        buffer_m: Buffer in metres applied when ``area`` is a point.

    Returns:
        The resolved ``(west, south, east, north)`` bbox.

    Raises:
        ValueError: If ``area`` is not a string, 2-tuple, or 4-tuple.

    Examples:
        >>> resolve_area((-71.1, 42.0, -71.0, 42.1))
        (-71.1, 42.0, -71.0, 42.1)
    """
    if isinstance(area, str):
        from netstead.osm import query as osm_query  # lazy: place geocoding needs [osm]

        return tuple(osm_query.geocode_area(area)["bbox"])  # type: ignore[return-value]
    if isinstance(area, Sequence) and not isinstance(area, str | bytes):
        values = [float(v) for v in area]
        if len(values) == 4:
            return (values[0], values[1], values[2], values[3])
        if len(values) == 2:
            return point_buffer_bbox(values[0], values[1], buffer_m)
    raise ValueError("area must be a place string, a (lat, lon) point, or a (west, south, east, north) bbox")


# ---------------------------------------------------------------------------
# Source URIs
# ---------------------------------------------------------------------------


def overture_data_root(overture_release: str, data_root: str | None) -> str:
    """Return the base URI for an Overture release (AWS bucket unless overridden).

    Args:
        overture_release: Date-versioned release string (e.g. ``"2024-11-13.0"``).
        data_root: Explicit base URI (Azure mirror / local snapshot dir). When
            given it is used verbatim and ``overture_release`` is ignored.

    Returns:
        The release base URI (no trailing slash).

    Examples:
        >>> overture_data_root("2024-11-13.0", None)
        's3://overturemaps-us-west-2/release/2024-11-13.0'
        >>> overture_data_root("ignored", "/snap")
        '/snap'
    """
    if data_root is not None:
        return data_root.rstrip("/")
    return f"{_AWS_BASE}/{overture_release}"


def latest_release(fs: Any = None) -> str:
    """Return the newest release name on the public Overture bucket (anonymous S3 listing).

    Not called automatically by any read/build/count path -- :data:`OVERTURE_RELEASE`
    stays the pinned default for reproducibility. Call this yourself (e.g. from a
    REPL) to check whether that pin needs bumping.

    Args:
        fs: An ``fsspec`` filesystem to list with (mainly for tests); defaults to
            an anonymous ``s3`` filesystem.

    Returns:
        The lexicographically-last release directory name, e.g. ``"2026-09-23.1"``.
    """
    if fs is None:
        import fsspec

        fs = fsspec.filesystem("s3", anon=True)
    releases = sorted(path.rsplit("/", 1)[-1] for path in fs.ls("overturemaps-us-west-2/release"))
    return releases[-1]


def _type_source(root: str, feature_type: str) -> str:
    """Return the read path for a transportation feature type under ``root``.

    Hive-partitioned remote layout (``theme=.../type=.../*``) for ``s3://`` roots;
    a flat ``<type>.parquet`` file for a local-snapshot / fixture root.
    """
    if root.startswith(("s3://", "az://", "abfss://", "http://", "https://")):
        return f"{root}/theme=transportation/type={feature_type}/*"
    return f"{root}/{feature_type}.parquet"


def _prepare_backend(engine: Any, source: str) -> Any:
    """Load the duckdb extensions the read needs and return the engine's backend."""
    backend = engine.con
    _ensure_extension(backend, "spatial", _SPATIAL_LOADED_ATTR)
    if source.startswith(("s3://", "az://", "abfss://", "http://", "https://")):
        _ensure_extension(backend, "httpfs", _HTTPFS_LOADED_ATTR)
    return backend


def _run_or_raise_stale_release(fn: Any, *, overture_release: str, data_root: str | None) -> Any:
    """Call ``fn()``, translating a duckdb "no files" error into an actionable one.

    DuckDB raises a generic ``IOException`` ("No files found that match the
    pattern ...") whenever a Parquet glob matches nothing. The usual cause here
    is that ``overture_release`` has been retired from the public bucket (Overture
    only keeps a handful of recent releases around), so that's what we tell the
    caller rather than letting the raw path-mismatch message stand alone.
    """
    try:
        return fn()
    except duckdb.IOException as exc:
        if "no files found" not in str(exc).lower():
            raise
        raise duckdb.IOException(
            f"No Overture data found for release {overture_release!r} "
            f"(data_root={data_root!r}); this Overture release may have been "
            "retired; set overture.release (or pass overture_release=) to a "
            "current one from s3://overturemaps-us-west-2/release/"
        ) from exc


def _bbox_intersects(table: Any, bbox: tuple[float, float, float, float]) -> Any:
    """Build the ibis predicate: row ``bbox`` struct intersects ``bbox``."""
    west, south, east, north = bbox
    return (
        (table.bbox.xmin <= east) & (table.bbox.xmax >= west) & (table.bbox.ymin <= north) & (table.bbox.ymax >= south)
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _matching_segments(
    bbox: tuple[float, float, float, float],
    network_type: NetworkTypes,
    overture_release: str,
    data_root: str | None,
    engine: Any,
) -> Any:
    """The lazy ibis table of road segments in ``bbox`` allowed for ``network_type`` (bbox + class pushed down)."""
    source = _type_source(overture_data_root(overture_release, data_root), "segment")
    _prepare_backend(engine, source)
    table = _run_or_raise_stale_release(
        lambda: engine.read_parquet(source, hive_partitioning=source.endswith("/*")),
        overture_release=overture_release,
        data_root=data_root,
    )
    available = set(table.columns)
    predicate = _bbox_intersects(table, bbox)
    if "subtype" in available:
        predicate = predicate & (table.subtype == _ROAD_SUBTYPE)
    allowed = attrs.allowed_classes(network_type)
    if allowed and "class" in available:
        predicate = predicate & table["class"].isin(sorted(allowed))
    return table.filter(predicate)


def count_segments(
    bbox: tuple[float, float, float, float],
    *,
    network_type: NetworkTypes = "drive",
    overture_release: str = OVERTURE_RELEASE,
    data_root: str | None = None,
    engine: Any = None,
) -> int:
    """Count the road segments :func:`read_segments` would return, without reading their geometry.

    The same bbox + ``class`` predicates as the read, so it is the cheap pre-query the workbench's
    build estimate sizes a request with.

    Args:
        bbox: ``(west, south, east, north)`` in EPSG:4326.
        network_type: One of ``drive``/``walk``/``bike``/``all``.
        overture_release: Pinned release string (ignored when ``data_root`` set).
        data_root: Override base URI (Azure mirror / local snapshot dir).
        engine: Compute engine (default: corral ibis/duckdb).

    Returns:
        The number of matching segments.
    """
    matching = _matching_segments(bbox, network_type, overture_release, data_root, engine or get_engine())
    return int(matching.count().execute())


def read_segments(
    bbox: tuple[float, float, float, float],
    *,
    network_type: NetworkTypes = "drive",
    overture_release: str = OVERTURE_RELEASE,
    data_root: str | None = None,
    engine: Any = None,
    extra_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read in-bbox Overture road segments as plain records.

    The bbox + ``class`` predicates are pushed into ``read_parquet`` so unrelated
    rows / partitions are pruned. Geometry is returned as a WKT ``LINESTRING`` in
    the ``geometry`` key; nested properties (``names``, ``speed_limits``,
    ``connectors``, ...) come back as nested Python dicts / lists.

    Args:
        bbox: ``(west, south, east, north)`` in EPSG:4326.
        network_type: One of ``drive``/``walk``/``bike``/``all``; selects the
            allowed ``class`` list (``all`` applies no class filter).
        overture_release: Pinned release string (ignored when ``data_root`` set).
        data_root: Override base URI (Azure mirror / local snapshot dir).
        engine: Compute engine (default: corral ibis/duckdb).
        extra_tags: Extra dotted Overture property paths to carry; their
            top-level columns are added to the read projection.

    Returns:
        A list of segment records (``geometry`` as WKT + nested properties).
    """
    filtered = _matching_segments(bbox, network_type, overture_release, data_root, engine or get_engine())
    available = set(filtered.columns)

    wanted = [c for c in _SEGMENT_PROPERTY_COLUMNS if c in available]
    for path in extra_tags or []:
        top = path.split(".")[0]
        if top in available and top not in wanted:
            wanted.append(top)
    projected = filtered.select(*wanted, geometry=_st_as_text(_st_geom_from_wkb(_wkb(filtered.geometry))))
    return projected.to_pyarrow().to_pylist()


def read_connectors(
    segments: Sequence[Mapping[str, Any]],
    *,
    overture_release: str = OVERTURE_RELEASE,
    data_root: str | None = None,
    engine: Any = None,
) -> dict[str, tuple[float, float]]:
    """Read the Overture connectors ``segments`` reference, as a ``{connector_id: (lon, lat)}`` map.

    Every connector lies on the segment geometry that references it, so the read
    is bounded by the selected segments' own extent (not the request bbox) and
    is complete even when a segment crosses the bbox edge and ends far outside
    it. The extent is pushed down as a bbox predicate (row-group pruning); a
    global ``id IN (...)`` scan was measured at ~400 s on S3 versus ~3 s for
    this extent read on an XS area.

    Args:
        segments: Records from :func:`read_segments` (``geometry`` as WKT plus
            the ``connectors`` list).
        overture_release: Pinned release string (ignored when ``data_root`` set).
        data_root: Override base URI (Azure mirror / local snapshot dir).
        engine: Compute engine (default: corral ibis/duckdb).

    Returns:
        ``{connector_id: (lon, lat)}`` for every referenced connector found.
    """
    wanted = {c["connector_id"] for seg in segments for c in seg.get("connectors") or []}
    if not wanted:
        return {}
    engine = engine or get_engine()
    source = _type_source(overture_data_root(overture_release, data_root), "connector")
    _prepare_backend(engine, source)

    table = _run_or_raise_stale_release(
        lambda: engine.read_parquet(source, hive_partitioning=source.endswith("/*")),
        overture_release=overture_release,
        data_root=data_root,
    )
    filtered = table.filter(_bbox_intersects(table, _segments_extent(segments)))
    projected = filtered.select(
        "id",
        x=_st_x(_st_geom_from_wkb(_wkb(filtered.geometry))),
        y=_st_y(_st_geom_from_wkb(_wkb(filtered.geometry))),
    )
    rows = projected.to_pyarrow().to_pylist()
    return {row["id"]: (row["x"], row["y"]) for row in rows if row["id"] in wanted}


def _segments_extent(segments: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float]:
    """The ``(west, south, east, north)`` extent of the segments' WKT geometries, padded by a float tolerance."""
    coords = [xy for seg in segments for xy in parse_wkt_linestring(str(seg["geometry"]))]
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    eps = _EXTENT_EPS_DEG
    return (min(xs) - eps, min(ys) - eps, max(xs) + eps, max(ys) + eps)


def fetch_network_elements(
    area: str | Sequence[float],
    *,
    buffer_m: float = 0.0,
    network_type: NetworkTypes = "drive",
    overture_release: str = OVERTURE_RELEASE,
    data_root: str | None = None,
    engine: Any = None,
    extra_tags: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
    """Resolve ``area`` and read its Overture segments + connectors.

    Args:
        area: Place string, ``(lat, lon)`` point, or ``(west, south, east,
            north)`` bbox.
        buffer_m: Buffer in metres applied when ``area`` is a point.
        network_type: One of ``drive``/``walk``/``bike``/``all``.
        overture_release: Pinned release string (ignored when ``data_root`` set).
        data_root: Override base URI (Azure mirror / local snapshot dir).
        engine: Compute engine (default: corral ibis/duckdb).
        extra_tags: Extra dotted Overture property paths to carry onto links.

    Returns:
        ``(segments, connectors)`` ready for
        :func:`netstead.overture.convert.build_node_link_tables`.
    """
    engine = engine or get_engine()
    bbox = resolve_area(area, buffer_m=buffer_m)
    segments = read_segments(
        bbox,
        network_type=network_type,
        overture_release=overture_release,
        data_root=data_root,
        engine=engine,
        extra_tags=extra_tags,
    )
    connectors = read_connectors(
        segments,
        overture_release=overture_release,
        data_root=data_root,
        engine=engine,
    )
    return segments, connectors
