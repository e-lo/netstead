"""Tests for netstead.overture.query reading the committed local GeoParquet fixture.

These exercise the real DuckDB read path (bbox + class pushdown, WKB->WKT via the
spatial extension) against a tiny Overture-shaped fixture — no network / S3. The
fixture is built by ``tests/fixtures/overture/build_fixture.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead.overture import query
from netstead.overture.layout import is_local_snapshot

FIXTURE_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "overture")
WORLD_BBOX = (-0.5, -0.5, 0.5, 0.5)


@pytest.fixture
def engine():
    eng = IbisEngine()
    yield eng
    eng.close()


class TestResolveArea:
    def test_bbox_passthrough(self):
        assert query.resolve_area((-71.1, 42.0, -71.0, 42.1)) == (-71.1, 42.0, -71.0, 42.1)

    def test_point_buffer(self):
        _w, _s, e, n = query.resolve_area((0.0, 0.0), buffer_m=111320.0)
        assert (round(e, 3), round(n, 3)) == (1.0, 1.0)

    def test_bad_area_raises(self):
        with pytest.raises(ValueError, match="place string"):
            query.resolve_area((1.0, 2.0, 3.0))


class TestLatestRelease:
    def test_returns_newest_of_an_unsorted_listing(self):
        class _FakeFS:
            def ls(self, path):
                assert path == "overturemaps-us-west-2/release"
                return [
                    f"{path}/2026-08-19.0",
                    f"{path}/2026-09-23.1",
                    f"{path}/2026-09-23.0",
                ]

        assert query.latest_release(fs=_FakeFS()) == "2026-09-23.1"


class TestDataRoot:
    def test_default_is_aws_release(self):
        assert query.overture_data_root("2024-11-13.0", None).endswith("release/2024-11-13.0")

    def test_override_wins(self):
        assert query.overture_data_root("ignored", "/snap/") == "/snap"


class TestReadSegments:
    def test_drive_excludes_footway_and_returns_wkt(self, engine):
        segs = query.read_segments(WORLD_BBOX, network_type="drive", data_root=FIXTURE_ROOT, engine=engine)
        classes = {s["class"] for s in segs}
        assert "footway" not in classes
        assert {"residential", "primary"} <= classes
        main = next(s for s in segs if s["id"] == "s_main")
        assert main["geometry"].startswith("LINESTRING")
        # nested properties survive the round-trip
        assert main["names"]["primary"] == "Main St"
        assert isinstance(main["connectors"], list)

    def test_walk_includes_footway(self, engine):
        segs = query.read_segments(WORLD_BBOX, network_type="walk", data_root=FIXTURE_ROOT, engine=engine)
        assert "footway" in {s["class"] for s in segs}

    def test_bbox_filters_out_of_range(self, engine):
        far = (10.0, 10.0, 11.0, 11.0)
        assert query.read_segments(far, network_type="all", data_root=FIXTURE_ROOT, engine=engine) == []


def _segments(root, network_type="all", engine=None):
    return query.read_segments(WORLD_BBOX, network_type=network_type, data_root=root, engine=engine)


class TestReadConnectors:
    def test_returns_lon_lat_map(self, engine):
        conns = query.read_connectors(_segments(FIXTURE_ROOT, engine=engine), data_root=FIXTURE_ROOT, engine=engine)
        assert conns["ca"] == pytest.approx((0.0, 0.0))
        assert conns["cc"] == pytest.approx((0.0, 0.002))

    def test_returns_only_connectors_the_segments_reference(self, engine):
        drive = _segments(FIXTURE_ROOT, network_type="drive", engine=engine)
        conns = query.read_connectors(drive, data_root=FIXTURE_ROOT, engine=engine)
        assert set(conns) == {"ca", "cb", "cc", "cd"}  # "ce" is only on the excluded footway

    def test_no_segments_reads_nothing(self, engine):
        assert query.read_connectors([], data_root="/nonexistent/never-read", engine=engine) == {}


class TestFetchNetworkElements:
    def test_returns_segments_and_connectors(self, engine):
        segs, conns = query.fetch_network_elements(
            WORLD_BBOX, network_type="drive", data_root=FIXTURE_ROOT, engine=engine
        )
        assert len(segs) == 2  # residential + primary (footway excluded)
        # every connector referenced by a drive segment is resolvable
        referenced = {c["connector_id"] for s in segs for c in s["connectors"]}
        assert referenced <= set(conns)


class TestLocalSnapshotLayout:
    def test_fixture_is_a_local_snapshot(self):
        assert is_local_snapshot(FIXTURE_ROOT)

    def test_needs_both_files(self, tmp_path):
        (tmp_path / "segment.parquet").write_bytes(b"")
        assert not is_local_snapshot(tmp_path)
        assert not is_local_snapshot(tmp_path / "segment.parquet")


class TestCountSegments:
    def test_count_matches_read(self, engine):
        n = query.count_segments(WORLD_BBOX, network_type="drive", data_root=FIXTURE_ROOT, engine=engine)
        assert n > 0
        assert n == len(query.read_segments(WORLD_BBOX, network_type="drive", data_root=FIXTURE_ROOT, engine=engine))

    def test_empty_bbox_counts_zero(self, engine):
        assert query.count_segments((10.0, 10.0, 11.0, 11.0), data_root=FIXTURE_ROOT, engine=engine) == 0


class TestRetiredReleaseDiagnostic:
    """A missing-release / missing-path read should fail with an actionable message.

    DuckDB's own error for this ("No files found that match the pattern ...") is
    the same generic IOException it raises for any bad glob, so it reads like a
    path typo. A ``data_root`` that doesn't exist on disk reproduces that exact
    failure offline (no network / S3 needed) and lets us check the translated
    message names the release and points at a fix.
    """

    MISSING_ROOT = "/nonexistent/overture-snapshot-does-not-exist"

    def test_count_segments_names_release_and_fix(self, engine):
        with pytest.raises(Exception, match="may have been retired") as excinfo:
            query.count_segments(WORLD_BBOX, data_root=self.MISSING_ROOT, engine=engine)
        assert "s3://overturemaps-us-west-2/release/" in str(excinfo.value)
        assert "overture_release=" in str(excinfo.value)

    def test_read_segments_names_release_and_fix(self, engine):
        with pytest.raises(Exception, match="may have been retired"):
            query.read_segments(WORLD_BBOX, data_root=self.MISSING_ROOT, engine=engine)

    def test_read_connectors_names_release_and_fix(self, engine):
        with pytest.raises(Exception, match="may have been retired"):
            segments = [{"geometry": "LINESTRING (0 0, 1 1)", "connectors": [{"connector_id": "c", "at": 0.0}]}]
            query.read_connectors(segments, data_root=self.MISSING_ROOT, engine=engine)

    def test_other_errors_pass_through_unchanged(self, engine):
        # A genuinely unrelated duckdb error (bad bbox field access, say) must not
        # be relabelled as a retired release -- only "no files found" is rewritten.
        with pytest.raises(Exception) as excinfo:
            query.count_segments(WORLD_BBOX, network_type="not-a-real-type", data_root=FIXTURE_ROOT, engine=engine)
        assert "retired" not in str(excinfo.value)


def _native_geometry_snapshot(dest: Path) -> str:
    """Re-encode the WKB fixture into ``dest`` with a native duckdb GEOMETRY column.

    Current Overture releases surface ``geometry`` as GEOMETRY (not raw WKB
    binary) under duckdb spatial; this copy reproduces that encoding offline.
    """
    import duckdb

    con = duckdb.connect()
    con.install_extension("spatial")
    con.load_extension("spatial")
    for name in ("segment", "connector"):
        src = Path(FIXTURE_ROOT) / f"{name}.parquet"
        con.execute(
            f"COPY (SELECT * REPLACE (ST_GeomFromWKB(geometry) AS geometry) FROM read_parquet('{src}')) "
            f"TO '{dest / f'{name}.parquet'}' (FORMAT parquet)"
        )
    con.close()
    return str(dest)


class TestNativeGeometryEncoding:
    """Current releases store GEOMETRY, the committed fixture stores WKB; both read the same."""

    @pytest.fixture
    def native_root(self, tmp_path):
        return _native_geometry_snapshot(tmp_path)

    def test_snapshot_really_is_native_geometry(self, engine, native_root):
        query._prepare_backend(engine, native_root)
        table = engine.read_parquet(f"{native_root}/segment.parquet")
        assert table.geometry.type().is_geospatial()

    def test_read_segments_matches_wkb_fixture(self, engine, native_root):
        def by_id(root):
            segs = query.read_segments(WORLD_BBOX, network_type="all", data_root=root, engine=engine)
            return {s["id"]: s["geometry"] for s in segs}

        native = by_id(native_root)
        assert native == by_id(FIXTURE_ROOT)
        assert all(wkt.startswith("LINESTRING") for wkt in native.values())

    def test_read_connectors_matches_wkb_fixture(self, engine, native_root):
        segments = _segments(FIXTURE_ROOT, engine=engine)
        native = query.read_connectors(segments, data_root=native_root, engine=engine)
        assert native == query.read_connectors(segments, data_root=FIXTURE_ROOT, engine=engine)
        assert native

    def test_full_build_matches_wkb_fixture(self, engine, native_root):
        from netstead.overture import build

        native = build.build_network_from_overture(WORLD_BBOX, data_root=native_root, engine=engine)
        wkb = build.build_network_from_overture(WORLD_BBOX, data_root=FIXTURE_ROOT, engine=engine)
        assert len(native.links.to_pandas()) == len(wkb.links.to_pandas()) == 5
        assert len(native.nodes.to_pandas()) == len(wkb.nodes.to_pandas())


class TestSeveralNetworkTypes:
    def test_drive_plus_walk_is_the_union(self, engine):
        def ids(network_type):
            segs = query.read_segments(WORLD_BBOX, network_type=network_type, data_root=FIXTURE_ROOT, engine=engine)
            return {s["id"] for s in segs}

        drive, walk = ids("drive"), ids("walk")
        assert ids(["drive", "walk"]) == drive | walk != drive
