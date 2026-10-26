"""catalog.applicability compares a property change's ``existing`` with the network, as applying it would."""

from __future__ import annotations

import pytest
from netstead.workbench.errors import ActionError
from netstead_project_cards.actions import ApplyCard, CheckCard, OpenCatalog
from netstead_project_cards.card import DraftCard
from netstead_project_cards.catalog import applicability
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"


@pytest.fixture
def frames(session):
    handle = session.network(NET)
    return handle.links_df(), handle.nodes_df()


def change(ids, existing, conflict="error", prop="lanes", table="link", ignore_missing=False):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids), table=table, ignore_missing=ignore_missing),
        property_changes={prop: PropertyChange(set=9, existing=existing, existing_value_conflict=conflict)},
    )


def lanes_of(links, key):
    return int(links.loc[links.link_id == key, "lanes"].iloc[0])


def test_a_matching_existing_value_applies(frames):
    links, nodes = frames
    result = applicability(DraftCard(changes=[change([1], lanes_of(links, 1))]), links, nodes)
    assert result["ok"] is True and "warnings" not in result["changes"][0]


def test_int_and_float_existing_values_match(frames):
    links, nodes = frames
    now = lanes_of(links, 1)
    floated = links.assign(lanes=links["lanes"].astype(float))
    assert applicability(DraftCard(changes=[change([1], now)]), floated, nodes)["ok"] is True
    assert applicability(DraftCard(changes=[change([1], float(now))]), links, nodes)["ok"] is True


def test_a_mismatching_existing_value_is_a_conflict_by_default(frames):
    links, nodes = frames
    result = applicability(DraftCard(changes=[change([1, 2, 3], 99)]), links, nodes)
    one = result["changes"][0]
    assert result["ok"] is False and one["resolved"] == 3 and one["missing"] == []
    assert one["reason"].startswith("conflict: lanes: expected 99, found ")
    assert one["reason"].endswith(" on 3 of 3 links")


def test_a_conflict_counts_only_the_mismatching_rows(frames):
    links, nodes = frames
    wide = links.assign(lanes=[1] * len(links))
    wide.loc[wide.link_id == 2, "lanes"] = 2
    reason = applicability(DraftCard(changes=[change([1, 2, 3], 1)]), wide, nodes)["changes"][0]["reason"]
    assert reason == "conflict: lanes: expected 1, found 2 on 1 of 3 links"


@pytest.mark.parametrize(("conflict", "tail"), [("warn", ""), ("skip", "; lanes will be skipped")])
def test_warn_and_skip_conflicts_apply_with_a_warning(frames, conflict, tail):
    links, nodes = frames
    result = applicability(DraftCard(changes=[change([1, 2], 99, conflict)]), links, nodes)
    one = result["changes"][0]
    assert result["ok"] is True and one["reason"] is None
    (warning,) = one["warnings"]
    assert warning.startswith("lanes: expected 99, found ") and warning.endswith(f" on 2 of 2 links{tail}")


def test_every_conflicting_property_is_named(frames):
    links, nodes = frames
    both = RoadwayPropertyChange(
        facility=Selection(ids=[1]),
        property_changes={"lanes": PropertyChange(set=1, existing=99), "name": PropertyChange(set="x", existing="zz")},
    )
    reason = applicability(DraftCard(changes=[both]), links, nodes)["changes"][0]["reason"]
    assert reason.startswith("conflict: lanes: expected 99") and "; name: expected zz" in reason


def test_nodes_are_counted_as_nodes_and_an_unknown_column_is_a_reason(frames):
    links, nodes = frames
    key = nodes["node_id"].iloc[0].item()
    clash = change([key], "never-a-value", prop="node_id", table="node")
    assert applicability(DraftCard(changes=[clash]), links, nodes)["changes"][0]["reason"].endswith("on 1 of 1 nodes")
    unknown = change([1], 2, prop="nope")
    assert applicability(DraftCard(changes=[unknown]), links, nodes)["changes"][0]["reason"] == (
        "link has no column(s) nope"
    )


def test_ignored_missing_ids_are_not_compared(frames):
    links, nodes = frames
    both = change([1, 999999], lanes_of(links, 1), ignore_missing=True)
    result = applicability(DraftCard(changes=[both]), links, nodes)
    assert result["ok"] is True and result["changes"][0]["missing"] == [999999]


def test_applicability_agrees_with_applying(session, tmp_path):
    root = tmp_path / "cat"
    root.mkdir()
    (root / "p.yml").write_text(DraftCard(project="P", changes=[change([1], 99)]).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    checked = session.dispatch(CheckCard(card_id="p.yml"))
    assert checked["ok"] is False and checked["changes"][0]["reason"].startswith("conflict: lanes: expected 99")
    with pytest.raises(ActionError, match="conflict: lanes: expected 99"):
        session.dispatch(ApplyCard(card_id="p.yml"))


def test_a_card_already_applied_this_session_says_so(session, tmp_path):
    root = tmp_path / "cat"
    root.mkdir()
    (root / "p.yml").write_text(DraftCard(project="P", changes=[change([1], None)]).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    assert session.dispatch(CheckCard(card_id="p.yml"))["ok"] is True
    session.dispatch(ApplyCard(card_id="p.yml"))
    again = session.dispatch(CheckCard(card_id="p.yml"))
    assert again["ok"] is False and again["reason"].startswith(f"already applied on {NET} at ")
    assert len(again["reason"].rsplit(" at ", 1)[1]) == 5  # HH:MM
