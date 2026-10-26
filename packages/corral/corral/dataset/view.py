"""Generic geographic scopes — bbox / polygon / geometry-buffer (task 2.8 / issue #67).

Implements the three spatial scope constructors the architecture
(``docs/architecture.md`` §6.2) places in :mod:`corral.dataset` —
the engine-agnostic, network-unaware ones. Network-aware scopes
(BFS, network-distance buffers) live in :mod:`netstead.scope`
and compose on top of these by feeding their seed geometries through
:func:`from_polygon` / :func:`from_geometry_buffer`.

Cross-engine strategy is **ibis-first** (architecture §6.1): every
predicate is built as an ibis expression and pushed to duckdb as a
single SQL ``WHERE`` clause via the duckdb **spatial extension**
(builtin-UDF wrappers below). For partitioned parquet sources the
spatial predicate stays inside the ``read_parquet`` plan so duckdb
prunes unrelated partitions — locked in by
``tests/dataset/test_view.py::test_from_bbox_explain_pushdown``.

For non-ibis engines (parity is ``ibis + pandas`` per the task brief),
the table is wrapped as an ``ibis.memtable`` via
:func:`corral.validation._ibis.to_ibis`, filtered, materialized via
**pyarrow**, and rebuilt on the source engine's ``from_records``
primitive. No ``import pandas`` lives in this module — same rule as
the validators.

``shapely`` is an **optional** dependency. :func:`from_bbox` is pure
numeric and does not require shapely; :func:`from_polygon` and
:func:`from_geometry_buffer` raise
:class:`~corral.engines.errors.EngineNotAvailableError` with the
install hint when called without it. WKT strings are accepted as the
shapely-free escape hatch.

Cross-references: architecture §6.1/§6.2, validators in
:mod:`corral.validation.schema_check` (same pattern),
:mod:`netstead.scope`, issue https://github.com/e-lo/netstead/issues/67.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import ibis
import ibis.expr.datatypes as dt
from ibis import udf

from corral.engines.errors import EngineNotAvailableError
from corral.engines.ibis_engine import backend_lock
from corral.validation._ibis import to_ibis

from .table import Table

if TYPE_CHECKING:  # pragma: no cover - typing only
    import ibis.expr.types as ir
    import shapely.geometry.base as _shapely_base


__all__ = ["from_bbox", "from_geometry_buffer", "from_polygon"]


# ---------------------------------------------------------------------------
# DuckDB spatial extension — ibis builtin UDF wrappers
# ---------------------------------------------------------------------------
#
# Builtin UDFs compile to the named SQL function (case-insensitive at
# duckdb) with no pandas round-trip. Geometry slots are typed
# ``binary`` because that is duckdb spatial's wire type for GEOMETRY
# across the ibis ↔ duckdb boundary; the value stays opaque (WKB).


@udf.scalar.builtin(name="ST_GeomFromText")
def _st_geom_from_text(wkt: str) -> dt.binary:  # type: ignore[empty-body]
    """Parse WKT/EWKT into a duckdb GEOMETRY."""


@udf.scalar.builtin(name="ST_Intersects")
def _st_intersects(left: dt.binary, right: dt.binary) -> bool:  # type: ignore[empty-body]
    """Boolean spatial intersect predicate."""


@udf.scalar.builtin(name="ST_MakeEnvelope")
def _st_make_envelope(minx: float, miny: float, maxx: float, maxy: float) -> dt.binary:  # type: ignore[empty-body]
    """Axis-aligned GEOMETRY envelope from corners."""


@udf.scalar.builtin(name="ST_Buffer")
def _st_buffer(geom: dt.binary, distance: float) -> dt.binary:  # type: ignore[empty-body]
    """Buffer GEOMETRY by ``distance`` in the geometry's CRS units."""


@udf.scalar.builtin(name="ST_GeomFromWKB")
def _st_geom_from_wkb(wkb: dt.binary) -> dt.binary:  # type: ignore[empty-body]
    """Parse WKB bytes into a duckdb GEOMETRY."""


def geom_from_any(col: Any) -> Any:
    """Decode a stored geometry column (WKT ``str`` **or** WKB ``bytes``) to GEOMETRY.

    The binary-geometry migration (geometry ADR) makes WKB the canonical stored
    encoding, but CSV-sourced tables still hold WKT. Dispatch on the ibis column
    dtype — ``ST_GeomFromWKB`` for a binary column, ``ST_GeomFromText`` for a
    string one — so the spatial scopes work on either without the caller
    converting first. A literal WKT the caller passes in still uses
    :func:`_st_geom_from_text` directly (it is always text).
    """
    if col.type().is_binary():
        return _st_geom_from_wkb(col)
    return _st_geom_from_text(col)


# Sentinel attribute name used to cache "spatial extension is loaded"
# on the duckdb backend object. Avoids re-installing on every call.
_SPATIAL_LOADED_ATTR = "_corral_spatial_loaded"


