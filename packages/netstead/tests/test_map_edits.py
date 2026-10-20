"""Tests for :mod:`netstead.map.edits` — edit-log Phase 1.

The edit log is the bridge from the interactive viewer (which collects
proposed fixes per user click) to the Python world (which applies them
deterministically and saves). The format is intentionally simple — a
``list[Edit]`` per session, YAML on disk, PK-indexed so it survives row
reordering.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import netstead.map
import pandas as pd
import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead import Network
from netstead.map.edits import Edit, EditLog, apply_edits, dump_edit_log, load_edit_log


@pytest.fixture
def tiny_net(tmp_path) -> Network:
    """Small network with a known free_speed column we can test edits against."""
    link = pd.DataFrame(
        {
            "link_id": [1, 2, 3],
            "from_node_id": [1, 2, 3],
            "to_node_id": [2, 3, 1],
            "directed": [True, True, True],
            "length": [100.0, 200.0, 150.0],
            "free_speed": [40.0, 35.0, None],
        }
    )
    node = pd.DataFrame(
        {
            "node_id": [1, 2, 3],
            "x_coord": [-120.6, -120.5, -120.4],
            "y_coord": [47.5, 47.6, 47.7],
        }
    )
    csv = tmp_path / "n"
    csv.mkdir()
    link.to_csv(csv / "link.csv", index=False)
    node.to_csv(csv / "node.csv", index=False)
    return Network.from_source(csv, engine=IbisEngine())


# ---------------------------------------------------------------------------
# Edit + EditLog data types
# ---------------------------------------------------------------------------


def test_edit_carries_pk_not_positional_row():
    """An Edit is identified by its primary-key dict, not by a positional row.

    Positional row indices aren't stable across re-runs (re-orders,
    deletes, partial loads). PKs survive.
    """
    e = Edit(
        id="e1",
        kind="fix",
        table="link",
        pk={"link_id": 42},
        column="free_speed",
        from_value=None,
        to_value=25,
    )
    assert e.pk == {"link_id": 42}
    assert e.kind == "fix"


def test_edit_log_is_serialisable_roundtrip_via_yaml(tmp_path):
    """An EditLog round-trips through YAML on disk unchanged."""
    log = EditLog(
        source="./tiny",
        spec_version="0.97",
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 3},
                column="free_speed",
                from_value=None,
                to_value=25,
                reason="schema.required",
                issue_id="i0",
            )
        ],
    )
    path = tmp_path / "edits.yaml"
    dump_edit_log(log, path)
    loaded = load_edit_log(path)
    assert loaded.source == log.source
    assert loaded.spec_version == "0.97"
    assert len(loaded.edits) == 1
    assert loaded.edits[0].pk == {"link_id": 3}
    assert loaded.edits[0].to_value == 25


def test_dump_edit_log_emits_projectcard_shape(tmp_path):
    """The on-disk YAML is a network-wrangler ProjectCard.

    - Top-level ``project`` key (required by ProjectCard schema)
    - Top-level ``changes`` array
    - Each change wraps a ``roadway_property_change`` with a facility
      selector (``model_link_id`` for link edits, ``model_node_id``
      for node edits) and ``property_changes`` values with ``existing``
      (drift check) + ``set`` (new value).
    """
    import yaml

    log = EditLog(
        source="./tiny",
        spec_version="0.97",
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 42},
                column="free_speed",
                from_value=40,
                to_value=25,
                reason="schema.required: free_speed missing",
                issue_id="i0",
            ),
            Edit(
                id="e2",
                kind="fix",
                table="node",
                pk={"node_id": 7},
                column="y_coord",
                from_value=None,
                to_value=47.6022,
            ),
        ],
    )
    path = tmp_path / "edits.yaml"
    dump_edit_log(log, path)
    data = yaml.safe_load(path.read_text())

    assert "project" in data, "ProjectCard requires a top-level 'project' key"
    assert isinstance(data.get("changes"), list) and len(data["changes"]) == 2

    first = data["changes"][0]["roadway_property_change"]
    # Clicked ids: a fix aimed at a specific link fails loudly when that link is gone.
    assert first["facility"] == {"links": {"model_link_id": [42], "ignore_missing": False}}
    assert first["property_changes"]["free_speed"]["existing"] == 40
    assert first["property_changes"]["free_speed"]["set"] == 25
    # A change cannot carry its own notes; they ride on the card's notes as netstead.note[<i>].
    assert "notes" not in first
    assert "netstead.note[0]: schema.required: free_speed missing · issue_id=i0 · edit_id=e1" in data["notes"]

    second = data["changes"][1]["roadway_property_change"]
    assert second["facility"] == {"nodes": {"model_node_id": [7], "ignore_missing": False}}
    # from_value=None → no drift key emitted (empty `existing` would be
    # ambiguous with "existing null" for a real column default).
    assert "existing" not in second["property_changes"]["y_coord"]
    assert second["property_changes"]["y_coord"]["set"] == 47.6022


def _sample_log() -> EditLog:
    return EditLog(
        source="./tiny",
        spec_version="0.97",
        created_at="2026-10-20T00:00:00+00:00",
        client="netstead.map",
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 42},
                column="free_speed",
                from_value=40,
                to_value=25,
                reason="schema.required:\n  free_speed missing",
                issue_id="i0",
            ),
            Edit(id="e2", kind="fix", table="node", pk={"node_id": 7}, column="y_coord", to_value=47.6022),
        ],
    )


def test_dump_edit_log_writes_a_schema_valid_projectcard(tmp_path):
    """The file passes projectcard's own validator, without schema defaults filled in.

    netstead core does not depend on ``projectcard``; the project-cards plugin does, and the workspace installs it.
    """
    import yaml

    projectcard = pytest.importorskip("projectcard")
    path = tmp_path / "edits.yaml"
    dump_edit_log(_sample_log(), path)
    card = yaml.safe_load(path.read_text())
    projectcard.validate_card(copy.deepcopy(card), parse_defaults=False)


def test_edit_reasons_round_trip_through_the_card_notes(tmp_path):
    """A multi-line reason folds onto one ``netstead.note[<i>]`` line; reason, issue id and edit id read back."""
    path = tmp_path / "edits.yaml"
    dump_edit_log(_sample_log(), path)
    loaded = load_edit_log(path)
    assert [(e.id, e.reason, e.issue_id) for e in loaded.edits] == [
        ("e1", "schema.required: free_speed missing", "i0"),
        ("e2", None, None),
    ]
    assert loaded.source == "./tiny" and loaded.client == "netstead.map"
    assert [(e.table, e.pk, e.from_value, e.to_value) for e in loaded.edits] == [
        ("link", {"link_id": 42}, 40, 25),
        ("node", {"node_id": 7}, None, 47.6022),
    ]


def test_dump_load_dump_is_stable(tmp_path):
    """Loading a log and dumping it again writes the same file: ids and issue ids are not folded into the reason."""
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"
    log = _sample_log()
    log.edits.append(
        Edit(id="e3", kind="fix", table="link", pk={"link_id": 1}, column="name", to_value="Rue Él", reason="accents")
    )
    dump_edit_log(log, first)
    dump_edit_log(load_edit_log(first), second)
    assert second.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")
    assert "Rue Él" in first.read_text(encoding="utf-8")  # allow_unicode: written as is, not escaped


def test_a_reason_that_looks_like_an_id_suffix_only_at_the_end_is_parsed(tmp_path):
    path = tmp_path / "edits.yaml"
    path.write_text(
        "project: p\n"
        "notes: 'netstead.note[0]: see edit_id=x · then · issue_id=i9 · edit_id=e7'\n"
        "changes:\n"
        "  - roadway_property_change:\n"
        "      facility: {links: {model_link_id: [5], ignore_missing: false}}\n"
        "      property_changes: {lanes: {set: 2}}\n"
    )
    (edit,) = load_edit_log(path).edits
    assert (edit.id, edit.reason, edit.issue_id) == ("e7", "see edit_id=x · then", "i9")


@pytest.mark.parametrize(
    "facility",
    [
        {"links": {"model_link_id": [1, 2], "lanes": 2, "ignore_missing": True}},  # ids narrowed by another key
        {"nodes": {"model_node_id": [1], "osm_node_id": "9", "ignore_missing": True}},
        {"links": {"model_link_id": [1, 2], "ignore_missing": True}, "from": {"model_node_id": 1}},  # a segment
        {"links": {"model_link_id": [1, 2], "ignore_missing": True}, "to": {"model_node_id": 3}},
    ],
)
def test_load_edit_log_skips_an_id_selector_that_is_not_only_ids(tmp_path, facility):
    """Only a selector of ids alone is a list of cells to edit; anything narrower yields no edits rather than all."""
    import yaml

    path = tmp_path / "narrow.yaml"
    card = {"project": "p", "changes": [{"roadway_property_change": {"facility": facility, "property_changes": {}}}]}
    card["changes"][0]["roadway_property_change"]["property_changes"] = {"lanes": {"set": 3}}
    path.write_text(yaml.safe_dump(card))
    assert load_edit_log(path).edits == []


def test_load_edit_log_still_reads_the_old_shape(tmp_path):
    """Logs written before the schema-valid writer (facility ids at the top, per-change notes) still replay."""
    path = tmp_path / "old.yaml"
    path.write_text(
        "project: netstead edits\n"
        "tags: [netstead, edit-log]\n"
        "notes: |\n"
        "  source: ./tiny\n"
        "changes:\n"
        "  - roadway_property_change:\n"
        "      facility:\n"
        "        model_node_id: [7]\n"
        "      property_changes:\n"
        "        y_coord:\n"
        "          existing: 47.5\n"
        "          set: 47.6\n"
        "      notes: 'moved · edit_id=e1'\n"
    )
    log = load_edit_log(path)
    (edit,) = log.edits
    assert (edit.table, edit.pk, edit.column, edit.from_value, edit.to_value) == (
        "node",
        {"node_id": 7},
        "y_coord",
        47.5,
        47.6,
    )
    assert (edit.reason, edit.id) == ("moved", "e1")
    assert log.source == "./tiny"


def test_load_edit_log_skips_a_query_facility(tmp_path):
    """A card selecting links by a query (name, ref, ...) is not a cell edit: it is skipped, not misread."""
    path = tmp_path / "query.yaml"
    path.write_text(
        "project: q\n"
        "changes:\n"
        "  - roadway_property_change:\n"
        "      facility:\n"
        "        links: {name: [Main Street], ignore_missing: true}\n"
        "      property_changes:\n"
        "        lanes: {set: 2}\n"
    )
    assert load_edit_log(path).edits == []


def test_the_browser_writer_emits_the_same_shape(tmp_path, run_node):
    """The offline report's "Fix locally" download (``buildYaml`` in map_component.js) writes the same card."""
    import yaml

    source = (Path(netstead.map.__file__).parent / "templates" / "map_component.js").read_text()
    funcs = re.search(r"^ *function buildYaml\(\) \{.*?(?=^ *function downloadYaml\(\))", source, re.S | re.M)
    assert funcs, "buildYaml/yamlScalar not found in map_component.js"
    edits = [
        {
            "id": "e1",
            "pk": {"link_id": 42},
            "column": "free_speed",
            "from_value": 40,
            "to_value": 25,
            "reason": "schema.required: it's\nmissing",
            "issue_id": "i0",
        },
        {"id": "e2", "pk": {"node_id": 7}, "column": "y_coord", "from_value": None, "to_value": 47.6022},
        {"id": "e3", "pk": {"other": 1}, "column": "x", "to_value": 1},  # no facility key: skipped
        # NEL, a C0 separator, a BOM, a NUL and a C1 control: each would break the line or the YAML; all fold.
        {"id": "e4", "pk": {"link_id": 3}, "column": "lanes", "to_value": 2, "reason": "a\x85b\x1cc﻿d\x00e\x9ff"},
    ]
    script = tmp_path / "probe.js"
    script.write_text(
        f"function loadEditLog() {{ return {json.dumps(edits)}; }}\n"
        f"{funcs.group(0)}\nprocess.stdout.write(buildYaml());\n"
    )
    proc = run_node([str(script)])
    assert proc.returncode == 0, proc.stderr
    card = yaml.safe_load(proc.stdout)

    first, second, _ = (c["roadway_property_change"] for c in card["changes"])
    assert first == {
        "facility": {"links": {"model_link_id": [42], "ignore_missing": False}},
        "property_changes": {"free_speed": {"existing": 40, "set": 25}},
    }
    assert second["facility"] == {"nodes": {"model_node_id": [7], "ignore_missing": False}}
    browser_notes = [line for line in card["notes"].splitlines() if line.startswith("netstead.note")]
    assert browser_notes == [
        "netstead.note[0]: schema.required: it's missing · issue_id=i0 · edit_id=e1",
        "netstead.note[1]: edit_id=e2",
        "netstead.note[2]: a b c d e f · edit_id=e4",
    ]

    # The Python writer folds the same characters the same way.
    py_path = tmp_path / "python.yaml"
    dump_edit_log(EditLog(edits=[Edit(kind="fix", table="link", **e) for e in edits]), py_path)
    py_notes = yaml.safe_load(py_path.read_text(encoding="utf-8"))["notes"].splitlines()
    assert py_notes == browser_notes

    path = tmp_path / "browser.yaml"
    path.write_text(proc.stdout, encoding="utf-8")
    loaded = load_edit_log(path)
    assert [(e.pk, e.to_value) for e in loaded.edits] == [
        ({"link_id": 42}, 25),
        ({"node_id": 7}, 47.6022),
        ({"link_id": 3}, 2),
    ]
    assert loaded.client == "netstead.map (browser)"

    projectcard = pytest.importorskip("projectcard")
    projectcard.validate_card(copy.deepcopy(card), parse_defaults=False)


