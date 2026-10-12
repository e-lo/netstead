"""Tests for the workbench's foreign-key relations (read from the GMNS spec, never hard-coded)."""

import json

import pandas as pd
import pytest
from netstead.fixtures import leavenworth
from netstead.workbench import Session
from netstead.workbench.registry import as_pandas
from netstead.workbench.related import Match, Relation, count, ids_of, relate, relation_graph, restrict, row_vias


@pytest.fixture(scope="module")
def lw(tmp_path_factory):
    """The Leavenworth handle: link, node, lane, link_tod."""
    tmp = tmp_path_factory.mktemp("rel")
    src = str(leavenworth.parquet_dir())
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([src])}
    s = Session(project_dir=tmp, environ=env)
    s.dispatch({"type": "open_network", "source": src})
    return s.registry.get("leavenworth")


@pytest.fixture(scope="module")
def graph(lw):
    return relation_graph(lw.roadway.spec, lw.tables())


def test_foreign_keys_come_from_the_spec(graph):
    fks = {(f.table, f.column, f.ref_table, f.ref_column) for f in graph.fks}
    assert {
        ("link", "from_node_id", "node", "node_id"),
        ("link", "to_node_id", "node", "node_id"),
        ("lane", "link_id", "link", "link_id"),
        ("link_tod", "link_id", "link", "link_id"),
    } <= fks


def test_self_references_and_absent_tables_are_left_out(graph):
    assert all(f.table != f.ref_table for f in graph.fks)  # link.parent_link_id, node.parent_node_id
    assert not any(f.ref_table in {"geometry", "zone", "time_set_definitions"} for f in graph.fks)  # not browsable


def test_primary_keys_prefer_the_spec(graph):
    assert graph.pks == {"link": "link_id", "node": "node_id", "lane": "lane_id", "link_tod": "link_tod_id"}


def test_label_names_the_relation(graph):
    fk = next(f for f in graph.fks if f.table == "lane")
    assert fk.label == "lane.link_id → link"


def test_a_link_reaches_its_lanes_and_its_end_nodes(graph, lw):
    link = lw.links_df().iloc[0]
    rel = relate(graph, {"link": [int(link.link_id)]})
    assert rel["lane"].hop == 1 and rel["lane"].via == ["lane.link_id → link"]
    lanes = as_pandas(graph.tables["lane"])
    assert count(graph.tables["lane"], rel["lane"].matches) == int((lanes.link_id == link.link_id).sum())
    ids, more = ids_of(graph.tables["node"], rel["node"].matches, "node_id", limit=10)
    assert set(ids) == {int(link.from_node_id), int(link.to_node_id)} and not more
    assert rel["node"].via == ["link.from_node_id → node", "link.to_node_id → node"]
    assert "link" not in rel  # a source table is never "related" to itself


def test_a_node_reaches_links_both_ways_and_hop_two_reaches_their_lanes(graph, lw):
    links = lw.links_df()
    touching = set(links.loc[(links.from_node_id == 1) | (links.to_node_id == 1), "link_id"].astype(int))
    rel = relate(graph, {"node": [1]})
    ids, more = ids_of(graph.tables["link"], rel["link"].matches, "link_id", limit=1000)
    assert set(ids) == touching and not more
    assert "lane" not in rel  # one hop by default
    rel2 = relate(graph, {"node": [1]}, hops=2)
    lanes = as_pandas(graph.tables["lane"])
    assert rel2["lane"].hop == 2
    assert count(graph.tables["lane"], rel2["lane"].matches) == int(lanes.link_id.isin(touching).sum())


def test_empty_sources_relate_nothing(graph):
    assert relate(graph, {"link": []}) == {} and relate(graph, {}) == {}


def test_ids_of_truncates_in_key_order(graph):
    rel = relate(graph, {"node": [1, 2, 3, 4, 5]})
    ids, more = ids_of(graph.tables["link"], rel["link"].matches, "link_id", limit=2)
    assert len(ids) == 2 and ids == sorted(ids) and more