def _ensure_spatial(backend: Any) -> None:
    """Lazily install + load the duckdb spatial extension on ``backend`` (cached)."""
    if getattr(backend, _SPATIAL_LOADED_ATTR, False):
        return
    raw = getattr(backend, "con", None)
    if raw is None:  # pragma: no cover - protective
        raise EngineNotAvailableError(
            "corral.dataset.view: spatial scopes require an ibis duckdb backend; "
            f"got {type(backend).__name__} which has no underlying duckdb connection."
        )
    try:
        with backend_lock(backend):  # ``raw`` is the bare duckdb connection: the method locks miss it
            raw.install_extension("spatial")
            backend.load_extension("spatial")
    except Exception as exc:
        raise EngineNotAvailableError(
            "corral.dataset.view: failed to load the duckdb 'spatial' extension. "
            "On an offline machine, prime the extension cache with "
            '`duckdb -c "INSTALL spatial;"`, or pass an IbisEngine whose backend '
            "already has it loaded."
        ) from exc
    setattr(backend, _SPATIAL_LOADED_ATTR, True)


# ---------------------------------------------------------------------------
# Optional-dep shapely gate
# ---------------------------------------------------------------------------


def _require_shapely() -> None:
    """Raise :class:`EngineNotAvailableError` with an install hint if shapely is missing."""
    try:
        import shapely  # noqa: F401 - optional gate
    except ImportError as exc:
        raise EngineNotAvailableError(
            "corral.dataset.view: from_polygon / from_geometry_buffer require shapely. "
            "Install with `pip install shapely>=2.0` or `pip install netstead[clean]`."
        ) from exc


def _geometry_to_wkt(geom: Any) -> str:
    """Coerce a shapely geometry / ``.wkt``-bearing object / bare WKT string to WKT."""
    if isinstance(geom, str):
        return geom
    wkt = getattr(geom, "wkt", None)
    if isinstance(wkt, str):
        return wkt
    raise TypeError(f"corral.dataset.view: expected a shapely geometry or WKT string; got {type(geom).__name__}.")


# ---------------------------------------------------------------------------
# Filter plumbing — keep the ibis predicate near where it is built
# ---------------------------------------------------------------------------


def _ibis_backend_of(table: Table) -> Any | None:
    """Return the source ibis duckdb backend of ``table`` if its expr is ibis-native."""
    expr = table.expr
    if not isinstance(expr, ibis.expr.types.Table):
        return None
    # ``_find_backend`` is ibis's underscored-but-stable accessor — it
    # raises if the expression has no backend (e.g. a fresh memtable).
    # In our path the expr came from an IbisEngine read so a backend
    # is always present.
    try:
        return expr._find_backend()
    except Exception:  # pragma: no cover - defensive
        return None


def _apply_predicate(
    table: Table,
    predicate_builder: Any,
    *,
    geometry_column: str,
) -> Table:
    """Apply an ibis spatial predicate; stay lazy on ibis, pyarrow round-trip elsewhere.

    Tables whose expression is an ibis Table on a duckdb backend keep
    the filter lazy on that backend. Non-ibis engines route through an
    ``ibis.memtable`` (default duckdb backend), materialize via
    **pyarrow**, and rebuild on the source engine via
    :meth:`Engine.from_records`. Missing geometry column is a no-op so
    :meth:`Package.scope` works on mixed-geometry packages.
    """
    backend = _ibis_backend_of(table)
    if backend is not None:
        if geometry_column not in table.expr.columns:
            return table._derived(table.expr)
        _ensure_spatial(backend)
        return table._derived(table.expr.filter(predicate_builder(table.expr)))

    ibis_table = to_ibis(table.expr)
    if geometry_column not in ibis_table.columns:
        return table._derived(table.expr)
    # ``use_default=True`` resolves to the default duckdb backend that
    # ``ibis.memtable`` lazily binds to on first execute — exactly the
    # one we need to load the spatial extension onto.
    _ensure_spatial(ibis_table._find_backend(use_default=True))
    filtered = ibis_table.filter(predicate_builder(ibis_table))
    arrow = filtered.to_pyarrow()
    columnar: dict[str, list[Any]] = {name: arrow.column(name).to_pylist() for name in arrow.column_names}
    new_expr = table.engine.from_records(columnar, schema=table.schema)
    return table._derived(new_expr)


# ---------------------------------------------------------------------------
# Public spatial scope constructors
# ---------------------------------------------------------------------------


