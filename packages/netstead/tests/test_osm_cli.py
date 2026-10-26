"""Tests for ``netstead build`` — the OSM network-build CLI command.

The actual OSM fetch is monkeypatched out (no network); we exercise argument
parsing, the write, and the JSON summary contract.
"""

from __future__ import annotations

import json

from corral.engines.ibis_engine import IbisEngine
from netstead.cli.app import app
from netstead.osm import network_from_records
from typer.testing import CliRunner

runner = CliRunner()

_NODE_RECS = [
    {"node_id": 1, "x_coord": -71.0, "y_coord": 42.0},
    {"node_id": 2, "x_coord": -71.0, "y_coord": 42.001},
]
_LINK_RECS = [
    {
        "link_id": 1,
        "from_node_id": 1,
        "to_node_id": 2,
        "directed": True,
        "length": 111.2,
        "free_speed": None,
        "lanes": None,
        "facility_type": "residential",
        "name": "Main St",
        "geometry": "LINESTRING (-71.0 42.0, -71.0 42.001)",
        "osm_way_id": 100,
        "osm_node_ids": "1,2",
    },
    {
        "link_id": 2,
        "from_node_id": 2,
        "to_node_id": 1,
        "directed": True,
        "length": 111.2,
        "free_speed": None,
        "lanes": None,
        "facility_type": "residential",
        "name": "Main St",
        "geometry": "LINESTRING (-71.0 42.001, -71.0 42.0)",
        "osm_way_id": 100,
        "osm_node_ids": "2,1",
    },
]


def _fake_network(*_args, **_kwargs):
    return network_from_records(_NODE_RECS, _LINK_RECS, engine=IbisEngine())


def test_build_bbox_writes_network(tmp_path, monkeypatch):
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    dest = tmp_path / "net"
    result = runner.invoke(app, ["build", str(dest), "--bbox", "-71.1,41.9,-70.9,42.1", "--format", "csv", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["nodes"] == 2
    assert payload["links"] == 2
    assert payload["spec_version"] == "0.97"
    assert dest.exists()


def test_build_point_requires_buffer(monkeypatch):
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    result = runner.invoke(app, ["build", "out", "--point", "42.0,-71.0"])
    assert result.exit_code != 0


def test_build_requires_exactly_one_area(monkeypatch):
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    # zero areas
    assert runner.invoke(app, ["build", "out"]).exit_code != 0
    # two areas
    two = runner.invoke(app, ["build", "out", "--bbox", "0,0,1,1", "--place", "Anytown"])
    assert two.exit_code != 0


def test_build_rejects_unknown_source(monkeypatch):
    # 'osm' and 'overture' are the supported sources; anything else is rejected.
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    result = runner.invoke(app, ["build", "out", "--bbox", "0,0,1,1", "--source", "bogus"])
    assert result.exit_code != 0


def test_build_rejects_bad_bbox(monkeypatch):
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    result = runner.invoke(app, ["build", "out", "--bbox", "0,0,1"])  # only 3 values
    assert result.exit_code != 0


def test_build_rejects_buffer_with_bbox(monkeypatch):
    monkeypatch.setattr("netstead.osm.build_network_from_osm", _fake_network)
    # --buffer is only meaningful with --point; combining it with --bbox used to
    # be silently ignored. The CLI should reject the combination instead.
    result = runner.invoke(app, ["build", "out", "--bbox", "0,0,1,1", "--buffer", "500"])
    assert result.exit_code != 0


def test_build_reports_network_error_cleanly(monkeypatch):
    import requests

    def _boom(*_a, **_k):
        raise requests.exceptions.ConnectionError("overpass unreachable")

    monkeypatch.setattr("netstead.osm.build_network_from_osm", _boom)
    result = runner.invoke(app, ["build", "out", "--bbox", "0,0,1,1"])
    assert result.exit_code == 1
    # Handled (clean Exit), not a leaked ConnectionError traceback.
    assert isinstance(result.exception, SystemExit)


def _network_types_passed(monkeypatch, tmp_path, *args):
    seen = {}

    def fake(*_a, network_type, **_kw):
        seen["network_type"] = network_type
        return _fake_network()

    monkeypatch.setattr("netstead.osm.build_network_from_osm", fake)
    result = runner.invoke(
        app, ["build", str(tmp_path / "net"), "--bbox", "-71.1,41.9,-70.9,42.1", "--format", "csv", "--json", *args]
    )
    return result, seen.get("network_type")


def test_network_type_defaults_to_roads(tmp_path, monkeypatch):
    result, seen = _network_types_passed(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.stderr
    assert seen == ["roads"]
    assert json.loads(result.stdout)["network_type"] == "roads"


def test_network_type_repeated_and_comma_values(tmp_path, monkeypatch):
    repeated = _network_types_passed(monkeypatch, tmp_path, "--network-type", "paths", "--network-type", "roads")
    assert repeated[1] == ["paths", "roads"]
    assert _network_types_passed(monkeypatch, tmp_path, "--network-type", "roads, paths")[1] == ["roads", "paths"]


def test_network_type_legacy_names_are_mapped(tmp_path, monkeypatch):
    assert _network_types_passed(monkeypatch, tmp_path, "--network-type", "drive")[1] == ["roads"]
    assert _network_types_passed(monkeypatch, tmp_path, "--network-type", "bike")[1] == ["roads", "paths"]


def test_network_type_unknown_name_lists_the_valid_ones(tmp_path, monkeypatch):
    result, _ = _network_types_passed(monkeypatch, tmp_path, "--network-type", "boat")
    assert result.exit_code != 0
    assert "roads" in result.output + result.stderr and "paths" in result.output + result.stderr