def test_load_edit_log_rejects_non_projectcard_shape(tmp_path):
    """Not a ProjectCard: no ``project`` key → loud error, not silent misparse."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("something_else:\n  - foo\n")
    with pytest.raises(ValueError, match="ProjectCard"):
        load_edit_log(bad)


def test_load_edit_log_reads_hand_written_projectcard(tmp_path):
    """A ProjectCard someone hand-wrote (no netstead metadata) still loads.

    Real network-wrangler workflows: a user hand-authored a card
    without ever touching the viewer. We still parse it and produce
    valid Edits that apply_edits can consume.
    """
    path = tmp_path / "hand.yaml"
    path.write_text(
        "project: hand-authored\n"
        "changes:\n"
        "  - roadway_property_change:\n"
        "      facility:\n"
        "        model_link_id: [5]\n"
        "      property_changes:\n"
        "        free_speed:\n"
        "          set: 30\n"
    )
    log = load_edit_log(path)
    assert len(log.edits) == 1
    assert log.edits[0].pk == {"link_id": 5}
    assert log.edits[0].column == "free_speed"
    assert log.edits[0].to_value == 30
    assert log.edits[0].from_value is None


# ---------------------------------------------------------------------------
# apply_edits — the deterministic replay path
# ---------------------------------------------------------------------------


def test_apply_edits_applies_a_simple_value_fix(tiny_net):
    """A fix that sets link[link_id=3].free_speed from null → 25 succeeds and mutates."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 3},
                column="free_speed",
                from_value=None,
                to_value=25,
            )
        ]
    )
    result = apply_edits(tiny_net, log)
    assert len(result.applied) == 1
    assert not result.skipped
    df = result.net.links.to_pandas()
    new_val = df[df["link_id"] == 3]["free_speed"].iloc[0]
    assert new_val == 25


