"""Deletions cascade through core's plans (links, then nodes); additions are one mutation, nodes first."""

import pytest
from netstead_project_cards.errors import CardError, CardNotSupported
from netstead_project_cards.lower import apply_change, describe
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


def test_ignore_missing_skips_missing_links(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayDeletion(links=[1, 999999], ignore_missing=True))
    assert counts(session)["link"] == before["link"] - 1 and applied.rows == {"lane": 1, "link": 1}
    assert applied.skipped == ["link 999999 not found; skipped (ignore_missing)"]


def test_ignore_missing_with_clean_nodes_uses_only_the_links_still_there(session, host):
    plain = apply_change(host, NET, RoadwayDeletion(links=[1], clean_nodes=True)).rows
    host.undo(NET)
    applied = apply_change(host, NET, RoadwayDeletion(links=[999999, 1], clean_nodes=True, ignore_missing=True))
    assert applied.rows == plain and applied.skipped == ["link 999999 not found; skipped (ignore_missing)"]


def test_ignore_missing_with_every_link_gone_changes_nothing(session, host):
    applied = apply_change(host, NET, RoadwayDeletion(links=[999998, 999999], ignore_missing=True))
    assert applied.marks == [] and applied.skipped == ["link 999998, 999999 not found; skipped (ignore_missing)"]
    assert session.network(NET).version == 0


def test_ignore_missing_is_for_links_only(host):
    with pytest.raises(CardError, match="not found"):
        apply_change(host, NET, RoadwayDeletion(nodes=[999999], ignore_missing=True))


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
        (RoadwayAddition(links=[{**NEW_LINK, "link_id": 1}], nodes=[NEW_NODE]), "^link 1 already exists$"),
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


# ---------------------------------------------------------------- review fixes

NODE_USERS = [1, 2, 3, 4, 5, 185, 209, 293]  # every link that starts or ends at node 1


@pytest.mark.parametrize(
    ("planner", "change"),
    [
        ("plan_delete", RoadwayDeletion(links=[1])),
        ("plan_delete", RoadwayDeletion(links=[1], clean_nodes=True)),  # step 1 of two
        ("plan_add", RoadwayAddition(nodes=[NEW_NODE], links=[NEW_LINK])),
    ],
)
def test_an_edit_between_reading_and_planning_is_refused(session, host, race_before, planner, change):
    before = counts(session)
    race_before(planner)
    with pytest.raises(CardError, match="leavenworth changed while the change was being planned; apply it again"):
        apply_change(host, NET, change)
    assert counts(session) == before and session.network(NET).version == 1  # only the race's edit landed


def test_a_node_other_links_still_use_is_refused_before_anything_changes(session, host):
    message = (
        r"^node 1 is still used by links 2, 3, 4, 5, 185 \(\+2 more\), which this change does not delete; "
        r"add them or drop the node$"
    )
    with pytest.raises(CardError, match=message):
        apply_change(host, NET, RoadwayDeletion(links=[1], nodes=[1]))
    assert session.network(NET).version == 0  # step 1 (the link) was never applied
    with pytest.raises(CardError, match=r"^node 1 is still used by link 293, which"):
        apply_change(host, NET, RoadwayDeletion(links=NODE_USERS[:-1], nodes=[1]))
    assert session.network(NET).version == 0


def test_deleting_a_node_with_all_its_links(session, host):
    before = counts(session)
    applied = apply_change(host, NET, RoadwayDeletion(links=NODE_USERS, nodes=[1]))
    assert applied.steps == 2 and counts(session)["node"] == before["node"] - 1
    assert counts(session)["link"] == before["link"] - len(NODE_USERS)


def test_clean_nodes_with_a_self_loop(session, host):
    loop = {**NEW_LINK, "link_id": 9002, "from_node_id": 9001, "to_node_id": 9001}
    apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[loop]))
    nodes = counts(session)["node"]
    applied = apply_change(host, NET, RoadwayDeletion(links=[9002], clean_nodes=True))
    assert counts(session)["node"] == nodes - 1 and applied.rows["node"] == 1


def test_clean_nodes_with_two_deleted_links_sharing_a_node(session, host):
    other = {**NEW_NODE, "node_id": 9002}
    onward = {**NEW_LINK, "link_id": 9002, "from_node_id": 9001, "to_node_id": 9002}
    apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE, other], links=[NEW_LINK, onward]))
    nodes = counts(session)["node"]
    applied = apply_change(host, NET, RoadwayDeletion(links=[9001, 9002], clean_nodes=True))
    assert counts(session)["node"] == nodes - 2  # 9001 (shared) and 9002 went; node 1 stays
    assert applied.rows["node"] == 2


def test_describe_many_ids():
    assert describe(RoadwayDeletion(links=list(range(1, 8)), nodes=[1])) == (
        "roadway_deletion link 1, 2, 3, 4, 5 (+2 more) and node 1"
    )
    added = RoadwayAddition(nodes=[{**NEW_NODE, "node_id": 9000 + i} for i in range(6)])
    assert describe(added) == "roadway_addition node 9000, 9001, 9002, 9003, 9004 (+1 more)"


def test_clean_nodes_without_links_does_not_mention_end_nodes():
    assert describe(RoadwayDeletion(nodes=[1], clean_nodes=True)) == "roadway_deletion node 1"


def test_already_exist_keeps_the_input_order(host):
    links = [{**NEW_LINK, "link_id": 5}, {**NEW_LINK, "link_id": 9002}, {**NEW_LINK, "link_id": 1}]
    with pytest.raises(CardError, match=r"^link 5, 1 already exist$"):
        apply_change(host, NET, RoadwayAddition(links=links, nodes=[NEW_NODE]))


@pytest.mark.parametrize("flag", ["walk_access", "bike_access", "drive_access", "bus_only", "rail_only"])
def test_a_foreign_field_with_no_column_is_not_supported(session, host, flag):
    with pytest.raises(CardNotSupported, match=f"^link record field '{flag}' has no column in this network$"):
        apply_change(host, NET, RoadwayAddition(nodes=[NEW_NODE], links=[{**NEW_LINK, flag: 1}]))
    assert session.network(NET).version == 0
