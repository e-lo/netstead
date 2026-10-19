"""project_cards.preview applies a change to a derived copy and shows it; the base network is untouched."""

import pytest
from netstead.workbench.actions import CloseNetwork, Select
from netstead.workbench.errors import ActionError, NotSupportedYet
from netstead_project_cards.actions import AddChange, ClosePreview, PreviewChange
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection, TransitPropertyChange

NET = "leavenworth"
LANES_3 = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=3)})


def lanes(session, net_id, key=1):
    links = session.network(net_id).links_df()
    return links.loc[links.link_id == key, "lanes"].tolist()


def test_a_preview_is_a_derived_network_made_active(session):
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    preview = result["preview"]
    assert preview != NET and session.active == preview and result["base"] == NET
    assert lanes(session, preview) == [3] and lanes(session, NET) == [1] and session.network(NET).version == 0
    assert session.network(preview).derived_from == NET
    assert session.state()["plugins"]["project_cards"]["preview"] == {
        "net_id": preview,
        "base": NET,
        "describe": "roadway_property_change link 1: lanes → 3",
    }


def test_adding_while_previewing_changes_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(PreviewChange(use_selection=True, change=LANES_3))
    result = session.dispatch(AddChange(use_selection=True, change=LANES_3))
    assert result["net_id"] == NET and lanes(session, NET) == [3]


def test_close_preview_returns_to_the_base(session):
    session.dispatch(Select(link_ids=[1]))
    preview = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert session.dispatch(ClosePreview()) == {"closed": preview, "active": NET}
    assert preview not in session.registry.ids() and session.state()["plugins"]["project_cards"]["preview"] is None
    with pytest.raises(ActionError, match="no preview is open"):
        session.dispatch(ClosePreview())


def test_a_second_preview_replaces_the_first(session):
    session.dispatch(Select(link_ids=[1]))
    first = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    second = session.dispatch(PreviewChange(use_selection=True, change=LANES_3))["preview"]
    assert first not in session.registry.ids() and set(session.registry.ids()) == {NET, second}


def test_a_failing_preview_leaves_nothing_behind(session):
    missing = RoadwayPropertyChange(facility=Selection(ids=[999999]), property_changes={"lanes": PropertyChange(set=3)})
    with pytest.raises(ActionError, match="not found"):
        session.dispatch(PreviewChange(change=missing))
    assert session.registry.ids() == [NET] and session.active == NET


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