def from_bbox(
    table: Table,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    *,
    geometry_column: str = "geometry",
) -> Table:
    """Return a scoped :class:`Table` whose rows intersect the bounding box.

    Compiles to ``ST_Intersects(ST_GeomFromText(geom), ST_MakeEnvelope(...))`` —
    a single duckdb ``WHERE`` clause that pushes down into ``read_parquet``
    for Hive-partitioned sources (locked by the partition-pruning snapshot
    test). A missing ``geometry_column`` is a silent no-op so
    :meth:`Package.scope` is safe on mixed-geometry packages.

    Args:
        table: Source lazy :class:`Table`.
        minx: Western bbox bound (same CRS as the WKT column).
        miny: Southern bound.
        maxx: Eastern bound.
        maxy: Northern bound.
        geometry_column: WKT/WKB string column name. Default ``"geometry"``.

    Returns:
        A new :class:`Table` carrying the spatial filter (lazy on ibis;
        eager pyarrow round-trip on pandas).

    Raises:
        EngineNotAvailableError: If the duckdb spatial extension cannot
            be loaded.

    Examples:
        Scope the bundled sample ``venue`` table to a small bbox around
        Portland (picks out one of the four bookstore POINTs)::

            >>> from corral.dataset import Package
            >>> from corral.dataset.view import from_bbox
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.fixtures import sample
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["venue"],
            ... )
            >>> scoped = from_bbox(pkg["venue"], -123.0, 45.0, -122.0, 46.0)
            >>> scoped.count() < pkg["venue"].count()
            True
    """

    def _build(expr: ir.Table) -> Any:
        return _st_intersects(
            geom_from_any(expr[geometry_column]),
            _st_make_envelope(minx, miny, maxx, maxy),
        )

    return _apply_predicate(table, _build, geometry_column=geometry_column)


def from_polygon(
    table: Table,
    polygon: _shapely_base.BaseGeometry,
    *,
    geometry_column: str = "geometry",
) -> Table:
    """Return a scoped :class:`Table` whose rows intersect ``polygon``.

    Serializes ``polygon`` to WKT once on the Python side; the duckdb
    spatial extension does the geometric test. Same lazy / push-down
    behavior as :func:`from_bbox`.

    Args:
        table: Source lazy :class:`Table`.
        polygon: A shapely (multi-)polygon, any ``.wkt``-bearing object,
            or a bare WKT string.
        geometry_column: Geometry column name (default ``"geometry"``).

    Returns:
        A new :class:`Table` carrying the spatial filter.

    Raises:
        EngineNotAvailableError: If shapely is not installed (only for
            non-string inputs) or the duckdb spatial extension cannot
            be loaded.

    Examples:
        WKT strings work without the shapely optional dep::

            >>> from corral.dataset import Package
            >>> from corral.dataset.view import from_polygon
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.fixtures import sample
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["venue"],
            ... )
            >>> wkt = (
            ...     "POLYGON ((-123.0 45.0, -122.0 45.0, "
            ...     "-122.0 46.0, -123.0 46.0, -123.0 45.0))"
            ... )
            >>> scoped = from_polygon(pkg["venue"], wkt)
            >>> scoped.count() < pkg["venue"].count()
            True
    """
    # Bare WKT strings skip the shapely gate; everything else requires
    # the optional dep (we use shapely only to canonicalise the input
    # type — the geometric work happens inside duckdb).
    if not isinstance(polygon, str):
        _require_shapely()
    wkt = _geometry_to_wkt(polygon)

    def _build(expr: ir.Table) -> Any:
        return _st_intersects(
            geom_from_any(expr[geometry_column]),
            _st_geom_from_text(wkt),
        )

    return _apply_predicate(table, _build, geometry_column=geometry_column)


def from_geometry_buffer(
    table: Table,
    geometry: _shapely_base.BaseGeometry,
    distance_m: float,
    *,
    geometry_column: str = "geometry",
) -> Table:
    """Return a scoped :class:`Table` within ``distance_m`` of ``geometry``.

    The buffer is computed inside duckdb via ``ST_Buffer`` — the Python
    side never materializes the buffered polygon. ``distance_m`` is in
    the **CRS units of the geometry column** (EPSG:4326 → degrees). The
    parameter name matches ``NetworkScope.buffer_spatial`` in
    :mod:`netstead.scope`; metric-aware projection belongs in that
    higher-level layer.

    Args:
        table: Source lazy :class:`Table`.
        geometry: Shapely geometry (point/line/polygon) or WKT string.
        distance_m: Buffer distance in the geometry column's CRS units.
        geometry_column: Geometry column name (default ``"geometry"``).

    Returns:
        A new :class:`Table` carrying the buffered-intersection filter.

    Raises:
        EngineNotAvailableError: If shapely is not installed (non-string
            inputs only) or the duckdb spatial extension cannot be loaded.

    Examples:
        Buffer around a point (small radius in degrees) — picks one of
        the four bookstore venues out of the bundled sample::

            >>> from corral.dataset import Package
            >>> from corral.dataset.view import from_geometry_buffer
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.fixtures import sample
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["venue"],
            ... )
            >>> scoped = from_geometry_buffer(
            ...     pkg["venue"],
            ...     "POINT (-122.6810 45.5230)",
            ...     0.5,
            ... )
            >>> scoped.count() < pkg["venue"].count()
            True
    """
    if not isinstance(geometry, str):
        _require_shapely()
    wkt = _geometry_to_wkt(geometry)

    def _build(expr: ir.Table) -> Any:
        return _st_intersects(
            geom_from_any(expr[geometry_column]),
            _st_buffer(_st_geom_from_text(wkt), float(distance_m)),
        )

    return _apply_predicate(table, _build, geometry_column=geometry_column)
