"""Deletions cascade through core's plans (links, then nodes); additions are one mutation, nodes first."""

import pytest
from netstead_project_cards.errors import CardError
from netstead_project_cards.lower import apply_change
from netstead_project_cards.types import RoadwayAddition, RoadwayDeletion

NET = "leavenworth"
NEW_NODE = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.60, "node_type": "intersection"}
NEW_LINK = {
    "link_id": 9001,
    "from_node_id": 1,
    "to_node_id": 9001,
    "name": "New Street",
    "facility_type": "residential",
    "lanes": 1,
    "free_speed": 25.0,
    "allowed_uses": "auto,walk,bike",
    "directed": False,
}


def counts(session):
    return {name: table.count() for name, table in session.network(NET).roadway.tables.items()}


def test_deleting_a_link_takes_its_lanes(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayDeletion(links=[1]))
    after = counts(session)
    assert after["link"] == before["link"] - 1 and after["lane"] == before["lane"] - 1
    assert applied.steps == 1 and applied.rows == {"lane": 1, "link": 1}
    assert session.network(NET).lineage[-1] == "project_cards: roadway_deletion link 1"


def test_a_node_still_used_by_a_link_is_refused(session, host):
    with pytest.raises(CardError, match="still used"):
        apply_change(host, NET, RoadwayDeletion(nodes=[1]))
    assert session.network(NET).version == 0


def test_missing_ids_are_refused(host):
    with pytest.raises(CardError, match="not found"):
        apply_change(host, NET, RoadwayDeletion(links=[999999]))


def test_an_empty_deletion_is_refused(host):
    with pytest.raises(CardError, match="needs links and/or nodes"):
        apply_change(host, NET, RoadwayDeletion())


def test_adding_a_node_and_a_link_is_one_mutation(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[NEW_LINK]))
    assert applied.steps == 1 and applied.rows == {"node": 1, "link": 1}
    assert counts(session)["link"] == before["link"] + 1 and counts(session)["node"] == before["node"] + 1
    assert len(session.edits[NET].last().results) == 2  # the node's add_rows, then the link's
    links = session.network(NET).links_df()
    assert links.loc[links.link_id == 9001, "name"].tolist() == ["New Street"]


def test_clean_nodes_deletes_end_nodes_nothing_else_uses(session, host):
    apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[NEW_LINK]))
    nodes = counts(session)["node"]
    applied = apply_change(host, NET, RoadwayDeletion(links=[9001], clean_nodes=True))
    assert counts(session)["node"] == nodes - 1  # 9001 went; node 1 is used by other links and stays
    assert applied.steps == 2  # links, then nodes: core plans a node deletion against the network as it is
    assert [e.note for e in session.edits[NET].entries[-2:]] == [
        "roadway_deletion link 9001 (and unused end nodes) (links)",
        "roadway_deletion link 9001 (and unused end nodes) (nodes)",
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (RoadwayAddition(links=[{**NEW_LINK, "link_id": 1}], nodes=[NEW_NODE]), "link 1 already exist"),
        (RoadwayAddition(links=[{**NEW_LINK, "to_node_id": 424242}]), "node.* 424242, which are not in the network"),
        (RoadwayAddition(links=[{**NEW_LINK, "colour": "red"}], nodes=[NEW_NODE]), "no column"),
        (RoadwayAddition(nodes=[NEW_NODE, NEW_NODE]), "repeat"),
    ],
)
def test_bad_additions_are_refused_and_leave_nothing_behind(session, host, change, message):
    before = counts(session)
    with pytest.raises(CardError, match=message):
        apply_change(host, NET, change)
    assert counts(session) == before and session.network(NET).version == 0