def test_apply_edits_skips_when_pk_not_found(tiny_net):
    """A fix whose PK doesn't exist in the table is skipped with a reason."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 9999},
                column="free_speed",
                from_value=None,
                to_value=25,
            )
        ]
    )
    result = apply_edits(tiny_net, log)
    assert not result.applied
    assert len(result.skipped) == 1
    assert "pk" in result.skipped[0].reason.lower() or "found" in result.skipped[0].reason.lower()


def test_apply_edits_skips_on_value_drift(tiny_net):
    """If from_value doesn't match the table's current value, skip + report."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 1},
                column="free_speed",
                from_value=99,  # actual is 40
                to_value=25,
            )
        ]
    )
    result = apply_edits(tiny_net, log)
    assert not result.applied
    assert len(result.skipped) == 1
    assert "drift" in result.skipped[0].reason.lower() or "match" in result.skipped[0].reason.lower()


def test_apply_edits_with_from_value_none_skips_strict_check(tiny_net):
    """``from_value=None`` is treated as 'don't check' (the most common JS-side case)."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 1},
                column="free_speed",
                from_value=None,
                to_value=25,
            )
        ]
    )
    result = apply_edits(tiny_net, log)
    # link_id=1 had free_speed=40; from_value=None means "don't enforce", so apply succeeds.
    assert len(result.applied) == 1
    df = result.net.links.to_pandas()
    assert df[df["link_id"] == 1]["free_speed"].iloc[0] == 25


def test_apply_edits_unknown_table_is_skipped(tiny_net):
    """An edit pointing at a table the network doesn't have is skipped, not crash."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="nonexistent_table",
                pk={"id": 1},
                column="x",
                from_value=None,
                to_value=1,
            )
        ]
    )
    result = apply_edits(tiny_net, log)
    assert not result.applied
    assert len(result.skipped) == 1


