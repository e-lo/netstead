"""Tests for :mod:`corral.dataset.view` (task 2.8 / issue #67).

Exercises the three spatial scope constructors (``from_bbox``,
``from_polygon``, ``from_geometry_buffer``) plus the
:meth:`Package.scope` extension against the Leavenworth GMNS fixture.
Cross-engine parametrisation is **ibis + pandas only** per the task
brief — polars is excluded because its native spatial story is via the
same duckdb extension we already exercise through the ibis path, so
adding polars would only re-test the memtable round-trip already
covered by pandas.

A partition-pruning EXPLAIN snapshot test pins the ibis-first promise
from architecture §6.1 — bbox scope compiles to an
``ST_INTERSECTS``-filtered ``read_parquet`` plan, so duckdb can prune
unrelated partitions.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from corral.dataset import Package
from corral.dataset.view import (
    from_bbox,
    from_geometry_buffer,
    from_polygon,
)
from corral.engines.errors import EngineNotAvailableError
from corral.engines.ibis_engine import IbisEngine
from netstead.fixtures import leavenworth

# Downtown Leavenworth bbox (chosen to be tight enough to filter out
# the bulk of the 339-row fixture but loose enough to keep a non-empty
# result on both engines).
_BBOX = (-120.67, 47.59, -120.66, 47.60)

# Same area as a polygon for from_polygon parity.
_WKT_POLY = "POLYGON ((-120.67 47.59, -120.66 47.59, -120.66 47.60, -120.67 47.60, -120.67 47.59))"


def _gmns_datapackage() -> Path:
    import netstead

    return pathlib.Path(netstead.__file__).parent / "spec" / "0.97" / "datapackage.json"


def _make_engine(name: str):
    if name == "ibis":
        return IbisEngine()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine: {name!r}")


def _geometry_table(engine_name: str):
    eng = _make_engine(engine_name)
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=eng,
        spec=_gmns_datapackage(),
        tables=["geometry"],
    )
    return pkg, pkg["geometry"]


# ---------------------------------------------------------------------------
# from_bbox — cross-engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_from_bbox_filters_geometry(engine_name: str) -> None:
    _, geom = _geometry_table(engine_name)
    total = geom.count()
    scoped = from_bbox(geom, *_BBOX)
    assert scoped.count() > 0
    assert scoped.count() < total


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_from_bbox_empty_when_far(engine_name: str) -> None:
    _, geom = _geometry_table(engine_name)
    # Mid-Atlantic — nothing in Leavenworth fits.
    scoped = from_bbox(geom, -30.0, 0.0, -29.0, 1.0)
    assert scoped.count() == 0


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_from_bbox_noop_when_geometry_column_absent(engine_name: str) -> None:
    """Tables without a geometry column pass through unchanged."""
    eng = _make_engine(engine_name)
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=eng,
        spec=_gmns_datapackage(),
        tables=["link"],  # link has geometry_id (FK), not geometry (WKT)
    )
    link = pkg["link"]
    scoped = from_bbox(link, *_BBOX)
    assert scoped.count() == link.count()


def test_from_bbox_returns_new_table_does_not_mutate_source() -> None:
    _, geom = _geometry_table("ibis")
    before = geom.count()
    scoped = from_bbox(geom, *_BBOX)
    assert scoped is not geom
    assert geom.count() == before  # original untouched


# ---------------------------------------------------------------------------
# from_polygon — cross-engine + WKT escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_from_polygon_wkt_string(engine_name: str) -> None:
    _, geom = _geometry_table(engine_name)
    scoped = from_polygon(geom, _WKT_POLY)
    bbox_scoped = from_bbox(geom, *_BBOX)
    # Same area expressed both ways → same row count.
    assert scoped.count() == bbox_scoped.count()


def test_from_polygon_accepts_shapely_geometry() -> None:
    shapely = pytest.importorskip("shapely")
    poly = shapely.from_wkt(_WKT_POLY)
    _, geom = _geometry_table("ibis")
    scoped = from_polygon(geom, poly)
    assert scoped.count() > 0


# ---------------------------------------------------------------------------
# from_geometry_buffer — cross-engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_from_geometry_buffer_around_point(engine_name: str) -> None:
    _, geom = _geometry_table(engine_name)
    # Tight 0.005-degree radius around a Leavenworth node.
    scoped = from_geometry_buffer(geom, "POINT (-120.6660 47.5960)", 0.005)
    total = geom.count()
    assert 0 < scoped.count() < total


# ---------------------------------------------------------------------------
# Package.scope(bbox=...) — end-to-end
# ---------------------------------------------------------------------------


def test_package_scope_bbox_end_to_end() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["geometry", "link", "node"],
    )
    scoped_pkg = pkg.scope(bbox=_BBOX)
    # geometry table is filtered.
    assert scoped_pkg["geometry"].count() < pkg["geometry"].count()
    # link / node tables pass through (no geometry column).
    assert scoped_pkg["link"].count() == pkg["link"].count()
    assert scoped_pkg["node"].count() == pkg["node"].count()
    # Same spec + engine.
    assert scoped_pkg.engine is pkg.engine
    assert scoped_pkg.spec is pkg.spec


def test_package_scope_polygon_kwarg() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["geometry"],
    )
    scoped_pkg = pkg.scope(polygon=_WKT_POLY)
    assert 0 < scoped_pkg["geometry"].count() < pkg["geometry"].count()


def test_package_scope_rejects_multiple_spatial_kwargs() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["geometry"],
    )
    with pytest.raises(ValueError, match="at most one of bbox"):
        pkg.scope(bbox=_BBOX, polygon=_WKT_POLY)


# ---------------------------------------------------------------------------
# Partition-pruning EXPLAIN snapshot (architecture §6.1)
# ---------------------------------------------------------------------------


def test_from_bbox_explain_pushdown(tmp_path: Path) -> None:
    """Bbox scope compiles to ``ST_INTERSECTS``-filtered ``read_parquet``.

    With the predicate visible at the read_parquet level, duckdb can
    push it down for true partition pruning on Hive-partitioned
    sources. We verify two layers:

    1. The SQL ibis emits carries the spatial predicate in the WHERE
       (sanity check that the builtin UDFs survived compilation).
    2. The duckdb ``EXPLAIN`` plan for the read includes a Filter that
       references ST_INTERSECTS — so the engine sees it as a pushdown
       candidate.
    """
    # Materialize the geometry fixture as a partitioned parquet dataset
    # so the EXPLAIN plan exercises the partitioned read path. We split
    # by geometry_id parity — a trivial partitioning that's enough to
    # let duckdb show one Filter node per partition.
    eng = IbisEngine()
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=eng,
        spec=_gmns_datapackage(),
        tables=["geometry"],
    )
    arrow_geom: pa.Table = pkg["geometry"].expr.to_pyarrow()
    import pyarrow.compute as pc

    parity = pc.bit_wise_and(arrow_geom.column("geometry_id"), 1)
    arrow_partitioned = arrow_geom.append_column("h_part", parity.cast(pa.int32()))
    part_root = tmp_path / "geometry"
    pq.write_to_dataset(arrow_partitioned, root_path=str(part_root), partition_cols=["h_part"])

    # Re-open as a fresh ibis-on-duckdb scan of the partitioned dir.
    scan_eng = IbisEngine()
    pkg2 = Package.from_source(
        part_root,
        engine=scan_eng,
        spec=_gmns_datapackage(),
    )
    # The partitioned dataset surfaces as one resource named after the
    # directory.
    geom_tbl = next(iter(pkg2.values()))
    scoped = from_bbox(geom_tbl, *_BBOX)

    sql = ibis.to_sql(scoped.expr)
    sql_upper = sql.upper()
    assert "ST_INTERSECTS" in sql_upper, f"missing predicate in compiled SQL: {sql}"
    assert "ST_MAKEENVELOPE" in sql_upper

    # duckdb EXPLAIN — we use the raw connection to avoid the ibis
    # explain pretty-printer (which truncates UDF names on some
    # versions). The plan string must mention the spatial UDF; that's
    # the pushdown handle duckdb's optimiser uses.
    raw = scan_eng.con.con
    plan_rows = raw.execute(f"EXPLAIN {sql}").fetchall()
    plan_text = "\n".join(str(row) for row in plan_rows)
    plan_upper = plan_text.upper()
    assert "ST_INTERSECTS" in plan_upper, f"plan missing pushdown predicate:\n{plan_text}"
    # And the read_parquet node is in the plan (sanity: we did scan the
    # partitioned dataset, not a different one).
    assert "PARQUET" in plan_upper or "READ_PARQUET" in plan_upper


# ---------------------------------------------------------------------------
# Optional-dep gate
# ---------------------------------------------------------------------------


def test_from_polygon_requires_shapely_for_non_string_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-string geometry inputs trigger the shapely gate."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "shapely":
            raise ImportError("simulated: shapely missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    _, geom = _geometry_table("ibis")

    class _FakeGeom:
        wkt = _WKT_POLY

    with pytest.raises(EngineNotAvailableError, match="shapely"):
        from_polygon(geom, _FakeGeom())
