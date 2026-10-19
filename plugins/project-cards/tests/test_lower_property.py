"""A property change becomes core edit plans (host.plan_update) applied in one host.mutate."""

import pytest
from netstead_project_cards.errors import CardError, CardNotSupported
from netstead_project_cards.lower import apply_change, describe
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection, TransitPropertyChange

NET = "leavenworth"


def cell(session, key, column, table="link"):
    h = session.network(NET)
    frame = h.links_df() if table == "link" else h.nodes_df()
    return frame.loc[frame[f"{table}_id"] == key, column].tolist()


def change(ids, table="link", **props):
    return RoadwayPropertyChange(
        facility=Selection(table=table, ids=ids), property_changes={k: PropertyChange(**v) for k, v in props.items()}
    )


def test_set_is_one_mutation_planned_by_core(session, host):
    applied = apply_change(host, NET, change([1, 2], free_speed={"set": 30}))
    assert cell(session, 1, "free_speed") == [30.0] and cell(session, 2, "free_speed") == [30.0]
    assert applied.steps == 1 and applied.rows == {"link": 2}
    last = session.edits[NET].last()
    assert last.source == "project_cards" and len(last.results) == 1
    assert session.network(NET).lineage[-1] == "project_cards: roadway_property_change link 1, 2: free_speed → 30"


def test_describe_names_the_change():
    assert describe(change([1], free_speed={"existing": 40, "set": 30})) == (
        "roadway_property_change link 1: free_speed 40 → 30"
    )
    assert describe(change([1, 2, 3, 4, 5, 6], lanes={"change": 1})) == (
        "roadway_property_change link 1, 2, 3, 4, 5 (+1 more): lanes +1"
    )


def test_an_existing_mismatch_is_a_conflict_and_changes_nothing(session, host):
    with pytest.raises(CardError, match=r"free_speed: expected 40 on link 2, found 40\.23"):
        apply_change(host, NET, change([1, 2], free_speed={"existing": 40, "set": 30}))
    assert session.network(NET).version == 0 and cell(session, 1, "free_speed") == [40.0]


def test_existing_conflict_can_warn_or_skip(session, host):
    warned = apply_change(
        host, NET, change([2], free_speed={"existing": 40, "set": 30, "existing_value_conflict": "warn"})
    )
    assert warned.warnings and cell(session, 2, "free_speed") == [30.0]
    skipped = apply_change(
        host,
        NET,
        change([1], free_speed={"existing": 99, "set": 30, "existing_value_conflict": "skip"}, lanes={"set": 2}),
    )
    assert skipped.skipped and cell(session, 1, "free_speed") == [40.0] and cell(session, 1, "lanes") == [2]


def test_all_properties_skipped_changes_nothing(session, host):
    applied = apply_change(
        host, NET, change([1], free_speed={"existing": 99, "set": 30, "existing_value_conflict": "skip"})
    )
    assert applied.steps == 0 and applied.skipped and session.network(NET).version == 0


def test_a_delta_groups_rows_by_their_current_value(session, host):
    apply_change(host, NET, change([1], lanes={"set": 2}))
    applied = apply_change(host, NET, change([1, 2], lanes={"change": 1}))
    assert cell(session, 1, "lanes") == [3] and cell(session, 2, "lanes") == [2]
    assert applied.steps == 1 and len(session.edits[NET].last().results) == 2  # one update per current value


def test_core_refuses_what_storage_cannot_hold(session, host):
    with pytest.raises(CardError, match="does not fit"):
        apply_change(host, NET, change([1], free_speed={"set": "fast"}))
    with pytest.raises(CardError, match="does not fit"):
        apply_change(host, NET, change([1], lanes={"set": 1.5}))
    with pytest.raises(CardError, match="key"):
        apply_change(host, NET, change([1], link_id={"set": 2}))
    assert session.network(NET).version == 0


@pytest.mark.parametrize(
    ("ids", "props", "message"),
    [
        ([999999], {"lanes": {"set": 2}}, "link 999999 not found"),
        ([1], {"nope": {"set": 2}}, "no column"),
        ([1], {"name": {"change": 1}}, "only numbers change by a delta"),
    ],
)
def test_bad_targets_are_refused(session, host, ids, props, message):
    with pytest.raises(CardError, match=message):
        apply_change(host, NET, change(ids, **props))


def test_a_node_property(session, host):
    apply_change(host, NET, change([1], table="node", ctrl_type={"existing": "stop_2_way", "set": "signal"}))
    assert cell(session, 1, "ctrl_type", table="node") == ["signal"]


def test_a_query_applies_to_its_resolved_ids(session, host):
    sel = Selection(query={"links": {"name": ["Benton Street"], "ignore_missing": True}}, resolved=[1, 5])
    apply_change(host, NET, RoadwayPropertyChange(facility=sel, property_changes={"lanes": PropertyChange(set=2)}))
    assert cell(session, 1, "lanes") == [2] and cell(session, 5, "lanes") == [2]


def test_an_incomplete_change_and_transit_are_refused(host):
    with pytest.raises(CardError, match="needs a facility"):
        apply_change(host, NET, RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)}))
    with pytest.raises(CardNotSupported, match="P6"):
        apply_change(host, NET, TransitPropertyChange())
