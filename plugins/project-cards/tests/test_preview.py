"""project_cards.preview applies a change to a derived copy and shows it; the base network is untouched."""

import pytest
from netstead.fixtures import leavenworth
from netstead.workbench.actions import CloseNetwork, OpenNetwork, Select
from netstead.workbench.errors import ActionError, NotSupportedYet
from netstead_project_cards.actions import AddChange, ClosePreview, PreviewChange
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)

NET = "leavenworth"
LANES_3 = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=3)})
LINK_2_LANES_4 = RoadwayPropertyChange(facility=Selection(ids=[2]), property_changes={"lanes": PropertyChange(set=4)})


def lanes(session, net_id, key=1):
    links = session.network(net_id).links_df()
    return links.loc[links.link_id == key, "lanes"].tolist()


def preview_state(session):
    return session.state()["plugins"]["project_cards"]["preview"]


def test_a_preview_is_a_derived_network_made_active(session):
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    preview = result["preview"]
    assert preview != NET
    assert session.active == preview
    assert result["base"] == NET
    assert lanes(session, preview) == [3]
    assert lanes(session, NET) == [1]
    assert session.network(NET).version == 0
    assert session.network(preview).derived_from == NET
    assert preview_state(session) == {
        "net_id": preview,
        "base": NET,
        "describe": "roadway_property_change Link list: 1 link (GMNS): lanes → 3",
        "stale": False,
    }


def test_adding_while_previewing_changes_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    result = session.dispatch(AddChange(use_selection=True, change=LANES_3))
    assert result["net_id"] == NET
    assert lanes(session, NET) == [3]


def test_a_selection_made_on_the_preview_adds_to_the_base(session):
    preview = session.dispatch(PreviewChange(change=LINK_2_LANES_4))["preview"]
    session.dispatch(Select(link_ids=[1]))  # lands on the active network: the preview
    assert session.selection["net_id"] == preview
    result = session.dispatch(AddChange(use_selection=True, change=LANES_3))
    assert result["net_id"] == NET
    assert lanes(session, NET) == [3]
    assert lanes(session, preview) == [1]


def test_a_selection_made_on_the_preview_previews_again(session):
    first = session.dispatch(PreviewChange(change=LINK_2_LANES_4))["preview"]
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    assert result["base"] == NET
    assert first not in session.registry.ids()
    assert set(session.registry.ids()) == {NET, result["preview"]}
    assert lanes(session, result["preview"]) == [3]


def test_a_picked_link_only_the_preview_has_fails_on_the_base(session):
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}
    link = {
        "link_id": 9001,
        "from_node_id": 1,
        "to_node_id": 9001,
        "name": "New",
        "facility_type": "residential",
        "lanes": 1,
        "allowed_uses": "auto",
    }
    session.dispatch(PreviewChange(change=RoadwayAddition(nodes=[node], links=[link])))
    session.dispatch(Select(link_ids=[9001]))
    with pytest.raises(ActionError, match="not found"):
        session.dispatch(AddChange(use_selection=True, change=LANES_3))
    assert session.network(NET).version == 0


def test_a_selection_on_another_network_is_still_refused(session):
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir())))  # leavenworth-2, now active
    session.dispatch(Select(link_ids=[1]))
    with pytest.raises(ActionError, match="is on network 'leavenworth-2'"):
        session.dispatch(AddChange(net_id=NET, use_selection=True, change=LANES_3))


def test_close_preview_returns_to_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    preview = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert session.dispatch(ClosePreview()) == {"closed": preview, "active": NET}
    assert preview not in session.registry.ids()
    assert preview_state(session) is None
    with pytest.raises(ActionError, match="no preview is open"):
        session.dispatch(ClosePreview())


def test_a_second_preview_replaces_the_first(session):
    session.dispatch(Select(link_ids=[1]))
    first = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    second = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert first not in session.registry.ids()
    assert set(session.registry.ids()) == {NET, second}


def test_a_failing_preview_leaves_nothing_behind(session):
    missing = RoadwayPropertyChange(facility=Selection(ids=[999999]), property_changes={"lanes": PropertyChange(set=3)})
    with pytest.raises(ActionError, match="not found"):
        session.dispatch(PreviewChange(change=missing))
    assert session.registry.ids() == [NET]
    assert session.active == NET


def test_a_preview_closed_from_core_cannot_be_closed_again(session):
    session.dispatch(Select(link_ids=[1]))
    preview = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    session.dispatch(CloseNetwork(net_id=preview))
    with pytest.raises(ActionError, match="no preview is open"):
        session.dispatch(ClosePreview())
    assert session.active == NET


def test_a_transit_preview_is_not_supported_yet(session):
    with pytest.raises(NotSupportedYet):
        session.dispatch(PreviewChange(change=TransitPropertyChange()))
    assert session.registry.ids() == [NET]


# ---------------------------------------------------------------------- the base goes, or moves


def close_base_and_reopen(session):
    session.dispatch(CloseNetwork(net_id=NET))
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir()), net_id=NET))  # the same id, a new network


def test_a_preview_does_not_redirect_to_a_reopened_network_with_its_bases_id(session, no_state_reads):
    no_state_reads(session)  # only the handlers see the preview: nothing has dropped it yet
    preview = session.dispatch(PreviewChange(change=LINK_2_LANES_4))["preview"]
    close_base_and_reopen(session)
    with pytest.raises(ActionError, match="the network this preview was made from is closed"):
        session.dispatch(AddChange(net_id=preview, change=LINK_2_LANES_4))
    assert session.network(NET).version == 0


def test_closing_a_preview_whose_base_is_gone_does_not_activate_the_reopened_network(session, no_state_reads):
    no_state_reads(session)
    preview = session.dispatch(PreviewChange(change=LINK_2_LANES_4))["preview"]
    close_base_and_reopen(session)
    session.dispatch({"type": "set_active_network", "net_id": preview})
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir())))  # leavenworth-2 ...
    session.dispatch({"type": "set_active_network", "net_id": "leavenworth-2"})  # ... is what the user looks at
    assert session.dispatch(ClosePreview()) == {"closed": preview, "active": "leavenworth-2"}
    assert preview not in session.registry.ids()


def test_a_selection_on_a_preview_whose_base_is_gone_does_not_reach_the_reopened_network(session, no_state_reads):
    no_state_reads(session)
    preview = session.dispatch(PreviewChange(change=LINK_2_LANES_4))["preview"]
    close_base_and_reopen(session)
    session.dispatch({"type": "set_active_network", "net_id": preview})
    session.dispatch(Select(link_ids=[1]))
    with pytest.raises(ActionError, match="the selection is on network"):
        session.dispatch(AddChange(net_id=NET, use_selection=True, change=LANES_3))
    assert session.network(NET).version == 0


def test_the_state_drops_a_preview_whose_base_is_gone(session):
    session.dispatch(PreviewChange(change=LINK_2_LANES_4))
    close_base_and_reopen(session)
    assert preview_state(session) is None


def test_a_change_to_the_base_marks_the_preview_stale(session):
    session.dispatch(PreviewChange(change=LINK_2_LANES_4))
    assert preview_state(session)["stale"] is False
    session.dispatch(AddChange(change=LANES_3.model_copy(update={"facility": Selection(ids=[1])})))
    assert preview_state(session)["stale"] is True
