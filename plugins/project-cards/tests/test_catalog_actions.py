"""project_cards.open_catalog / refresh_catalog / check_card / preview_card / apply_card / session_summary."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar, Literal

import pytest
from netstead.workbench.actions import CloseNetwork, EditCells
from netstead.workbench.errors import ActionError, NotSupportedYet, PathNotAllowed
from netstead.workbench.plugins import BaseAction
from netstead_project_cards.actions import (
    AddChange,
    ApplyCard,
    CheckCard,
    ClosePreview,
    CommitCard,
    OpenCatalog,
    PreviewCard,
    RefreshCatalog,
    SessionSummary,
    UndoChange,
)
from netstead_project_cards.card import DraftCard
from netstead_project_cards.types import PropertyChange, RoadwayDeletion, RoadwayPropertyChange, Selection

NET = "leavenworth"


class Wrap(BaseAction):
    """A test plugin's Action: apply the good card, then fail (so the apply is rolled back)."""

    type: Literal["wrap.apply"] = "wrap.apply"
    mutates: ClassVar[bool] = True


def lanes(*ids, value=3, existing=None, conflict="error", ignore_missing=False):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids), ignore_missing=ignore_missing),
        property_changes={"lanes": PropertyChange(set=value, existing=existing, existing_value_conflict=conflict)},
    )


def write(card: DraftCard, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card.to_yaml(), encoding="utf-8")
    return path


def cell(session, key, column, net_id=NET):
    links = session.network(net_id).links_df()
    return links.loc[links.link_id == key, column].tolist()


def cards_state(session):
    return session.state()["plugins"]["project_cards"]


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    write(DraftCard(project="Good", tags=["fix"], changes=[lanes(1), lanes(2, 3, value=4)]), root / "good.yml")
    write(DraftCard(project="Bad", changes=[lanes(1), lanes(999999)]), root / "bad.yml")
    (root / "notes.yml").write_text("just: notes\n", encoding="utf-8")
    return root


@pytest.fixture
def published(session):
    events = []
    session.events.publish = events.append
    return events


def test_open_catalog_lists_the_cards_and_the_rejected_files(session, folder):
    result = session.dispatch(OpenCatalog(path=str(folder)))
    assert result == {"path": str(folder), "cards": 2, "rejected": 1}
    catalog = cards_state(session)["catalog"]
    assert catalog["path"] == str(folder) and len(catalog["cards"]) == 2
    assert [c["card_id"] for c in catalog["cards"]] == ["bad.yml", "good.yml"]
    assert catalog["cards"][1] == {
        "card_id": "good.yml",
        "path": str(folder / "good.yml"),
        "project": "Good",
        "tags": ["fix"],
        "change_count": 2,
        "applicability": None,
    }
    assert [r["card_id"] for r in catalog["rejected"]] == ["notes.yml"]
    assert session.network(NET).version == 0  # nothing on the network changed
    assert session.history[-1].python == f"app.do(OpenCatalog(path={str(folder)!r}))"


def test_open_catalog_refusals(session, tmp_path):
    with pytest.raises(PathNotAllowed):
        session.dispatch(OpenCatalog(path="/etc"))
    with pytest.raises(ActionError, match="no such file or folder"):
        session.dispatch(OpenCatalog(path=str(tmp_path / "nope")))
    (tmp_path / "file.yml").write_text("project: x\n", encoding="utf-8")
    with pytest.raises(ActionError, match="is not a folder"):
        session.dispatch(OpenCatalog(path=str(tmp_path / "file.yml")))
    assert cards_state(session)["catalog"] is None


def test_refresh_rescans_the_folder_and_forgets_the_checks(session, folder):
    with pytest.raises(ActionError, match="no catalog is open"):
        session.dispatch(RefreshCatalog())
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(CheckCard(card_id="good.yml"))
    assert cards_state(session)["catalog"]["cards"][1]["applicability"]["ok"] is True
    write(DraftCard(project="New", changes=[lanes(4)]), folder / "new.yml")
    (folder / "bad.yml").unlink()
    assert session.dispatch(RefreshCatalog()) == {"path": str(folder), "cards": 2, "rejected": 1}
    cards = cards_state(session)["catalog"]["cards"]
    assert [(c["card_id"], c["applicability"]) for c in cards] == [("good.yml", None), ("new.yml", None)]


def test_check_card_reports_applicability_without_changing_the_network(session, folder):
    session.dispatch(OpenCatalog(path=str(folder)))
    good = session.dispatch(CheckCard(card_id="good.yml"))
    assert good["card_id"] == "good.yml" and good["ok"] is True and good["net_id"] == NET and good["version"] == 0
    assert [c["resolved"] for c in good["changes"]] == [1, 2]
    bad = session.dispatch(CheckCard(card_id="bad.yml"))
    assert bad["ok"] is False and bad["changes"][1]["missing"] == [999999]
    assert session.network(NET).version == 0
    assert cards_state(session)["catalog"]["cards"][0]["applicability"]["ok"] is False
    with pytest.raises(ActionError, match="has no card 'nope'"):
        session.dispatch(CheckCard(card_id="nope"))


