"""Tests for /api/n/{net_id}/{component}/... routes."""

import json

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.viz.buffers import unpack_network
from netstead.workbench import Session, build_app

BASE = "/api/n/rdu-i40/roadway"


@pytest.fixture(scope="module")
def client(tmp_path_factory, rdu_source):
    tmp = tmp_path_factory.mktemp("wb")
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([rdu_source])}
    s = Session(project_dir=tmp, environ=env, parser=StubParser())
    s.dispatch({"type": "open_network", "source": rdu_source})
    return TestClient(build_app(s))


def test_network_bin_unpacks(client):
    r = client.get(f"{BASE}/network.bin")
    assert r.headers["content-type"] == "application/octet-stream"
    net = unpack_network(r.content)
    assert net["links"]["count"] == 178 and net["nodes"]["count"] == 143


def test_network_bin_uses_geometry_table(tmp_path):
    """Leavenworth carries link shapes in its ``geometry`` table, so links are not all straight segments."""
    source = str(leavenworth.csv_dir())
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([source])}
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser())
    s.dispatch({"type": "open_network", "source": source})
    c = TestClient(build_app(s))
    net_id = s.state()["networks"][0]["id"]
    net = unpack_network(c.get(f"/api/n/{net_id}/roadway/network.bin").content)
    assert net["links"]["count"] == 339
    assert len(net["links"]["positions"]) > 2 * 2 * 339  # more than two vertices (four floats) per link


def test_attrs_index_aligned(client):
    j = client.get(f"{BASE}/network.attrs.json").json()
    assert len(j["link_id"]) == len(j["facility_type"]) == 178


def test_properties_and_property(client):
    props = {p["name"]: p["kind"] for p in client.get(f"{BASE}/properties").json()["properties"]}
    assert props["lanes"] == "continuous"
    p = client.get(f"{BASE}/property/lanes").json()
    assert p["kind"] == "continuous" and len(p["values"]) == 178
    assert client.get(f"{BASE}/property/nope").status_code == 404


def test_feature_lookup(client):
    lid = client.get(f"{BASE}/network.attrs.json").json()["link_id"][0]
    j = client.get(f"{BASE}/feature/link/{lid}").json()
    assert j["pk"] == "link_id" and j["attributes"]["link_id"] == lid and "geometry" not in j["attributes"]
    assert client.get(f"{BASE}/feature/link/999999999").status_code == 404


def test_tables_schema_rows(client):
    names = [t["name"] for t in client.get(f"{BASE}/tables").json()["tables"]]
    assert names[:2] == ["link", "node"]
    assert client.get(f"{BASE}/table/link/schema").json()["primary_key"] == "link_id"
    rows = client.get(f"{BASE}/table/link/rows", params={"limit": 5, "sort": "link_id", "dir": "desc"}).json()
    assert rows["total"] == 178 and len(rows["rows"]) == 5


def test_rows_filter_and_ids(client):
    spec = json.dumps([{"col": "facility_type", "op": "contains", "val": "motorway"}])
    j = client.get(f"{BASE}/table/link/rows", params={"filter": spec}).json()
    assert 0 < j["total"] < 178
    lid = client.get(f"{BASE}/network.attrs.json").json()["link_id"][0]
    assert client.get(f"{BASE}/table/link/rows", params={"ids": str(lid)}).json()["total"] == 1


def test_bad_filter_is_400(client):
    assert client.get(f"{BASE}/table/link/rows", params={"filter": "{"}).status_code == 400
    bad = json.dumps([{"col": "nope", "op": "eq", "val": 1}])
    assert client.get(f"{BASE}/table/link/rows", params={"filter": bad}).status_code == 400


def test_unknown_network_table_component(client):
    assert client.get("/api/n/nope/roadway/properties").status_code == 404
    assert client.get(f"{BASE}/table/nope/schema").status_code == 404
    assert client.get("/api/n/rdu-i40/bogus/properties").status_code == 404
    assert client.get("/api/n/rdu-i40/transit/properties").status_code == 501


# --- distinct values and schema enums (the column-filter popovers), on Leavenworth ---

LV = "/api/n/leavenworth/roadway"


@pytest.fixture(scope="module")
def leavenworth_client(tmp_path_factory):
    from netstead.fixtures import leavenworth

    src = str(leavenworth.parquet_dir())
    tmp = tmp_path_factory.mktemp("wb-lv")
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([src])}
    s = Session(project_dir=tmp, environ=env, parser=StubParser())
    s.dispatch({"type": "open_network", "source": src})
    return TestClient(build_app(s))


def test_distinct_values_route(leavenworth_client):
    j = leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "facility_type"}).json()
    assert {"residential", "primary"} <= set(j["values"]) and j["list_like"] is False and j["truncated"] is False
    j = leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "allowed_uses"}).json()
    assert j["list_like"] is True and "auto" in j["values"]


def test_distinct_values_route_limit_and_errors(leavenworth_client):
    j = leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "facility_type", "limit": 1}).json()
    assert len(j["values"]) == 1 and j["truncated"] is True
    assert leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "nope"}).status_code == 400
    assert leavenworth_client.get(f"{LV}/table/nope/distinct", params={"col": "x"}).status_code == 404
    assert leavenworth_client.get(f"{LV}/table/link/distinct").status_code == 422  # col is required
    assert leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "x", "limit": 0}).status_code == 422


def test_streets_crossing_lists_only_streets_that_meet_it(leavenworth_client):
    got = leavenworth_client.get(f"{LV}/streets/crossing", params={"name": "pine street"}).json()
    assert got["street"] == "pine street"
    assert {"Orchard Street", "Central Avenue"} <= set(got["streets"])
    assert "Pine Street" not in got["streets"] and got["streets"] == sorted(got["streets"])
    every = leavenworth_client.get(f"{LV}/table/link/distinct", params={"col": "name"}).json()["values"]
    assert len(got["streets"]) < len(every)
    unknown = leavenworth_client.get(f"{LV}/streets/crossing", params={"name": "No Such Road"}).json()
    assert unknown["streets"] == []
    assert leavenworth_client.get(f"{LV}/streets/crossing").status_code == 422


def test_distinct_values_route_on_a_lazy_table(leavenworth_client):
    j = leavenworth_client.get(f"{LV}/table/lane/distinct", params={"col": "allowed_uses"}).json()
    assert j["values"] and j["list_like"] is True


def test_schema_carries_spec_enums(leavenworth_client):
    cols = {c["name"]: c for c in leavenworth_client.get(f"{LV}/table/link/schema").json()["columns"]}
    assert "shared lane" in cols["bike_facility"]["enum"]  # a shared category, inlined by the spec loader
    assert cols["free_speed"]["enum"] is None and cols["facility_type"]["enum"] is None  # GMNS leaves it free text


def test_rows_route_accepts_the_token_operators(leavenworth_client):
    body = {"filter": [{"col": "allowed_uses", "op": "any_of", "val": ["bike"]}], "limit": 1}
    assert leavenworth_client.post(f"{LV}/table/link/rows", json=body).json()["total"] > 0
    body = {"filter": [{"col": "free_speed", "op": "between", "val": [35, 45]}], "limit": 1}
    assert leavenworth_client.post(f"{LV}/table/link/rows", json=body).json()["total"] > 0
    body = {"filter": [{"col": "free_speed", "op": "between", "val": [20]}], "limit": 1}
    assert leavenworth_client.post(f"{LV}/table/link/rows", json=body).status_code == 400
