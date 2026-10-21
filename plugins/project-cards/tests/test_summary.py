"""summary.py: the session summary text from the applied cards, the draft and the plugin's history entries."""

from datetime import datetime

from netstead_project_cards.summary import build_summary

NOW = datetime(2026, 10, 21, 9, 30, 15, 123456)
NETWORK = {"name": "leavenworth", "source": "/data/leavenworth", "spec_version": "0.1"}


def test_an_empty_session():
    text = build_summary(None, [], None, [], NOW)
    assert text.startswith("# Project cards: session summary\n\nNo network open.\nDate: 2026-10-21T09:30:15\n")
    assert "## Applied cards\n\nnone\n" in text and "## Draft card\n\nnone\n" in text
    assert "## Actions\n\nnone\n" in text and text.rstrip().endswith("after project_cards.open_catalog.")


def test_a_session_with_cards_a_draft_and_actions():
    applied = [
        {
            "card_id": "speed.yml",
            "path": "/cards/speed.yml",
            "project": "Speed fixes",
            "applied_at": "2026-10-21T09:00:00",
            "changes": 2,
            "source": "catalog",
            "edit_ids": [3, 4],
        },
        {
            "card_id": "one",
            "path": "/cards/one.yml",
            "project": "",
            "changes": 1,
            "source": "committed",
            "edit_ids": [],
        },
    ]
    draft = {"project": "Widening", "changes": ["roadway_property_change link 1: lanes → 3"]}
    history = [
        {
            "seq": 2,
            "ts": NOW.timestamp(),
            "ok": True,
            "error": None,
            "action": {"type": "project_cards.open_catalog", "path": "/cards", "net_id": None},
        },
        {
            "seq": 3,
            "ts": NOW.timestamp(),
            "ok": False,
            "error": "link 999999 not found",
            "action": {"type": "project_cards.apply_card", "card_id": "bad.yml", "ignore_missing": False},
        },
    ]
    text = build_summary(NETWORK, applied, draft, history, NOW)
    assert "Network: leavenworth\nSource: /data/leavenworth\nSpec version: 0.1\n" in text
    assert "- Speed fixes (speed.yml, catalog): 2 changes, edit ids 3, 4; /cards/speed.yml\n" in text
    assert "- one (one, committed): 1 change, edit ids none; /cards/one.yml\n" in text
    assert "## Draft card\n\nWidening:\n- roadway_property_change link 1: lanes → 3\n" in text
    assert "- 2. 09:30:15 project_cards.open_catalog path='/cards' (ok)\n" in text
    assert "- 3. 09:30:15 project_cards.apply_card card_id='bad.yml' (failed: link 999999 not found)\n" in text


def test_long_field_values_are_shortened():
    history = [{"seq": 1, "ts": None, "ok": True, "action": {"type": "project_cards.open_catalog", "path": "x" * 100}}]
    text = build_summary(NETWORK, [], {"project": "p", "changes": []}, history, NOW)
    assert "path='" + "x" * 58 + "…" in text and "## Draft card\n\nnone\n" in text  # a draft without changes: none
