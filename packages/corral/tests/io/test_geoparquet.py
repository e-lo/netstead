"""ParquetAdapter GeoParquet output — `geo` file metadata for a WKB geometry column."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from corral.dataset.geometry import encode_wkb
from corral.dataset.table import Table
from corral.engines.ibis_engine import IbisEngine
from corral.io.parquet_adapter import ParquetAdapter

_ROWS = [
    {"link_id": 1, "geometry": "LINESTRING (0 0, 1 1)"},
    {"link_id": 2, "geometry": "LINESTRING (2 2, 5 3, 8 1)"},
]


def _wkb_links(engine: IbisEngine):
    expr = engine.from_records(_ROWS)
    return encode_wkb(Table(name="link", expr=expr, engine=engine), column="geometry")


def _read_geo(path: Path) -> dict:
    md = pq.read_schema(path).metadata or {}
    assert b"geo" in md, f"no geo metadata in {path.name}; keys={list(md)}"
    return json.loads(md[b"geo"])


def test_write_attaches_geo_metadata(tmp_path: Path):
    eng = IbisEngine()
    ParquetAdapter().write(_wkb_links(eng).expr, tmp_path / "link.parquet", eng)
    geo = _read_geo(tmp_path / "link.parquet")
    assert geo["version"].startswith("1.")
    assert geo["primary_column"] == "geometry"
    col = geo["columns"]["geometry"]
    assert col["encoding"] == "WKB"
    assert "geometry_types" in col  # required by the spec (may be [])


def test_geo_metadata_records_bbox(tmp_path: Path):
    eng = IbisEngine()
    ParquetAdapter().write(_wkb_links(eng).expr, tmp_path / "link.parquet", eng)
    bbox = _read_geo(tmp_path / "link.parquet")["columns"]["geometry"]["bbox"]
    # Union of (0 0,1 1) and (2 2,5 3,8 1): minx=0 miny=0 maxx=8 maxy=3
    assert bbox == [0.0, 0.0, 8.0, 3.0]


def test_crs_omitted_defaults_to_crs84(tmp_path: Path):
    eng = IbisEngine()
    ParquetAdapter().write(_wkb_links(eng).expr, tmp_path / "link.parquet", eng)
    col = _read_geo(tmp_path / "link.parquet")["columns"]["geometry"]
    # netstead is EPSG:4326 lon/lat == GeoParquet's default CRS84, encoded by omission.
    assert "crs" not in col


def test_non_geometry_table_stays_plain_parquet(tmp_path: Path):
    eng = IbisEngine()
    expr = eng.from_records([{"node_id": 1, "x": 1.0}, {"node_id": 2, "x": 2.0}])
    ParquetAdapter().write(expr, tmp_path / "node.parquet", eng)
    md = pq.read_schema(tmp_path / "node.parquet").metadata or {}
    assert b"geo" not in md


def test_roundtrip_reads_back_as_wkb_via_encode(tmp_path: Path):
    # duckdb+spatial reads GeoParquet back as a GEOMETRY column; encode_wkb
    # normalizes it to WKB bytes (the in-memory canon) — a GEOMETRY column
    # can't materialize to Arrow without geoarrow.
    eng = IbisEngine()
    adapter = ParquetAdapter()
    adapter.write(_wkb_links(eng).expr, tmp_path / "link.parquet", eng)
    back = adapter.read(tmp_path / "link.parquet", eng)
    assert back["geometry"].type().is_geospatial()
    normalized = encode_wkb(Table(name="link", expr=back, engine=eng), "geometry")
    cell = normalized.expr.to_pyarrow().to_pylist()[0]["geometry"]
    assert isinstance(cell, (bytes, bytearray))


def test_geopandas_reads_it_as_geometry(tmp_path: Path):
    gpd = pytest.importorskip("geopandas")
    eng = IbisEngine()
    ParquetAdapter().write(_wkb_links(eng).expr, tmp_path / "link.parquet", eng)
    gdf = gpd.read_parquet(tmp_path / "link.parquet")
    assert gdf.geometry.geom_type.tolist() == ["LineString", "LineString"]