def test_restrict_works_on_frames_and_lazy_tables(graph):
    frame = pd.DataFrame({"a": [1, 2, 3], "b": [9, 9, 4]})
    assert restrict(frame, [Match("a", (1,), "x"), Match("b", (4,), "y")]).a.tolist() == [1, 3]
    assert restrict(frame, []).empty
    lazy = restrict(graph.tables["lane"], [Match("link_id", (1,), "x")])
    assert as_pandas(lazy).link_id.unique().tolist() == [1]
    assert count(graph.tables["lane"], []) == 0


def test_row_vias_names_the_matching_key_per_row():
    ib = Match("ib_link_id", (1,), "movement.ib_link_id → link")
    ob = Match("ob_link_id", (2,), "movement.ob_link_id → link")
    rel = Relation("movement", 1, [ib, ob])
    page = pd.DataFrame({"mvmt_id": [7, 8, 9], "ib_link_id": [1, 5, 5], "ob_link_id": [3, 2, 6]})
    assert row_vias(page, rel) == ["movement.ib_link_id → link", "movement.ob_link_id → link", None]
    assert row_vias(page, None) == [None, None, None]


def test_outbound_values_are_distinct_on_lazy_tables(graph):
    """A lane highlight points out at its links: DuckDB returns each referenced link once."""
    from netstead.workbench.related import _distinct

    lanes = as_pandas(graph.tables["lane"])
    values = _distinct(graph.tables["lane"], "link_id", "lane_id", lanes.lane_id.astype(int).tolist())
    assert len(values) == len(set(values)) == lanes.link_id.nunique()
    first = lanes.head(50)
    (match,) = relate(graph, {"lane": first.lane_id.astype(int).tolist()})["link"].matches
    assert sorted(match.values) == sorted(first.link_id.astype(int).unique().tolist())


def test_a_capped_hop_two_frontier_marks_the_next_hop_partial(graph, lw, monkeypatch):
    from netstead.workbench import related

    links = lw.links_df()
    busy = int(pd.concat([links.from_node_id, links.to_node_id]).value_counts().index[0])  # a node with 2+ links
    assert related.relate(graph, {"node": [busy]}, hops=2)["lane"].partial is False
    monkeypatch.setattr(related, "MAX_SOURCE_IDS", 1)
    rel = related.relate(graph, {"node": [busy]}, hops=2)
    assert rel["link"].partial is False  # hop 1 is complete
    assert rel["lane"].partial is True  # hop 2 started from one of the node's links only


def test_an_inbound_key_to_a_non_primary_column_follows_that_columns_values():
    """``stop.zone_code → zone.code``: a highlighted zone (by its id) reaches the stops carrying its code."""
    from netstead.workbench.related import ForeignKey, RelationGraph

    zone = pd.DataFrame({"zone_id": [1, 2, 3], "code": ["A", "B", "A"]})
    stop = pd.DataFrame({"stop_id": [10, 11, 12, 13], "zone_code": ["A", "B", "C", "A"]})
    g = RelationGraph(
        tables={"zone": zone, "stop": stop},
        fks=(ForeignKey("stop", "zone_code", "zone", "code"),),
        pks={"zone": "zone_id", "stop": "stop_id"},
    )
    rel = relate(g, {"zone": [1, 3]})
    assert rel["stop"].matches == [Match("zone_code", ("A",), "stop.zone_code → zone")]
    assert restrict(stop, rel["stop"].matches).stop_id.tolist() == [10, 13]


def test_foreign_keys_can_keep_self_references():
    from corral.spec.model import DataPackage, Field, ForeignKeyReference, Resource, Schema
    from corral.spec.model import ForeignKey as SpecFK
    from netstead.workbench.related import foreign_keys

    schema = Schema(
        fields=[Field(name="link_id"), Field(name="parent_link_id")],
        primary_key="link_id",
        foreign_keys=[SpecFK(fields="parent_link_id", reference=ForeignKeyReference(resource="", fields="link_id"))],
    )
    spec = DataPackage(name="x", resources=[Resource(name="link", path="link.csv", schema=schema)])
    columns = {"link": ["link_id", "parent_link_id"]}
    assert foreign_keys(spec, columns) == []
    assert [fk.label for fk in foreign_keys(spec, columns, self_refs=True)] == ["link.parent_link_id → link"]