def test_check_card_needs_a_catalog_and_a_network(session, make_session, folder):
    with pytest.raises(ActionError, match="no catalog is open"):
        session.dispatch(CheckCard(card_id="good.yml"))
    empty = make_session(open_network=False)
    empty.dispatch(OpenCatalog(path=str(folder)))
    with pytest.raises(ActionError, match="no network is open"):
        empty.dispatch(CheckCard(card_id="good.yml"))


def test_apply_card_changes_the_network_and_records_it(session, folder, published):
    before = [cell(session, k, "lanes") for k in (1, 2, 3)]
    session.dispatch(OpenCatalog(path=str(folder)))
    result = session.dispatch(ApplyCard(card_id="good.yml"))
    assert result == {
        "card_id": "good.yml",
        "net_id": NET,
        "changes": 2,
        "edit_ids": [1, 2],
        "warnings": [],
        "skipped": [],
    }
    assert cell(session, 1, "lanes") == [3] and cell(session, 2, "lanes") == [4] and cell(session, 3, "lanes") == [4]
    state = cards_state(session)
    (applied,) = state["applied"]
    assert applied["source"] == "catalog" and applied["card_id"] == "good.yml" and applied["project"] == "Good"
    assert applied["path"] == str(folder / "good.yml") and applied["edit_ids"] == [1, 2] and applied["changes"] == 2
    assert applied["net_id"] == NET and applied["applied_at"].endswith("+00:00")
    assert NET not in state["drafts"]  # applied, not drafted: the card is already a file
    assert state["catalog"]["cards"][1]["applicability"] is None  # the network changed: checked again on demand
    assert [e["name"] for e in published if e.get("type") == "plugin"] == ["catalog", "applied"]
    assert session.history[-1].python == "app.do(ApplyCard(card_id='good.yml'))"
    assert sorted(folder.iterdir()) == sorted([folder / "good.yml", folder / "bad.yml", folder / "notes.yml"])
    undone = session.dispatch(UndoChange())  # an applied card is undone as a whole, not as a draft change
    assert undone == {"net_id": NET, "undone": "card", "card_id": "good.yml", "edits": 2}
    assert [cell(session, k, "lanes") for k in (1, 2, 3)] == before
    assert cards_state(session)["applied"] == []
    assert [e for e in session.edits[NET].entries if e.source == "project_cards"] == []


def test_undo_change_refuses_a_core_edit_on_top_of_an_applied_card_then_undoes_the_card(session, folder):
    from netstead.workbench.actions import UndoEdit

    before = cell(session, 1, "lanes")
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(ApplyCard(card_id="good.yml"))
    session.dispatch(EditCells(ids=[5], values={"lanes": 9}))
    with pytest.raises(ActionError, match="was made by"):
        session.dispatch(UndoChange())
    assert cell(session, 1, "lanes") == [3] and cards_state(session)["applied"] != []  # the card is still there
    session.dispatch(UndoEdit())  # core undoes its own edit
    undone = session.dispatch(UndoChange())
    assert undone == {"net_id": NET, "undone": "card", "card_id": "good.yml", "edits": 2}
    assert cell(session, 1, "lanes") == before and cards_state(session)["applied"] == []


def test_undo_change_undoes_the_newest_plugin_change_first_card_then_draft(session, folder):
    before1, before5 = cell(session, 1, "lanes"), cell(session, 5, "lanes")
    session.dispatch(AddChange(change=lanes(5, value=2)))
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(ApplyCard(card_id="good.yml"))
    first = session.dispatch(UndoChange())
    assert first == {"net_id": NET, "undone": "card", "card_id": "good.yml", "edits": 2}
    assert cell(session, 1, "lanes") == before1 and cell(session, 5, "lanes") == [2]  # the draft change is still there
    second = session.dispatch(UndoChange())
    assert second["count"] == 0 and cell(session, 5, "lanes") == before5


def test_a_rolled_back_parent_restores_the_applied_card_after_undoing_it(make_session, folder):
    from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

    def wrap(host, action):
        host.dispatch(UndoChange())
        raise ActionError("the parent failed")

    wrapper = WorkbenchPlugin(
        id="wrap", name="W", version="0", requires_api=HOST_API, actions=(ActionSpec(Wrap, wrap),)
    )
    session = make_session(wrapper)
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(ApplyCard(card_id="good.yml"))
    assert not session.dispatch_recorded(Wrap()).ok
    state = cards_state(session)
    (applied,) = state["applied"]
    assert applied["card_id"] == "good.yml" and applied["edit_ids"] == [1, 2]
    assert cell(session, 1, "lanes") == [3] and cell(session, 2, "lanes") == [4]


