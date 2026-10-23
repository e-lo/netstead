"""WKT <-> WKB geometry re-encoding on a :class:`~corral.dataset.table.Table`.

Canonical in-memory geometry is **WKB bytes** (geometry ADR, 2026-10-01):
spatial ops decode it on the fly, Parquet stores it directly (GeoParquet), and a
WKB blob column round-trips through Arrow cleanly (unlike duckdb's ``GEOMETRY``
type). This module owns the *mechanics* of that encoding; the domain layer
(netstead) decides which column is geometry and in which CRS, and calls these.

- :func:`encode_wkb` — WKT *string* column → WKB bytes, at ingest.
- :func:`decode_wkt` — WKB bytes column → WKT string, for CSV export.

Both compile to duckdb spatial builtins through ibis (``ST_GeomFromText`` /
``ST_AsWKB`` / ``ST_GeomFromWKB`` / ``ST_AsText``) — no shapely, no geopandas, no
pandas round-trip, and the op stays lazy on the duckdb backend. Each is
**idempotent by dtype**: :func:`encode_wkb` skips a column that is already binary
and :func:`decode_wkt` skips one that is already text, so loading GeoParquet
(already WKB) and CSV (WKT) both converge on WKB without the caller tracking
provenance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt
from ibis import udf

# Reuse the cached spatial-extension loader and backend probe from the sibling
# scope module rather than duplicating them; ST_GeomFromText is also defined
# there, so we import it instead of re-registering the same builtin name.
from .view import _ensure_spatial, _ibis_backend_of
from .view import _st_geom_from_text as _geom_from_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .table import Table

__all__ = ["WKB", "WKT", "decode_wkt", "encode_wkb", "geoparquet_bounds"]

#: Canonical values for the GMNS config ``geometry_field_format`` field.
WKT = "WKT"
WKB = "WKB"


@udf.scalar.builtin(name="ST_AsWKB")
def _as_wkb(geom: dt.binary) -> dt.binary:  # type: ignore[empty-body]
    """Serialize a duckdb GEOMETRY (passed as its binary handle) to WKB bytes."""


@udf.scalar.builtin(name="ST_AsWKB")
def _as_wkb_geom(geom: dt.geometry) -> dt.binary:  # type: ignore[empty-body]
    """ST_AsWKB for a column already typed GEOMETRY (e.g. read back from GeoParquet)."""


@udf.scalar.builtin(name="ST_GeomFromWKB")
def _geom_from_wkb(wkb: dt.binary) -> dt.binary:  # type: ignore[empty-body]
    """Parse WKB bytes into a duckdb GEOMETRY."""


@udf.scalar.builtin(name="ST_AsText")
def _as_text(geom: dt.binary) -> str:  # type: ignore[empty-body]
    """Render a duckdb GEOMETRY as WKT text."""


@udf.scalar.builtin(name="ST_XMin")
def _st_xmin(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Minimum X of a GEOMETRY's envelope."""


@udf.scalar.builtin(name="ST_YMin")
def _st_ymin(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Minimum Y of a GEOMETRY's envelope."""


@udf.scalar.builtin(name="ST_XMax")
def _st_xmax(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Maximum X of a GEOMETRY's envelope."""


@udf.scalar.builtin(name="ST_YMax")
def _st_ymax(geom: dt.binary) -> float:  # type: ignore[empty-body]
    """Maximum Y of a GEOMETRY's envelope."""


def geoparquet_bounds(expr, column: str = "geometry") -> tuple[float, float, float, float] | None:
    """Return ``(minx, miny, maxx, maxy)`` over a WKB geometry column, or ``None``.

    Computes the dataset bbox for the GeoParquet ``geo`` metadata as a single
    duckdb aggregate — ``MIN(ST_XMin(ST_GeomFromWKB(col)))`` and friends — so no
    geometry is decoded in Python. Operates on a raw ibis expr (the parquet
    adapter holds an expr, not a Table). Returns ``None`` when the column is
    absent/non-binary, the table is empty, or the spatial extension can't load.
    """
    import ibis

    if not isinstance(expr, ibis.expr.types.Table) or column not in expr.columns:
        return None
    if not expr[column].type().is_binary():
        return None
    try:
        _ensure_spatial(expr._find_backend(use_default=True))
        geom = _geom_from_wkb(expr[column])
        agg = expr.aggregate(
            minx=_st_xmin(geom).min(),
            miny=_st_ymin(geom).min(),
            maxx=_st_xmax(geom).max(),
            maxy=_st_ymax(geom).max(),
        )
        rows = agg.to_pyarrow().to_pylist()
    except Exception:  # pragma: no cover - defensive: no spatial / exotic backend
        return None
    if not rows:
        return None
    r = rows[0]
    vals = (r.get("minx"), r.get("miny"), r.get("maxx"), r.get("maxy"))
    if any(v is None for v in vals):
        return None
    return (float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]))


def _spatial_expr(table: Table):
    """Return ``table``'s ibis expr with the duckdb spatial extension loaded.

    Uses the table's own duckdb backend when the expr is ibis-native (the ingest
    path); otherwise binds through the default duckdb backend that ``ibis``
    memtables resolve to. Either way the geometry op stays a lazy projection.
    """
    from corral.validation._ibis import to_ibis

    backend = _ibis_backend_of(table)
    if backend is not None:
        _ensure_spatial(backend)
        return table.expr
    expr = to_ibis(table.expr)
    _ensure_spatial(expr._find_backend(use_default=True))
    return expr


def encode_wkb(table: Table, column: str = "geometry") -> Table:
    """Return ``table`` with ``column`` normalized to WKB bytes (the in-memory canon).

    Handles every encoding the column might arrive in:

    * **binary** (already WKB) → no-op (idempotent).
    * **string** (WKT, from CSV) → ``ST_AsWKB(ST_GeomFromText(col))``.
    * **geospatial** (duckdb ``GEOMETRY``, how it reads GeoParquet back when the
      spatial extension is loaded) → ``ST_AsWKB(col)``. This keeps the ADR's
      "WKB BLOB in memory" invariant — a ``GEOMETRY`` column can't materialize to
      Arrow without the geoarrow package, a BLOB round-trips cleanly.

    A column of any other dtype is left untouched. Lazy projection on the duckdb
    backend.
    """
    if column not in table.columns():
        return table
    col_type = table.expr[column].type()
    if col_type.is_binary():
        return table  # already WKB
    if not (col_type.is_string() or col_type.is_geospatial()):
        return table  # nothing we know how to encode
    expr = _spatial_expr(table)
    col = expr[column]
    encoded = _as_wkb_geom(col) if col_type.is_geospatial() else _as_wkb(_geom_from_text(col))
    return table._derived(expr.mutate(**{column: encoded}))


def decode_wkt(table: Table, column: str = "geometry") -> Table:
    """Return ``table`` with WKB bytes ``column`` rendered back to WKT text.

    The inverse of :func:`encode_wkb`, for CSV export. No-op when the column is
    absent or already a string. Lazy ``ST_AsText(ST_GeomFromWKB(column))``.
    """
    if column not in table.columns():
        return table
    if not table.expr[column].type().is_binary():
        return table  # already text → nothing to decode
    expr = _spatial_expr(table)
    return table._derived(expr.mutate(**{column: _as_text(_geom_from_wkb(expr[column]))}))
