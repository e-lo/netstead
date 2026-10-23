"""GET /api/selection/export: the Find tab's Segment / Link list forms in GMNS, OSM and GERS ids (Leavenworth)."""

import json

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app

SRC = str(leavenworth.parquet_dir())
EXPORT = "/api/selection/export"
PINE = "Pine Street between Orchard Street and Central Avenue"


@pytest.fixture
def setup(tmp_path):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([SRC, str(tmp_path)])}
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[])
    s.dispatch({"type": "open_network", "source": SRC})
    return s, TestClient(build_app(s))


def _pine(s):
    s.dispatch({"type": "select", "utterance": PINE})
    assert s.selection["status"] == "resolved" and s.selection["link_ids"] == [60]


def _get(client, what, form, ids):
    return client.get(EXPORT, params={"what": what, "form": form, "id_system": ids})


def test_segment_and_link_list_each_carry_only_their_own_fields(setup):
    s, client = setup
    _pine(s)
    seg = _get(client, "fragment", "segment", "gmns").json()
    assert seg["content"] == {"links": {"name": ["Pine Street"]}, "from": {"node_id": 33}, "to": {"node_id": 19}}
    assert seg["valid"] is True and seg["captions"] == []
    ids = _get(client, "fragment", "link_list", "gmns").json()
    assert ids["content"] == {"links": {"link_id": [60]}} and ids["valid"] is True
    assert ids["filename"] == "leavenworth-selection-fragment-link_list.json"


def test_osm_ids_for_both_forms_and_the_shared_way_caption(setup):
    s, client = setup
    _pine(s)
    seg = _get(client, "fragment", "segment", "osm").json()
    assert seg["content"]["from"] == {"osm_node_id": 46999094} and seg["content"]["to"] == {"osm_node_id": 46993874}
    assert seg["valid"] is True and seg["captions"] == []  # a segment names its street: no way ids to share
    ids = _get(client, "fragment", "link_list", "osm").json()
    assert ids["content"] == {"links": {"osm_way_id": [5846369]}} and ids["valid"] is True
    assert ids["captions"] == ["OSM way 5846369 also covers 11 links outside this selection"]
    assert ids["filename"].endswith("-link_list-osm.json")


def test_shared_osm_ways_are_deduplicated_with_a_caption(setup):
    s, client = setup
    links = s.registry.get("leavenworth").links_df()
    way = links.osm_way_id.value_counts().loc[lambda c: c > 1].index[0]
    ids = [int(i) for i in links.loc[links.osm_way_id == way, "link_id"].head(3)]
    s.dispatch({"type": "select", "link_ids": ids})
    got = _get(client, "fragment", "link_list", "osm").json()
    assert got["content"]["links"]["osm_way_id"] == [int(way)]
    assert f"{len(ids)} links share OSM way {int(way)}" in got["captions"]


def test_gers_is_unavailable_on_leavenworth_with_the_reason(setup):
    s, client = setup
    _pine(s)
    r = _get(client, "fragment", "link_list", "gers")
    assert r.status_code == 409 and "overture_segment_id" in r.json()["detail"]
    options = _get(client, "fragment", "link_list", "gmns").json()["options"]
    assert options["id_systems"]["gmns"] is None and options["id_systems"]["osm"] is None
    assert "overture_segment_id" in options["id_systems"]["gers"]


def test_projectcard_forms_per_id_system(setup):
    s, client = setup
    _pine(s)
    seg = _get(client, "projectcard", "segment", "gmns").json()["content"]
    assert seg == {
        "links": {"name": ["Pine Street"], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    link_list = _get(client, "projectcard", "link_list", "osm").json()["content"]
    assert link_list == {"links": {"osm_link_id": ["5846369"], "ignore_missing": True}}
    node_seg = _get(client, "projectcard", "segment", "osm").json()["content"]
    assert node_seg["from"] == {"osm_node_id": "46999094"}
    validate = pytest.importorskip("projectcard.validate")
    for facility in (seg, link_list, node_seg):
        change = {"facility": facility, "property_changes": {"lanes": {"set": 2}}}
        assert validate.validate_card({"project": "pine", "changes": [{"roadway_property_change": change}]})


def test_a_clicked_selection_has_no_segment(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [60]})
    r = _get(client, "fragment", "segment", "gmns")
    assert r.status_code == 409 and "use Link list" in r.json()["detail"]
    options = _get(client, "fragment", "link_list", "gmns").json()["options"]
    assert options["forms"] == {"segment": "This selection has no named segment; use Link list", "link_list": None}


def test_projectcard_options_rule_out_gers(setup):
    s, client = setup
    _pine(s)
    assert _get(client, "projectcard", "link_list", "gmns").json()["options"]["id_systems"]["gers"]
    assert _get(client, "projectcard", "segment", "gmns").json()["options"]["forms"]["segment"] is None


def test_the_older_forms_cannot_be_written_in_other_ids(setup):
    s, client = setup
    _pine(s)
    r = _get(client, "fragment", "resolved", "osm")
    assert r.status_code == 409 and "segment or link_list" in r.json()["detail"]
