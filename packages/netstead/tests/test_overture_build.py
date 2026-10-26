"""End-to-end tests for netstead.overture.build against the local GeoParquet fixture.

No network / S3 — the pinned-release reads are redirected to the committed
fixture via ``data_root=``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead.overture import build

FIXTURE_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "overture")
WORLD_BBOX = (-0.5, -0.5, 0.5, 0.5)


@pytest.fixture
def engine():
    eng = IbisEngine()
    yield eng
    eng.close()


def _links_df(net):
    return net.links.to_pandas()


class TestBuildDriveNetwork:
    def test_nodes_links_and_provenance(self, engine):
        net = build.build_network_from_overture(WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine)
        links = _links_df(net)
        nodes = net.nodes.to_pandas()
        # residential (split at interior connector -> 2 sub-segs, two-way -> 4)
        # + primary one-way (1) = 5 links; footway excluded.
        assert len(links) == 5
        assert set(nodes["overture_connector_id"]) == {"ca", "cb", "cc", "cd"}
        assert "overture_segment_id" in links.columns

    def test_oneway_primary_has_single_direction(self, engine):
        net = build.build_network_from_overture(WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine)
        links = _links_df(net)
        hwy = links[links["overture_segment_id"] == "s_hwy"]
        assert len(hwy) == 1
        assert hwy.iloc[0]["ref"] == "US 1"
        assert hwy.iloc[0]["free_speed"] == pytest.approx(62.1)  # 100 km/h -> mph

    def test_name_carried(self, engine):
        net = build.build_network_from_overture(WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine)
        links = _links_df(net)
        assert "Main St" in set(links["name"])


class TestConfigAndValidation:
    def test_config_declares_crs_and_units(self, engine):
        net = build.build_network_from_overture(WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine)
        cfg = net.config.to_pandas().iloc[0]
        assert cfg["dataset_name"] == "overture_export"
        assert cfg["crs"] == "EPSG:4326"
        assert cfg["geometry_field_format"] == "WKT"
        assert cfg["speed"] == "mph"

    def test_network_validates_without_errors(self, engine):
        net = build.build_network_from_overture(WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine)
        errors = [i for i in net.validate().issues if i.severity.value == "error"]
        assert errors == [], errors


class TestEdgeCases:
    def test_extra_tags_carried_onto_links(self, engine):
        net = build.build_network_from_overture(
            WORLD_BBOX, network_type="roads", data_root=FIXTURE_ROOT, engine=engine, extra_tags=["subtype"]
        )
        assert "subtype" in net.links.to_pandas().columns

    def test_empty_area_raises(self, engine):
        with pytest.raises(ValueError, match="no Overture segments matched"):
            build.build_network_from_overture(
                (10.0, 10.0, 11.0, 11.0), network_type="roads", data_root=FIXTURE_ROOT, engine=engine
            )


class TestEdgeCrossingSegment:
    """A segment that crosses the bbox edge may end at a connector far outside the bbox."""

    BBOX = (0.0, 0.0, 0.01, 0.01)
    FAR_LON = 0.05  # 0.04 deg east of the bbox: well beyond any fixed connector-read padding

    @pytest.fixture
    def snapshot(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq
        import shapely.wkb as swkb
        from shapely.geometry import LineString, Point

        connectors = {"c_in": (0.005, 0.005), "c_far": (self.FAR_LON, 0.005)}
        coords = [connectors["c_in"], connectors["c_far"]]
        segment = {
            "id": "s_long",
            "subtype": "road",
            "class": "residential",
            "connectors": [{"connector_id": "c_in", "at": 0.0}, {"connector_id": "c_far", "at": 1.0}],
            "bbox": {"xmin": 0.005, "xmax": self.FAR_LON, "ymin": 0.005, "ymax": 0.005},
            "geometry": swkb.dumps(LineString(coords)),
        }
        pq.write_table(pa.Table.from_pylist([segment]), tmp_path / "segment.parquet")
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "id": cid,
                        "bbox": {"xmin": x, "xmax": x, "ymin": y, "ymax": y},
                        "geometry": swkb.dumps(Point(x, y)),
                    }
                    for cid, (x, y) in connectors.items()
                ]
            ),
            tmp_path / "connector.parquet",
        )
        return str(tmp_path)

    def test_far_endpoint_connector_becomes_a_node(self, engine, snapshot):
        net = build.build_network_from_overture(self.BBOX, data_root=snapshot, engine=engine)
        nodes = net.nodes.to_pandas()
        assert set(nodes["overture_connector_id"]) == {"c_in", "c_far"}
        assert nodes.set_index("overture_connector_id").loc["c_far", "x_coord"] == pytest.approx(self.FAR_LON)
        assert len(_links_df(net)) == 2  # one two-way residential segment
