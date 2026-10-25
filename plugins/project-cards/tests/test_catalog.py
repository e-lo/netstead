"""catalog.py: scanning a folder of cards, and checking each against the open network without mutating it."""

from __future__ import annotations

from pathlib import Path

import pytest
from netstead_project_cards.card import DraftCard, read_card_file
from netstead_project_cards.catalog import applicability, card_targets, scan_catalog
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)

NET = "leavenworth"
QUERY = {"links": {"name": ["Benton Street"], "ignore_missing": True}}


def lanes(*ids, value=3, ignore_missing=False):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids), ignore_missing=ignore_missing),
        property_changes={"lanes": PropertyChange(set=value)},
    )


def write(card: DraftCard, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card.to_yaml(), encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """Two cards (one applies, one names link 999999, in a sub-folder), a non-card YAML, a hidden card, a stray file."""
    root = tmp_path / "catalog"
    write(DraftCard(project="Good", tags=["fix"], changes=[lanes(1), lanes(2, 3)]), root / "good.yml")
    write(DraftCard(project="Bad", changes=[lanes(999999)]), root / "sub" / "bad.yaml")
    (root / "notes.yml").write_text("just: notes\n", encoding="utf-8")
    (root / ".draft.yml").write_text("project: hidden\n", encoding="utf-8")
    (root / "readme.txt").write_text("not a card", encoding="utf-8")
    (root / "sub" / "deeper").mkdir()
    write(DraftCard(project="Too deep", changes=[lanes(1)]), root / "sub" / "deeper" / "deep.yml")
    return root


@pytest.fixture
def frames(session):
    handle = session.network(NET)
    return handle.links_df(), handle.nodes_df()


def test_scan_lists_cards_one_level_deep_and_reports_what_is_not_a_card(folder, host):
    catalog = scan_catalog(str(folder), host.list_dir, read_card_file)
    assert catalog.path == str(folder)
    assert [(c.card_id, c.project, c.tags, c.change_count, c.applicability) for c in catalog.cards] == [
        ("sub/bad.yaml", "Bad", [], 1, None),
        ("good.yml", "Good", ["fix"], 2, None),
    ]
    assert catalog.cards[1].path == str(folder / "good.yml")
    assert [(r["card_id"], r["path"]) for r in catalog.rejected] == [("notes.yml", str(folder / "notes.yml"))]
    assert "project" in catalog.rejected[0]["error"]
    assert catalog.entry("good.yml") is catalog.cards[1] and catalog.entry("nope") is None
    assert catalog.to_dict()["cards"][1] == {
        "card_id": "good.yml",
        "path": str(folder / "good.yml"),
        "project": "Good",
        "tags": ["fix"],
        "change_count": 2,
        "applicability": None,
        "fit": {"state": "unchecked", "label": "Not checked", "reasons": [], "warnings": [], "net_id": None},
    }


def test_scan_reports_an_unreadable_file_rather_than_failing(folder, host):
    def read(path):
        if path.endswith("good.yml"):
            raise OSError(13, "Permission denied")
        return read_card_file(path)

    catalog = scan_catalog(str(folder), host.list_dir, read)
    assert [c.card_id for c in catalog.cards] == ["sub/bad.yaml"]
    assert [r["card_id"] for r in catalog.rejected] == ["good.yml", "notes.yml"]
    assert catalog.rejected[0]["error"] == "[Errno 13] Permission denied"


def test_a_card_whose_ids_exist_applies(frames):
    got = applicability(DraftCard(project="Good", changes=[lanes(1), lanes(2, 3)]), *frames)
    assert got == {
        "ok": True,
        "changes": [
            {
                "describe": "roadway_property_change Link list: 1 link (GMNS): lanes → 3",
                "resolved": 1,
                "missing": [],
                "reason": None,
            },
            {
                "describe": "roadway_property_change Link list: 2 links (GMNS): lanes → 3",
                "resolved": 2,
                "missing": [],
                "reason": None,
            },
        ],
    }


def test_a_missing_id_makes_the_card_inapplicable_unless_the_selection_ignores_it(frames):
    got = applicability(DraftCard(project="Bad", changes=[lanes(1, 999999)]), *frames)
    assert got["ok"] is False and got["changes"][0]["missing"] == [999999] and got["changes"][0]["resolved"] == 1
    tolerant = applicability(DraftCard(project="Meh", changes=[lanes(1, 999999, ignore_missing=True)]), *frames)
    assert tolerant["ok"] is True and tolerant["changes"][0]["missing"] == [999999]


def test_a_query_without_provenance_has_a_reason(frames):
    unresolved = RoadwayPropertyChange(
        facility=Selection(query=QUERY), property_changes={"lanes": PropertyChange(set=2)}
    )
    got = applicability(DraftCard(project="q", changes=[unresolved]), *frames)
    assert got["ok"] is False
    assert got["changes"][0]["reason"] == "query without provenance; re-resolution not supported"
    with_provenance = unresolved.model_copy(update={"facility": Selection(query=QUERY, resolved=[1, 5, 999999])})
    got = applicability(DraftCard(project="q", changes=[with_provenance]), *frames)
    assert got["ok"] is True and got["changes"][0] == {
        "describe": "roadway_property_change Segment: Benton Street (GMNS): lanes → 2",
        "resolved": 2,
        "missing": [999999],  # the query's ignore_missing is true: skipped, not refused
        "reason": None,
    }


def test_deletions_resolve_links_and_nodes(frames):
    deletion = RoadwayDeletion(links=[1, 999999], nodes=[1, 888888], ignore_missing=True)
    got = applicability(DraftCard(project="d", changes=[deletion]), *frames)
    assert got["ok"] is False  # a node is always aimed at a specific record
    assert got["changes"][0]["resolved"] == 2 and got["changes"][0]["missing"] == [999999, 888888]
    only_links = RoadwayDeletion(links=[1, 999999], ignore_missing=True)
    assert applicability(DraftCard(project="d", changes=[only_links]), *frames)["ok"] is True


def test_additions_need_free_ids_and_present_end_nodes(frames):
    node = {"node_id": 900001, "x_coord": 0.0, "y_coord": 0.0}
    link = {"link_id": 900001, "from_node_id": 900001, "to_node_id": 1}
    fine = RoadwayAddition(links=[link], nodes=[node])
    got = applicability(DraftCard(project="a", changes=[fine]), *frames)
    assert got["ok"] is True and got["changes"][0]["resolved"] == 2
    taken = RoadwayAddition(links=[{**link, "link_id": 1}])
    assert applicability(DraftCard(project="a", changes=[taken]), *frames)["changes"][0]["reason"] == (
        "link 1 already exists"
    )
    dangling = RoadwayAddition(links=[link])  # node 900001 is not added with it
    reason = applicability(DraftCard(project="a", changes=[dangling]), *frames)["changes"][0]["reason"]
    assert reason == "added links use node(s) 900001, which are not in the network"


def test_transit_and_incomplete_changes_have_reasons(frames):
    transit = TransitPropertyChange()
    no_facility = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)})
    got = applicability(DraftCard(project="t", changes=[transit, no_facility]), *frames)
    assert got["ok"] is False
    assert got["changes"][0]["reason"].startswith("not yet supported: transit")
    assert got["changes"][1]["reason"].startswith("a roadway_property_change needs a facility")


def test_card_targets_is_the_union_of_the_ids_the_changes_aim_at():
    card = DraftCard(
        project="u",
        changes=[
            lanes(1, 2),
            lanes(2, 3),
            RoadwayDeletion(links=[4], nodes=[7]),
            RoadwayPropertyChange(
                facility=Selection(table="node", ids=[8]), property_changes={"x_coord": PropertyChange(set=1)}
            ),
            RoadwayAddition(nodes=[{"node_id": 99, "x_coord": 0, "y_coord": 0}]),
            RoadwayPropertyChange(facility=Selection(query=QUERY), property_changes={"lanes": PropertyChange(set=2)}),
        ],
    )
    assert card_targets(card) == {"links": [1, 2, 3, 4], "nodes": [7, 8]}