def test_apply_card_refuses_a_card_with_a_missing_id_unless_told_to_skip_it(session, folder):
    session.dispatch(OpenCatalog(path=str(folder)))
    with pytest.raises(
        ActionError,
        match=r"bad\.yml does not apply to leavenworth: .*link 999999.*: missing 999999; apply with ignore_missing",
    ):
        session.dispatch(ApplyCard(card_id="bad.yml"))
    assert session.network(NET).version == 0 and cards_state(session)["applied"] == []
    with pytest.raises(ActionError, match="does not apply"):
        session.dispatch(ApplyCard(card_id="bad.yml", ignore_missing=False))
    result = session.dispatch(ApplyCard(card_id="bad.yml", ignore_missing=True))
    assert (
        result["changes"] == 2
        and result["edit_ids"] == [1]
        and result["skipped"] == ["link 999999 not found; skipped (ignore_missing)"]
    )
    assert cell(session, 1, "lanes") == [3]


def test_apply_card_always_refuses_a_query_without_provenance(session, folder):
    (folder / "query.yml").write_text(
        "project: q\nchanges:\n- roadway_property_change:\n    facility:\n      links:\n        name: [Benton Street]\n"
        "        ignore_missing: true\n    property_changes:\n      lanes: {set: 2}\n",
        encoding="utf-8",
    )
    session.dispatch(OpenCatalog(path=str(folder)))
    assert session.dispatch(CheckCard(card_id="query.yml"))["changes"][0]["reason"] == (
        "query without provenance; re-resolution not supported"
    )
    with pytest.raises(ActionError, match="query without provenance") as info:
        session.dispatch(ApplyCard(card_id="query.yml", ignore_missing=True))
    assert "ignore_missing=True" not in str(info.value)


def test_apply_card_overrides_the_cards_conflict_rule(session, folder):
    write(DraftCard(project="Stale", changes=[lanes(1, existing=7)]), folder / "stale.yml")
    session.dispatch(OpenCatalog(path=str(folder)))
    with pytest.raises(
        ActionError, match=r"does not apply to .*: conflict: lanes: expected 7, found 1 on 1 of 1 links"
    ):
        session.dispatch(ApplyCard(card_id="stale.yml"))
    result = session.dispatch(ApplyCard(card_id="stale.yml", existing_value_conflict="warn"))
    assert result["warnings"] == ["lanes: expected 7 on link 1, found 1"] and cell(session, 1, "lanes") == [3]
    skipped = session.dispatch(ApplyCard(card_id="stale.yml", existing_value_conflict="skip"))
    assert skipped["edit_ids"] == [] and skipped["skipped"] == ["lanes: expected 7 on link 1, found 3"]


def test_apply_card_is_all_or_nothing(session, folder):
    write(DraftCard(project="Half", changes=[lanes(1), RoadwayDeletion(nodes=[1])]), folder / "half.yml")
    session.dispatch(OpenCatalog(path=str(folder)))
    with pytest.raises(ActionError, match="change 2 of 2: node 1 is still used"):
        session.dispatch(ApplyCard(card_id="half.yml"))
    assert cell(session, 1, "lanes") == [1] and session.network(NET).version == 0
    assert cards_state(session)["applied"] == []


def test_a_rolled_back_parent_takes_the_applied_record_back(make_session, folder):

    from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

    def wrap(host, action):
        host.dispatch(ApplyCard(card_id="good.yml"))
        raise ActionError("the parent failed")

    wrapper = WorkbenchPlugin(
        id="wrap", name="W", version="0", requires_api=HOST_API, actions=(ActionSpec(Wrap, wrap),)
    )
    session = make_session(wrapper)
    session.dispatch(OpenCatalog(path=str(folder)))
    assert not session.dispatch_recorded(Wrap()).ok
    assert cards_state(session)["applied"] == [] and cell(session, 1, "lanes") == [1]


def test_apply_card_on_the_preview_targets_its_base(session, folder):
    session.dispatch(OpenCatalog(path=str(folder)))
    preview = session.dispatch(PreviewCard(card_id="good.yml"))["preview"]
    assert session.active == preview
    result = session.dispatch(ApplyCard(card_id="good.yml"))
    assert result["net_id"] == NET and cell(session, 1, "lanes") == [3]


