"""Tests for netstead.osm.local + build_network_from_osm_file (local files only, no network)."""

import logging
from pathlib import Path

import pytest
from netstead.osm import build_network_from_osm_file
from netstead.osm.local import read_osm_file

OSM_DIR = Path(__file__).resolve().parent / "fixtures" / "osm"
OSM_XML = OSM_DIR / "tiny.osm"
OVERPASS_JSON = OSM_DIR / "tiny_overpass.json"


@pytest.mark.parametrize("path", [OSM_XML, OVERPASS_JSON], ids=["xml", "json"])
def test_reads_nodes_and_filters_ways_for_drive(path, caplog):
    with caplog.at_level(logging.WARNING, logger="netstead.osm.local"):
        nodes, ways = read_osm_file(path, network_type="roads")
    assert nodes == {1: (-71.0, 42.0), 2: (-71.0, 42.001), 3: (-71.0, 42.002), 4: (-71.002, 42.002)}
    assert [w["id"] for w in ways] == [100]  # footway filtered, incomplete 102 dropped, building 103 not a highway
    assert ways[0] == {"id": 100, "nodes": [1, 2, 3], "tags": {"highway": "residential", "name": "Main St"}}
    assert "dropped 1 way(s)" in caplog.text


@pytest.mark.parametrize("path", [OSM_XML, OVERPASS_JSON], ids=["xml", "json"])
def test_all_keeps_every_highway_but_not_buildings(path):
    _, ways = read_osm_file(path, network_type="all")
    assert [w["id"] for w in ways] == [100, 101]


def test_unsupported_suffix(tmp_path):
    with pytest.raises(ValueError, match="unsupported OSM file"):
        read_osm_file(tmp_path / "x.pbf")


def test_json_without_elements(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"features": []}')
    with pytest.raises(ValueError, match="not an Overpass JSON export"):
        read_osm_file(bad)


def test_malformed_xml(tmp_path):
    bad = tmp_path / "x.osm"
    bad.write_text("<osm><node id='1'")
    with pytest.raises(ValueError, match="could not parse"):
        read_osm_file(bad)


def test_rejects_dtd_entities(tmp_path):
    bomb = tmp_path / "bomb.osm"
    bomb.write_text(
        '<?xml version="1.0"?>\n'
        "<!DOCTYPE osm [\n"
        '  <!ENTITY lol "lol">\n'
        '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        "]>\n"
        '<osm><node id="1" lat="0" lon="0"/></osm>\n'
    )
    with pytest.raises(ValueError, match="DTD/entity declarations are not allowed"):
        read_osm_file(bomb)


def test_unknown_network_type():
    with pytest.raises(ValueError, match="unknown network_type"):
        read_osm_file(OSM_XML, network_type="boat")


def test_build_from_file_makes_links_both_ways():
    net = build_network_from_osm_file(OSM_XML)
    links = net.links.to_pandas()
    assert sorted(zip(links.from_node_id, links.to_node_id, strict=True)) == [(1, 3), (3, 1)]
    assert set(links.name) == {"Main St"} and set(links.osm_way_id) == {100}


def test_build_from_file_with_no_matching_ways(tmp_path):
    only_foot = tmp_path / "foot.json"
    only_foot.write_text(
        '{"elements": [{"type": "node", "id": 1, "lat": 0, "lon": 0}, {"type": "node", "id": 2, "lat": 0, "lon": 1},'
        ' {"type": "way", "id": 9, "nodes": [1, 2], "tags": {"highway": "footway"}}]}'
    )
    with pytest.raises(ValueError, match=r"no OSM ways in foot\.json"):
        build_network_from_osm_file(only_foot)