def test_apply_edits_result_net_writes_cleanly(tiny_net, tmp_path):
    """The mutated network must round-trip through ``net.write()`` without
    OutOfSyncWarning / engine-mixing errors.

    Regression: ``apply_edits`` used to leave un-mutated tables backed by
    the original engine (e.g. ibis+duckdb) while mutated tables used
    IbisEngine — writing the resulting Network then blew up on the
    engine mismatch. All tables now share a single IbisEngine.

    Also verifies #164: no ``OutOfSyncWarning`` on the edit-then-save
    flow through ``apply_edits`` — the rebuilt Network's tables start
    with fresh ``dirty=False`` flags.
    """
    import warnings

    from netstead import Network as _Network

    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 3},
                column="free_speed",
                from_value=None,
                to_value=25,
            )
        ]
    )
    result = apply_edits(tiny_net, log)

    dest = tmp_path / "written"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result.net.write(dest, overwrite=True)
        oos = [x for x in w if "OutOfSync" in x.category.__name__]
    assert not oos, f"unexpected OutOfSyncWarning after apply_edits: {[str(x.message) for x in oos]}"

    # Round-trip: the edit landed on disk.
    reloaded = _Network.from_source(dest)
    df = reloaded.links.to_pandas()
    assert df[df["link_id"] == 3]["free_speed"].iloc[0] == 25


def test_apply_result_summary_includes_counts(tiny_net):
    """ApplyResult.summary() is human-readable, names applied + skipped counts."""
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 1},
                column="free_speed",
                from_value=None,
                to_value=25,
            ),
            Edit(
                id="e2",
                kind="fix",
                table="link",
                pk={"link_id": 9999},
                column="free_speed",
                from_value=None,
                to_value=25,
            ),
        ]
    )
    result = apply_edits(tiny_net, log)
    s = result.summary()
    assert "1" in s and "applied" in s.lower()
    assert "1" in s and "skipped" in s.lower()