def test_preview_card_shows_every_change_on_a_copy_and_publishes_the_targets(session, folder, published):
    session.dispatch(OpenCatalog(path=str(folder)))
    result = session.dispatch(PreviewCard(card_id="good.yml"))
    preview = result["preview"]
    assert result["base"] == NET and result["card_id"] == "good.yml" and result["warnings"] == []
    assert [c["describe"] for c in result["changes"]] == [
        "roadway_property_change link 1: lanes → 3",
        "roadway_property_change link 2, 3: lanes → 4",
    ]
    assert session.active == preview and cell(session, 1, "lanes", preview) == [3] and cell(session, 1, "lanes") == [1]
    assert cards_state(session)["preview"] == {
        "net_id": preview,
        "base": NET,
        "describe": "card good.yml: Good",
        "stale": False,
    }
    selection = [e for e in published if e.get("type") == "plugin" and e["name"] == "card_selection"]
    assert selection == [
        {
            "type": "plugin",
            "plugin": "project_cards",
            "name": "card_selection",
            "payload": {"card_id": "good.yml", "links": [1, 2, 3], "nodes": []},
        }
    ]
    session.dispatch(ClosePreview())
    assert session.active == NET and preview not in session.registry.ids()


def test_a_preview_that_fails_closes_its_copy(session, folder):
    session.dispatch(OpenCatalog(path=str(folder)))
    with pytest.raises(ActionError, match="change 2 of 2: link 999999 not found"):
        session.dispatch(PreviewCard(card_id="bad.yml"))
    assert session.registry.ids() == [NET] and cards_state(session)["preview"] is None


def test_a_second_card_preview_replaces_the_first(session, folder):
    session.dispatch(OpenCatalog(path=str(folder)))
    first = session.dispatch(PreviewCard(card_id="good.yml"))["preview"]
    second = session.dispatch(PreviewCard(card_id="good.yml"))["preview"]
    assert first != second and session.registry.ids() == [NET, second]


def test_transit_cards_are_refused_as_not_supported(session, folder):
    (folder / "transit.yml").write_text(
        "project: t\nchanges:\n- transit_property_change:\n    service: {}\n"
        "    property_changes: {headway_secs: {set: 600}}\n",
        encoding="utf-8",
    )
    session.dispatch(OpenCatalog(path=str(folder)))
    assert [r["card_id"] for r in cards_state(session)["catalog"]["rejected"]] == ["notes.yml", "transit.yml"]


def test_session_summary_names_the_network_the_cards_and_the_actions(session, folder, tmp_path):
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(ApplyCard(card_id="good.yml"))
    session.dispatch(AddChange(change=lanes(5, value=2)))
    session.dispatch(CommitCard(path=str(tmp_path / "widen.yml"), project="Widening"))
    session.dispatch(AddChange(change=lanes(6, value=2)))
    session.dispatch(EditCells(ids=[7], values={"lanes": 2}))  # core's: not a project_cards action
    with pytest.raises(ActionError):
        session.dispatch(ApplyCard(card_id="bad.yml"))
    text = session.dispatch(SessionSummary())["text"]
    assert text.startswith("# Project cards: session summary\n\nNetwork: leavenworth\nSource: ")
    assert "Spec version: " in text
    assert "- Good (good.yml, catalog): 2 changes, edit ids 1, 2; " in text
    assert "- Widening (widening, committed): 1 change, edit ids 3; " in text
    assert "## Draft card\n\n(unnamed):\n- roadway_property_change link 6: lanes → 2\n" in text
    actions = text.split("## Actions\n\n")[1].split("\n\n")[0].splitlines()
    assert [line.split(" ")[3] for line in actions] == [
        "project_cards.open_catalog",
        "project_cards.apply_card",
        "project_cards.add_change",
        "project_cards.commit",
        "project_cards.add_change",
        "project_cards.apply_card",
    ]
    assert actions[1].endswith("(ok)") and "(failed: bad.yml does not apply" in actions[5]
    assert "Replay:" in text


def test_session_summary_without_a_network(make_session):
    session = make_session(open_network=False)
    text = session.dispatch(SessionSummary())["text"]
    assert "No network open." in text and "## Applied cards\n\nnone\n" in text


def test_session_summary_for_a_closed_network_says_so(session):
    session.dispatch(CloseNetwork(net_id=NET))
    assert "No network open." in session.dispatch(SessionSummary(net_id=NET))["text"]


def test_the_catalog_dir_setting_opens_a_catalog_on_load(make_session, folder, tmp_path, caplog):
    session = make_session(overrides={"plugins.project_cards.catalog_dir": str(folder)})
    catalog = cards_state(session)["catalog"]
    assert catalog["path"] == str(folder) and len(catalog["cards"]) == 2
    with caplog.at_level(logging.WARNING):
        other = make_session(overrides={"plugins.project_cards.catalog_dir": str(tmp_path / "nope")})
    assert cards_state(other)["catalog"] is None and "catalog_dir" in caplog.text and "nope" in caplog.text


def test_a_transit_change_in_the_api_is_refused(session, folder):
    from netstead_project_cards.types import TransitPropertyChange

    with pytest.raises(NotSupportedYet):
        session.dispatch(AddChange(change=TransitPropertyChange()))
