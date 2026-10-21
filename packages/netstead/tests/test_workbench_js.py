"""Unit tests for the workbench's pure (import-free) front-end modules, run under node."""

import json
import re
import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SCHEMA = {
    "properties": {
        "viz": {"$ref": "#/$defs/VizSettings"},
        "app": {"$ref": "#/$defs/AppSettings"},
        "llm": {"$ref": "#/$defs/LLMSettings"},
        "validation": {"$ref": "#/$defs/ValidationSettings"},
        "io": {"$ref": "#/$defs/IOSettings"},
    },
    "$defs": {
        "VizSettings": {
            "description": "Map rendering.",
            "properties": {
                "basemap": {"default": "positron", "enum": ["positron", "esri"], "title": "Basemap", "type": "string"}
            },
        },
        "AppSettings": {
            "properties": {
                "port": {"default": 8850, "title": "Port", "type": "integer"},
                "approve_above_s": {"default": 90.0, "minimum": 0, "title": "Approve Above S", "type": "number"},
                "console": {"default": False, "title": "Console", "type": "boolean"},
            }
        },
        "LLMSettings": {"properties": {}},
        "ValidationSettings": {
            "properties": {"rules": {"additionalProperties": {}, "title": "Rules", "type": "object"}}
        },
        "IOSettings": {
            "properties": {
                "allowed_roots": {"items": {"type": "string"}, "title": "Allowed Roots", "type": "array"},
                "spec_version": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Spec"},
            }
        },
    },
}
PAYLOAD = {
    "schema": SCHEMA,
    "values": {
        "viz": {"basemap": "esri"},
        "app": {"port": 9000, "approve_above_s": 90.0, "console": False},
        "llm": {},
        "validation": {"rules": {"r1": {"enabled": False}}},
        "io": {"allowed_roots": ["/data"], "spec_version": None},
    },
    "sources": {
        "viz.basemap": "project",
        "app.port": "env",
        "app.approve_above_s": "default",
        "app.console": "default",
        "validation.rules.r1.enabled": "user",
        "io.allowed_roots": "env",
        "io.spec_version": "default",
    },
    "readonly": {"io.allowed_roots": "config only"},
    "restart": ["app.port", "app.console"],
    "notes": {"validation": "not used yet"},
}


def test_sections_skip_folded_llm_and_describe_each_field(node_module):
    secs = node_module("settingsform.js", ["sectionsFrom"], f"sectionsFrom({json.dumps(PAYLOAD)})")
    assert [s["name"] for s in secs] == ["viz", "app", "validation", "io"]
    viz, app, validation, io = secs
    assert viz["title"] == "Map" and viz["description"] == "Map rendering."
    assert (
        viz["fields"][0].items()
        >= {"key": "viz.basemap", "kind": "choice", "value": "esri", "source": "project"}.items()
    )
    port, approve, console = app["fields"]
    assert (port["kind"], port["restart"], approve["kind"], approve["min"], console["kind"]) == (
        "int",
        True,
        "float",
        0,
        "bool",
    )
    assert validation["note"] == "not used yet" and validation["fields"][0]["kind"] == "json"
    assert validation["fields"][0]["source"] == "user"  # the highest layer among its nested keys
    roots, spec = io["fields"]
    assert (roots["kind"], roots["readonly"]) == ("list", "config only")
    assert (spec["kind"], spec["nullable"]) == ("text", True)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ({"kind": "int", "label": "Port"}, "9100", {"ok": True, "value": 9100}),
        ({"kind": "int", "label": "Port"}, "9.5", {"ok": False, "error": "Port: enter a whole number"}),
        ({"kind": "float", "label": "S"}, "", {"ok": True, "value": None}),
        ({"kind": "bool", "label": "C"}, False, {"ok": True, "value": False}),
        ({"kind": "list", "label": "T"}, " a, b ,,c ", {"ok": True, "value": ["a", "b", "c"]}),
        ({"kind": "json", "label": "R"}, '{"x": 1}', {"ok": True, "value": {"x": 1}}),
        ({"kind": "json", "label": "R"}, "{x", {"ok": False, "error": "R: not valid JSON"}),
        ({"kind": "choice", "label": "B"}, "esri", {"ok": True, "value": "esri"}),
    ],
)
def test_parse_input(node_module, field, raw, expected):
    assert (
        node_module("settingsform.js", ["parseInput"], f"parseInput({json.dumps(field)}, {json.dumps(raw)})")
        == expected
    )


def test_a_number_the_browser_cannot_parse_is_not_a_reset(node_module):
    """A partial number ("-", "1e") reads as "" with validity.badInput: it must not delete the saved value."""
    got = node_module(
        "settingsform.js",
        ["parseControl"],
        '[parseControl({ kind: "int", label: "Port" }, { type: "number", value: "", badInput: true }),'
        ' parseControl({ kind: "int", label: "Port" }, { type: "number", value: "", badInput: false }),'
        ' parseControl({ kind: "bool", label: "C" }, { type: "checkbox", value: "on", checked: true })]',
    )
    assert got == [
        {"ok": False, "error": "Port: enter a whole number"},
        {"ok": True, "value": None},
        {"ok": True, "value": True},
    ]


def test_clearing_a_value_from_another_layer_points_to_reset(node_module):
    got = node_module(
        "settingsform.js",
        ["clearHint"],
        '[clearHint({ label: "Basemap", source: "project" }, "user"),'
        ' clearHint({ label: "B", source: "user" }, "user"),'
        ' clearHint({ label: "B", source: "default" }, "user"), clearHint({ label: "B", source: "env" }, "user")]',
    )
    assert "project" in got[0] and "Reset" in got[0] and got[1:] == [None, None, None]


def test_field_kind_nullable_enum_and_source_precedence(node_module):
    got = node_module(
        "settingsform.js",
        ["fieldKind", "sourceOf"],
        '[fieldKind({ anyOf: [{ enum: ["a", "b"], type: "string" }, { type: "null" }] }),'
        ' sourceOf({ "x.y": "project" }, "x.y"),'
        ' sourceOf({ "r.a": "user", "r.b": "env", "r.c": "project" }, "r"),'
        ' sourceOf({ "r.a": "session", "r.b": "env" }, "r"), sourceOf({ "rx": "env" }, "r")]',
    )
    assert got[0] == {"kind": "choice", "options": ["a", "b"], "nullable": True}
    assert got[1:] == ["project", "env", "session", "default"]


def test_reset_targets_the_layer_the_value_comes_from(node_module):
    got = node_module(
        "settingsform.js",
        ["resetScope"],
        '["default","user","project","env","session"].map(source => resetScope({ source }))',
    )
    assert got == [None, "user", "project", None, "session"]


def test_scope_note_warns_when_a_higher_layer_wins(node_module):
    got = node_module(
        "settingsform.js",
        ["scopeNote"],
        '[scopeNote({ source: "env" }, "user"), scopeNote({ source: "project" }, "session"),'
        ' scopeNote({ source: "user" }, "project"), scopeNote({ source: "default", restart: true }, "session")]',
    )
    assert "env" in got[0] and got[1] is None and got[2] is None and "launch" in got[3]


def test_sources_merge_focus_and_highlights_without_duplicates(node_module):
    got = node_module(
        "linking.js",
        ["sourcesFor"],
        '[sourcesFor({ table: "node", id: 7 }, new Set([1, 2])), sourcesFor({ table: "link", id: 2 }, new Set([1, 2])),'
        " sourcesFor(null, new Set())]",
    )
    assert got == [{"link": [1, 2], "node": [7]}, {"link": [1, 2]}, {}]


def test_rows_request_per_scope(node_module):
    expr = """(() => {
      const base = { selection: { link_ids: [5, 6] }, highlights: new Set([9]),
                     focus: { table: "node", id: 3 }, hops: 1 };
      return {
        all_link: rowsRequest({ ...base, scope: "all", table: "link" }),
        sel_link: rowsRequest({ ...base, scope: "selection", table: "link" }),
        sel_node: rowsRequest({ ...base, scope: "selection", table: "node" }),
        hl_lane: rowsRequest({ ...base, scope: "highlighted", table: "lane" }),
        rel_lane: rowsRequest({ ...base, scope: "related", table: "lane" }),
        empty_sel: rowsRequest({ ...base, selection: null, scope: "selection", table: "link" }),
        nothing: rowsRequest({ scope: "related", table: "lane", selection: null, highlights: new Set(), focus: null }),
      };
    })()"""
    got = node_module("linking.js", ["rowsRequest"], expr)
    sources = {"sources": {"link": [9], "node": [3]}, "hops": 1}
    tint = {"related": sources, "related_mode": "tint"}
    assert got["all_link"] == tint
    assert got["sel_link"] == {"ids": [5, 6], **tint}
    filter_by = {"related_mode": "filter", "tint": sources}  # the other tables are filtered *and* tinted
    assert got["sel_node"] == {"related": {"sources": {"link": [5, 6]}, "hops": 1}, **filter_by}
    assert got["hl_lane"] == {"related": {"sources": {"link": [9]}, "hops": 1}, **filter_by}
    assert got["rel_lane"] == {"related": {"sources": {"link": [9]}, "hops": 1}, **filter_by}  # highlights only
    assert got["empty_sel"] == {"ids": [], **tint}  # nothing selected shows no rows, not every row
    assert got["nothing"] == {"ids": []}


def test_related_scope_survives_a_row_click(node_module):
    """Clicking a row in the Related scope focuses it; the rows listed must not change (review finding)."""
    expr = """(() => {
      const base = { scope: "related", table: "node", selection: null, highlights: new Set([1]), hops: 1 };
      return [rowsRequest({ ...base, focus: null }).related,
              rowsRequest({ ...base, focus: { table: "node", id: 1 } }).related,
              rowsRequest({ ...base, highlights: new Set(), focus: { table: "node", id: 1 } })];
    })()"""
    before, after, focus_only = node_module("linking.js", ["rowsRequest"], expr)
    assert before == after == {"sources": {"link": [1]}, "hops": 1}
    assert focus_only == {"ids": []}  # a focus alone tints; the Related scope lists what is highlighted


def test_scope_hint_and_clamped_offset(node_module):
    got = node_module(
        "linking.js",
        ["scopeHint", "clampOffset"],
        '[scopeHint("selection", 0), scopeHint("related", 0), scopeHint("all", 0), scopeHint("related", 3),'
        " clampOffset(200, 100, 20), clampOffset(200, 100, 0), clampOffset(100, 100, 250), clampOffset(300, 100, 300)]",
    )
    assert got[0] == "Nothing selected." and "Highlight" in got[1] and got[2] == got[3] == ""
    assert got[4:] == [0, 200, 100, 200]


def test_row_marks(node_module):
    expr = """[
      rowMarks({ table: "link", id: 5, selection: { link_ids: [5] }, highlights: new Set([5]),
                 focus: { table: "link", id: 5 }, via: null }),
      rowMarks({ table: "lane", id: 5, selection: { link_ids: [5] }, highlights: new Set([5]),
                 focus: { table: "link", id: 5 }, via: "lane.link_id → link" }),
    ]"""
    assert node_module("linking.js", ["rowMarks"], expr) == [["sel", "hl", "focus"], ["rel"]]


def test_page_offset_and_id_coercion(node_module):
    got = node_module(
        "linking.js",
        ["coerceId", "pageOffset"],
        '[pageOffset(250, 100), pageOffset(0, 100), coerceId("12"), coerceId("A-1"), coerceId(""), coerceId("-4"),'
        ' coerceId("007"), coerceId("007", false), coerceId("12", false), coerceId("1e3"), coerceId("1.5"),'
        ' coerceId("9007199254740993"), coerceId(" 1")]',
    )
    assert got == [200, 0, 12, "A-1", "", -4, 7, "007", "12", "1e3", "1.5", "9007199254740993", " 1"]


def test_a_row_click_never_changes_the_recorded_selection():
    """Carried P1a bug: in filter-to-selection mode a row click collapsed the selection to that row."""
    import re

    from netstead.workbench.server import STATIC_DIR

    table = (STATIC_DIR / "js" / "table.js").read_text()
    body = re.search(r"function rowClick\(pkVal\) \{.*?\n\}", table, re.S)
    assert body, "table.js must define rowClick(pkVal)"
    assert "dispatch(" not in body.group(0) and "focus" in body.group(0)


def test_sections_skip_the_free_form_plugins_table(node_module):
    schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "plugins": {"type": "object", "title": "Plugins"}}}
    payload = {**PAYLOAD, "schema": schema, "values": {**PAYLOAD["values"], "plugins": {"hello": {"count": 3}}}}
    secs = node_module("settingsform.js", ["sectionsFrom"], f"sectionsFrom({json.dumps(payload)})")
    assert "plugins" not in [s["name"] for s in secs]


NET = (
    "const net = {id2idx: new Map([[7, 0]]), nodeId2idx: new Map([[3, 1]]), linkStart: [0, 3],"
    " linkPositions: [0, 0, 1, 1, 2, 2], nodePositions: [9, 9, 5, 6]};"
)
STATE = (
    '{active: "a", issues: {a: {version: 1, stale: true, counts: {error: 1, warning: 2, info: 3}}},'
    ' edits: {a: {pending: 2, unsaved: 2, dirty: true, warnings: 4, truncated: false, last_source: "workbench"},'
    ' b: {pending: 1, unsaved: 0, dirty: false, warnings: 0, truncated: false, last_source: "cards"}}}'
)


def test_issues_query_string(node_module):
    got = node_module(
        "issuelist.js",
        ["issuesQuery"],
        'issuesQuery({severity: ["error", "warning"], table: "link", code: "", located: "yes", '
        'record: {table: "link", id: 7}}, 100, 50)',
    )
    assert got == "offset=100&limit=50&severity=error%2Cwarning&table=link&located=yes&record=link%3A7"


def test_anchor_positions(node_module):
    expr = (
        f"(() => {{ {NET} return [anchorPosition({{link: 7}}, net), anchorPosition({{node: 3}}, net), "
        "anchorPosition({lonlat: [1, 2]}, net), anchorPosition({link: 99}, net), anchorPosition(null, net)]; })()"
    )
    assert node_module("issuelist.js", ["anchorPosition"], expr) == [[1, 1], [5, 6], [1, 2], None, None]


def test_marker_points_keep_what_the_map_can_place(node_module):
    expr = (
        f"(() => {{ {NET} return markerPoints("
        "[{i: 0, severity: 'warning', table: 'link', key: 7, anchor: {link: 7}},"
        " {i: 1, severity: 'info', table: 'link', key: 99, anchor: {link: 99}}], net); })()"
    )
    assert node_module("issuelist.js", ["markerPoints"], expr) == [
        {"i": 0, "severity": "warning", "table": "link", "key": 7, "position": [1, 1]}
    ]


def test_record_issues_and_the_status_line(node_module):
    assert node_module("issuelist.js", ["recordIssues"], 'recordIssues({link: {"7": [0, 4]}}, "link", 7)') == [0, 4]
    assert node_module("issuelist.js", ["recordIssues"], 'recordIssues(null, "link", 7)') == []
    line = node_module(
        "issuelist.js",
        ["statusLine"],
        "statusLine({version: 2, stale: true, counts: {error: 0, warning: 272, info: 16}})",
    )
    assert line == "Validated v2: 272 warnings, 16 info · the network changed since: re-run"
    assert node_module("issuelist.js", ["statusLine"], "statusLine(null)") == "Not validated yet."
    assert node_module("issuelist.js", ["rangeLabel"], "rangeLabel(100, 50, 288)") == "101\u2013150 of 288"


def test_the_issues_badge_counts_issues_and_edit_warnings(node_module):
    assert node_module("issuelist.js", ["issuesBadge"], f"issuesBadge({STATE})") == 10  # 6 issues + 4 warnings
    assert node_module(
        "issuelist.js", ["issuesBadge"], '[issuesBadge({active: "b", edits: {}}), issuesBadge(null)]'
    ) == [
        None,
        None,
    ]


@pytest.mark.parametrize(
    ("raw", "sample", "want"),
    [
        ("45", 40, 45),
        ("abc", 40, "abc"),
        ("true", True, True),
        ("0", False, False),
        (" x ", "y", "x"),
        ("", 40, None),
        ("  ", "y", None),
        ("007", "1", "007"),
    ],
)
def test_parse_cell(node_module, raw, sample, want):
    got = node_module("editmodel.js", ["parseCell"], f"parseCell({json.dumps(raw)}, {json.dumps(sample)})")
    assert got == want


def test_a_bad_boolean_is_refused(node_module):
    expr = '(() => { try { parseCell("maybe", true); return "accepted"; } catch (e) { return e.message; } })()'
    assert "Enter true or false" in node_module("editmodel.js", ["parseCell"], expr)


def test_cell_and_delete_actions(node_module):
    fix = node_module(
        "editmodel.js",
        ["cellAction"],
        'cellAction({table: "link", ids: [1], column: "free_speed", raw: "30", current: 40, note: "why"})',
    )
    assert fix == {"type": "edit_cells", "table": "link", "ids": [1], "values": {"free_speed": 30}, "note": "why"}
    clear = node_module(
        "editmodel.js",
        ["cellAction"],
        'cellAction({table: "link", ids: [1, 2], column: "name", raw: "", current: "Main"})',
    )
    assert clear == {"type": "edit_cells", "table": "link", "ids": [1, 2], "values": {"name": None}}
    gone = node_module("editmodel.js", ["deleteAction"], 'deleteAction("link", [1])')
    assert gone == {"type": "delete_rows", "table": "link", "ids": [1]}
    planned = node_module("editmodel.js", ["deleteAction"], 'deleteAction("link", [1], {version: 0, note: "n"})')
    assert planned == {"type": "delete_rows", "table": "link", "ids": [1], "note": "n", "expected_version": 0}


def test_the_save_confirmation_says_when_the_live_check_failed(node_module):
    def prompt(info):
        return node_module("editmodel.js", ["savePrompt"], f"savePrompt({json.dumps(info)})")

    assert prompt({"check_failed": False, "truncated": False, "warnings": []}) == ""
    assert prompt(None) == ""
    two = prompt({"check_failed": False, "truncated": False, "warnings": [{"message": "a"}, {"message": "b"}]})
    assert two.startswith("2 edit warnings are still open:\n• a\n• b")
    crashed = prompt({"check_failed": True, "truncated": False, "count": 0, "warnings": []})
    assert crashed.startswith("The live edit check failed") and "warning" not in crashed.split("\n")[0]
    assert crashed.endswith("Save the copy anyway?")
    cut = prompt({"check_failed": True, "truncated": True, "count": 60, "warnings": [{"message": "w"}] * 50})
    assert "stopped early" in cut and "found 60 warnings" in cut and "… and 50 more" in cut


def test_what_a_finished_save_and_validation_say(node_module):
    base = {"output": "/out/copy", "version": 3, "edits_saved": 2, "notes": ["1 table(s) differ from the source: link"]}

    def saved(**extra):
        return node_module("editmodel.js", ["savedText"], f"savedText({json.dumps({**base, **extra})})")

    assert saved() == "Saved a copy to /out/copy (2 edits). 1 table(s) differ from the source: link"
    assert "closed while saving" in saved(closed_while_saving=True)
    assert "copy holds version 3" in saved(edited_while_saving=True)
    expr = "[validatedText({superseded: true}), validatedText({superseded: false})]"
    said = node_module("editmodel.js", ["validatedText"], expr)
    assert said[0].startswith("A newer validation finished first") and said[1] == ""


def test_the_record_schema_and_what_changed(node_module):
    cols = (
        '[{name: "link_id", kind: "num"}, {name: "name", kind: "str"}, {name: "lanes", kind: "num"},'
        ' {name: "directed", kind: "bool"}, {name: "geometry", kind: "geom"}]'
    )
    schema = node_module("editmodel.js", ["recordSchema"], f'recordSchema({cols}, "link_id")')
    assert list(schema["properties"]) == ["name", "lanes", "directed"]
    assert schema["properties"]["lanes"] == {"title": "lanes", "anyOf": [{"type": "number"}, {"type": "null"}]}
    one = node_module("editmodel.js", ["recordSchema"], f'recordSchema({cols}, "link_id", ["lanes"])')
    assert list(one["properties"]) == ["lanes"]
    changed = node_module(
        "editmodel.js",
        ["changedValues"],
        'changedValues({name: "Main", lanes: 1, directed: true}, {lanes: 2, directed: true},'
        ' ["name", "lanes", "directed"])',
    )
    assert changed == {"name": None, "lanes": 2}  # a field cleared in the form is left out of its value: null


def test_cell_warnings_index_one_table(node_module):
    expr = (
        'cellWarnings([{table: "link", key: 1, column: "from_node_id", message: "m1"},'
        ' {table: "node", key: 1, column: "ctrl_type", message: "m2"},'
        ' {table: "link", key: 1, column: "directed", message: "m3"}], "link")'
    )
    assert node_module("editmodel.js", ["cellWarnings"], expr) == {"1": {"from_node_id": ["m1"], "directed": ["m3"]}}


def test_delete_plan_text(node_module):
    text = node_module(
        "editmodel.js", ["deletePlanText"], 'deletePlanText({table: "link", rows: {lane: 2, link: 1}, refused: null})'
    )
    assert text == "Delete 1 link row? This also deletes 2 lane rows. Undo is in the Edits panel."
    refused = node_module(
        "editmodel.js", ["deletePlanText"], 'deletePlanText({table: "node", rows: {}, refused: "node 1 is still used"})'
    )
    assert refused == "node 1 is still used"


def test_pending_lines_badges_and_undo(node_module):
    lines = node_module(
        "editmodel.js",
        ["pendingLine"],
        '[pendingLine({seq: 2, note: "link 1: lanes = 2", source: "workbench"}),'
        ' pendingLine({seq: 3, note: "x", source: "cards"})]',
    )
    assert lines == ["#2 link 1: lanes = 2", "#3 x [cards]"]
    badges = node_module(
        "editmodel.js",
        ["dirtyBadge"],
        "[dirtyBadge({dirty: true, unsaved: 2}), dirtyBadge({dirty: true, unsaved: 0}),"
        " dirtyBadge({dirty: false}), dirtyBadge(null)]",
    )
    assert badges == ["2 unsaved", "changed since saved", "", ""]
    panel = node_module(
        "editmodel.js", ["editsBadge"], f'[editsBadge({STATE}), editsBadge({{...{STATE}, active: "b"}})]'
    )
    assert panel == [{"text": "2", "dirty": True, "title": "2 unsaved"}, None]
    blocked = node_module(
        "editmodel.js",
        ["undoBlocked"],
        '[undoBlocked({pending: 1, last_source: "workbench"}), undoBlocked({pending: 1, last_source: "cards"}),'
        ' undoBlocked({pending: 1, last_source: "python"}), undoBlocked({pending: 0})]',
    )
    assert blocked == [
        "",
        "The last change was made by cards; undo it there.",
        "The last change was made from Python; undo it there.",
        "Nothing to undo.",
    ]
    assert node_module("editmodel.js", ["editSummary"], f"editSummary({STATE}).unsaved") == 2


@pytest.mark.parametrize(
    ("prev", "nxt", "want"),
    [
        ("a@1", "a@2", "edited"),
        ("a@1", "b@1", "switched"),
        (None, "a@0", "switched"),
        ("a@1", None, "switched"),
        ("a@1", "a@1", "same"),
        ("a-2@1", "a@2", "switched"),
    ],
)
def test_net_change(node_module, prev, nxt, want):
    assert node_module("linking.js", ["netChange"], f"netChange({json.dumps(prev)}, {json.dumps(nxt)})") == want


def test_row_marks_flag_issues_and_edit_warnings(node_module):
    expr = (
        'rowMarks({table: "link", id: 1, selection: null, highlights: new Set(), focus: null, via: null,'
        " issues: 2, warned: true})"
    )
    assert node_module("linking.js", ["rowMarks"], expr) == ["iss", "warn"]


def test_only_non_key_scalar_cells_are_edited_in_place(node_module):
    expr = (
        '[{name: "link_id", kind: "num"}, {name: "name", kind: "str"}, {name: "geometry", kind: "geom"},'
        ' {name: "directed", kind: "bool"}, undefined].map(c => cellEditable(c, "link_id"))'
    )
    assert node_module("editmodel.js", ["cellEditable"], expr) == [False, True, False, True, False]


def test_cell_options_offer_the_spec_values_then_the_existing_ones(node_module):
    expr = '[cellOptions({enum: ["b", "a"]}, ["b", "c"]), cellOptions({enum: null}, ["z", "y"]), cellOptions({}, null)]'
    assert node_module("editmodel.js", ["cellOptions"], expr) == [["a", "b", "c"], ["y", "z"], []]


def test_list_options_complete_the_last_token_of_a_comma_list(node_module):
    expr = (
        '[listOptions("", ["auto", "bike"]), listOptions("auto, b", ["auto", "bike"]),'
        ' listOptions("auto,", ["auto", "bike"])]'
    )
    assert node_module("editmodel.js", ["listOptions"], expr) == [
        ["auto", "bike"],
        ["auto, auto", "auto, bike"],
        ["auto,auto", "auto,bike"],
    ]


def test_leaving_a_changed_cell_says_the_edit_was_not_applied(node_module):
    expr = (
        '[discardedText("parking", "none", "none"), discardedText("parking", "angle", "none"),'
        ' discardedText("parking", "", null)]'
    )
    assert node_module("editmodel.js", ["discardedText"], expr) == [
        "",
        "parking: the typed value was not applied. Press Enter to apply an edit; Escape discards it.",
        "",
    ]


def test_an_empty_cell_parses_by_its_column_kind(node_module):
    expr = '[cellSample(null, "num"), cellSample(null, "bool"), cellSample(null, "str"), cellSample(3, "str")]'
    assert node_module("editmodel.js", ["cellSample"], expr) == [0, False, "", 3]
    refused = (
        '(() => { try { parseCell("maybe", cellSample(null, "bool")); return "sent"; }'
        ' catch (e) { return "refused"; } })()'
    )
    assert node_module("editmodel.js", ["parseCell", "cellSample"], refused) == "refused"


SEL = {
    "status": "ambiguous",
    "net_id": "n",
    "link_ids": [1, 2],
    "intent": {"facility": {"name": "Cherry"}},
    "anchors": [
        {
            "role": "from",
            "node_id": 53,
            "kind": "intersection",
            "detail": "d",
            "anchor": "Prospect",
            "pinned": False,
            "candidates": [],
            "lon": 1,
            "lat": 2,
        },
        {
            "role": "to",
            "node_id": 60,
            "kind": "intersection",
            "detail": "d",
            "anchor": "West Center",
            "pinned": False,
            "candidates": [{"node_id": 61, "lon": 3, "lat": 4}, {"node_id": 62}],
            "lon": 5,
            "lat": 6,
        },
    ],
    "anchors_unplaced": [],
}


def test_anchor_rows_list_candidates_and_mark_unplaced_ones(node_module):
    rows = node_module("selectmodel.js", ["anchorRows"], f"anchorRows({json.dumps(SEL)})")
    assert [r["role"] for r in rows] == ["from", "to"]
    assert rows[1]["candidates"] == [{"nodeId": 61, "placed": True}, {"nodeId": 62, "placed": False}]
    assert rows[1]["canUnpin"] is False
    assert rows[1]["canPin"] is True and rows[0]["canPin"] is False  # only an anchor with other candidates
    unplaced = {**SEL, "anchors": SEL["anchors"][:1], "anchors_unplaced": [{**SEL["anchors"][1], "pinned": True}]}
    rows = node_module("selectmodel.js", ["anchorRows"], f"anchorRows({json.dumps(unplaced)})")
    assert rows[1]["unplaced"] is True and rows[1]["canUnpin"] is True and rows[1]["canPin"] is False


def test_candidate_points_are_only_placed_candidates(node_module):
    pts = node_module("selectmodel.js", ["candidatePoints"], f"candidatePoints({json.dumps(SEL)})")
    assert pts == [{"role": "to", "node_id": 61, "lon": 3, "lat": 4}]
    assert node_module("selectmodel.js", ["candidatePoints"], "candidatePoints(null)") == []


def test_export_choices_follow_status_and_kind(node_module):
    expr = (
        f"[exportChoices({json.dumps(SEL)}), exportChoices({json.dumps({**SEL, 'status': 'resolved'})}),"
        f" exportChoices({json.dumps({**SEL, 'status': 'resolved', 'intent': None})}), exportChoices(null),"
        f" exportChoices({json.dumps({**SEL, 'status': 'not_found'})})]"
    )
    amb, res, ids, none, lost = node_module("selectmodel.js", ["exportChoices"], expr)
    assert amb == {"resolved": False, "query": False, "reason": "Pin the ambiguous anchors first."}
    assert res == {"resolved": True, "query": True, "reason": ""}
    assert ids["query"] is False and none["reason"] == "Nothing is selected."
    assert lost["resolved"] is False and lost["reason"] == "Nothing was found to export."


def test_selection_badge(node_module):
    expr = (
        f"[selectionBadge({json.dumps(SEL)}), selectionBadge({json.dumps({**SEL, 'status': 'resolved'})}),"
        " selectionBadge(null)]"
    )
    assert node_module("selectmodel.js", ["selectionBadge"], expr) == ["?", 2, None]


def test_link_widths_scale_a_numeric_property(node_module):
    net = {"L": {"count": 3}, "linkStart": [0, 2, 4, 6], "linkLanes": [1, 1, 1]}
    prop = {"name": "lanes", "kind": "continuous", "values": [1, None, 3], "min": 1, "max": 3}
    expr = f"Array.from(buildLinkWidths({json.dumps(net)}, {{width_by: 'lanes'}}, {json.dumps(prop)}))"
    assert node_module("palette.js", ["buildLinkWidths"], expr) == [1.5, 1.5, 1, 1, 10, 10]
    n, p = json.dumps(net), json.dumps(prop)
    off = f"[buildLinkWidths({n}, {{width_by: 'none'}}, {p}), buildLinkWidths({n}, {{width_by: 'lanes'}}, null)]"
    assert node_module("palette.js", ["buildLinkWidths"], off) == [None, None]


def test_unmatched_links_are_dimmed(node_module):
    net = {"L": {"count": 2}, "linkStart": [0, 1, 2], "attrsIds": None}
    expr = (
        f"Array.from(dimUnmatched(new Uint8Array([9,9,9,222, 9,9,9,222]), {json.dumps(net)}, [10, 20], new Set([20])))"
    )
    assert node_module("palette.js", ["dimUnmatched"], expr) == [9, 9, 9, 40, 9, 9, 9, 222]


def test_the_filtered_scope_adds_the_view_filter(node_module):
    vf = {"table": "link", "where": [{"col": "lanes", "op": "gte", "val": 2}]}
    expr = (
        f"[combinedFilter([{{col:'name',op:'contains',val:'a'}}], 'filtered', 'link', {json.dumps(vf)}),"
        f" combinedFilter([], 'all', 'link', {json.dumps(vf)}),"
        f" combinedFilter([], 'filtered', 'node', {json.dumps(vf)}),"
        " combinedFilter([], 'filtered', 'link', null)]"
    )
    on, off, other, none = node_module("linking.js", ["combinedFilter"], expr)
    assert on == [{"col": "lanes", "op": "gte", "val": 2}, {"col": "name", "op": "contains", "val": "a"}]
    assert off is None and other is None and none is None
    hint = node_module("linking.js", ["scopeHint"], "[scopeHint('filtered', 0), scopeHint('filtered', 3)]")
    assert hint == ["No link matches the filter (Layers → Filter links).", ""]
    nodes = node_module(
        "linking.js", ["scopeHint"], "[scopeHint('filtered', 0, 'node'), scopeHint('filtered', 40, 'node')]"
    )
    assert nodes == ["Filtered applies to links: every row is listed."] * 2
    assert node_module("linking.js", ["scopeHint"], "scopeHint('all', 40, 'node')") == ""


def test_a_stale_pin_is_named_and_turns_off_the_query_form(node_module):
    stale = {**SEL, "status": "resolved", "intent": {"to_node": 60, "from_anchor": "a"}, "stale_pins": [60]}
    expr = f"[staleAnchors({json.dumps(stale)}), staleNotes({json.dumps(stale)}), exportChoices({json.dumps(stale)})]"
    anchors, notes, choice = node_module("selectmodel.js", ["staleAnchors", "staleNotes", "exportChoices"], expr)
    assert anchors == [{"role": "to", "nodeId": 60}]
    assert notes[0].startswith("The to anchor's pinned node 60 is no longer on the facility.")
    assert choice["resolved"] is True and choice["query"] is False and "re-pinned" in choice["reason"]
    fresh = {**stale, "stale_pins": []}
    assert node_module("selectmodel.js", ["staleNotes"], f"staleNotes({json.dumps(fresh)})") == []


@pytest.mark.parametrize(
    ("op", "raw", "want"),
    [
        ("eq", "007", "007"),  # a text key stays text: the server reads it as the column's type
        ("gte", " 2 ", "2"),
        ("contains", "Main St", "Main St"),
        ("eq", "", ""),
        ("isnull", "anything", None),
        ("notnull", "", None),
    ],
)
def test_a_filter_value_is_sent_as_typed(node_module, op, raw, want):
    assert node_module("filtermodel.js", ["filterValue"], f"filterValue({json.dumps(op)}, {json.dumps(raw)})") == want


def _filter_server(where, count=12):
    return {"active": "n1", "filters": {"n1": {"table": "link", "where": where, "count": count}}}


def test_a_recorded_filter_is_restored_into_the_controls(node_module):
    one = _filter_server([{"col": "ref", "op": "eq", "val": "007"}])
    empty = _filter_server([{"col": "name", "op": "isnull", "val": None}], count=None)
    view, nulls = node_module(
        "filtermodel.js", ["filterView"], f"[filterView({json.dumps(one)}), filterView({json.dumps(empty)})]"
    )
    assert view == {
        "mode": "controls",
        "count": "12 links match",
        "canClear": True,
        "note": "",
        "controls": {"col": "ref", "op": "eq", "val": "007", "valDisabled": False},
    }
    assert nulls["controls"] == {"col": "name", "op": "isnull", "val": "", "valDisabled": True}
    assert nulls["count"] == "filter no longer applies"


def test_no_filter_resets_the_controls(node_module):
    for server in ({"active": "n1", "filters": {}}, {"active": "n1", "filters": {"n2": {"where": []}}}, None):
        view = node_module("filtermodel.js", ["filterView"], f"filterView({json.dumps(server)})")
        assert view == {
            "mode": "none",
            "count": "",
            "canClear": False,
            "note": "",
            "controls": {"op": "eq", "val": "", "valDisabled": False},
        }


def test_a_filter_the_controls_cannot_send_back_is_shown_read_only(node_module):
    in_list = _filter_server([{"col": "ref", "op": "in", "val": ["a", "<b>"]}])
    several = _filter_server([{"col": "lanes", "op": "gte", "val": 2}, {"col": "name", "op": "notnull", "val": None}])
    a, b = node_module(
        "filtermodel.js", ["filterView"], f"[filterView({json.dumps(in_list)}), filterView({json.dumps(several)})]"
    )
    assert a["mode"] == "readonly" and "controls" not in a and a["canClear"] is True
    assert a["note"] == "Set from Python: ref in a, <b>."  # escaped by the panel (textContent)
    assert b["note"] == "Set from Python: lanes ≥ 2 and name is not empty."


def test_the_filter_key_changes_only_with_the_recorded_filter(node_module):
    s1 = _filter_server([{"col": "lanes", "op": "gte", "val": 2}])
    s2 = {**s1, "filters": {"n1": {**s1["filters"]["n1"], "count": 99}}}
    s3 = _filter_server([{"col": "lanes", "op": "gte", "val": 3}])
    none, other = {"active": "n1", "filters": {}}, {"active": "n2", "filters": {}}
    expr = f"[{', '.join(f'filterKey({json.dumps(s)})' for s in (s1, s2, s3, none, other))}]"
    k1, k2, k3, kn, ko = node_module("filtermodel.js", ["filterKey"], expr)
    assert k1 == k2 and k1 != k3 and kn != ko and kn != k1


def test_focus_moves_to_unpin_after_a_pin(node_module):
    expr = (
        "[unpinAfterPin('pin-to-60'), unpinAfterPin('pin-from-a-b'), unpinAfterPin('unpin-to'), unpinAfterPin('use-x')]"
    )
    assert node_module("selectmodel.js", ["unpinAfterPin"], expr) == ["unpin-to", "unpin-from", None, None]


ASSIST_PAYLOAD = {
    "turns": [
        {"id": 1, "role": "user", "text": "set lanes", "steps": [], "draft_id": None, "error": None},
        {"id": 2, "role": "assistant", "text": "", "draft_id": 3, "error": None, "steps": [
            {"summary": "select “x”", "python": "app.do(Select(...))", "status": "done", "result": "resolved: 2 links"},
            {"summary": "set lanes = 3 on 2 links", "python": "app.do(EditCells(...))", "status": "drafted",
             "result": ""},
        ]},
    ],
    "draft": {"id": 3, "turn_id": 2, "status": "pending", "actions": [{"type": "edit_cells"}]},
    "status": {"provider": "ollama", "model": "qwen2.5:7b", "local": True, "used": {"ollama/qwen2.5:7b": 1234},
               "budget": 200000, "busy": False, "sends": ["a"], "error": None},
}  # fmt: skip


def test_step_rows_and_status_labels(node_module):
    rows = node_module("assistmodel.js", ["stepRows"], f"stepRows({json.dumps(ASSIST_PAYLOAD['turns'][1])})")
    assert [r["label"] for r in rows] == ["Done", "Awaiting approval"]
    assert rows[0]["result"] == "resolved: 2 links"
    expr = "stepRows({steps: [{status: 'superseded'}, {status: 'stale'}, {status: 'odd'}]}).map(r => r.label)"
    assert node_module("assistmodel.js", ["stepRows"], expr) == [
        "Replaced by a newer question", "Out of date: the network changed", "odd"
    ]  # fmt: skip
    assert node_module("assistmodel.js", ["stepRows"], "stepRows({})") == []


def test_the_pending_draft_and_the_badge(node_module):
    p = json.dumps(ASSIST_PAYLOAD)
    stale = json.dumps({**ASSIST_PAYLOAD, "draft": {**ASSIST_PAYLOAD["draft"], "status": "stale"}})
    expr = f"[pendingDraft({p}), assistBadge({p}), assistBadge({{turns: [], draft: null}}), assistBadge({stale})]"
    draft, badge, none, gone = node_module("assistmodel.js", ["pendingDraft", "assistBadge"], expr)
    assert draft == {"id": 3, "steps": 1} and badge == "1" and none is None and gone is None


def test_budget_text(node_module):
    remote = {**ASSIST_PAYLOAD["status"], "local": False, "provider": "anthropic", "model": "h",
              "used": {"anthropic/h": 12000, "openai/g": 3000}, "remote_used": 15000}  # fmt: skip
    no_model = {**ASSIST_PAYLOAD["status"], "model": None, "used": {"ollama": 7}}
    expr = (
        f"[budgetText({json.dumps(ASSIST_PAYLOAD['status'])}), budgetText({json.dumps(remote)}),"
        f" budgetText({json.dumps(no_model)}), usageKey('ollama', null), usageKey('ollama', 'q')]"
    )
    local, rem, bare, k1, k2 = node_module("assistmodel.js", ["budgetText", "usageKey"], expr)
    assert local == "1,234 tokens used (local model: no limit)"
    assert rem == "15,000 of 200,000 tokens used this session (all remote models)"  # every remote model counts
    assert bare == "7 tokens used (local model: no limit)" and (k1, k2) == ("ollama", "ollama/q")  # as usage_key


def test_the_command_bar_routes_enter_to_select_and_mod_enter_to_ask(node_module):
    expr = (
        "[barRoute({key:'Enter'}), barRoute({key:'Enter', ctrlKey:true}),"
        " barRoute({key:'Enter', metaKey:true}), barRoute({key:'a'})]"
    )
    assert node_module("assistmodel.js", ["barRoute"], expr) == ["select", "ask", "ask", None]


def test_settings_switches_save_what_is_on_screen_and_list_only_offerable_actions(node_module):
    expr = "switchedOn([{checked: true, id: 'a'}, {checked: false, id: 'b'}, {checked: true, id: 'c'}])"
    assert node_module("assistmodel.js", ["switchedOn"], expr) == ["a", "c"]
    actions = [
        {"type": "style", "plugin": None, "assistant": True},
        {"type": "open_network", "plugin": None, "assistant": False},  # the server says: never offered
        {"type": "loud.shout", "plugin": "loud", "assistant": True},  # a plugin's: under Plugins instead
    ]
    got = node_module("assistmodel.js", ["offerableCore"], f"offerableCore({json.dumps(actions)}).map(a => a.type)")
    assert got == ["style"]


# ---------------------------------------------------------------------------- icons.js


ICON_NAMES = [
    "undo", "redo", "gear", "terminal", "code", "save", "folder-open", "play", "download", "check-circle",
    "alert-triangle", "x-circle", "circle", "filter", "x", "chevron-down", "layers", "crosshair", "grip",
]  # fmt: skip


def test_icon_renders_an_inline_svg_that_is_hidden_from_readers_by_default(node_module):
    svg = node_module("icons.js", ["icon"], 'icon("undo")')
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'aria-hidden="true"' in svg and 'viewBox="0 0 24 24"' in svg and 'stroke="currentColor"' in svg
    assert 'width="18"' in svg and 'height="18"' in svg
    assert 'width="22"' in node_module("icons.js", ["icon"], 'icon("undo", {size: 22})')


def test_a_labelled_icon_is_an_image_for_readers(node_module):
    svg = node_module("icons.js", ["icon"], 'icon("gear", {label: "Settings"})')
    assert 'role="img"' in svg and 'aria-label="Settings"' in svg and "aria-hidden" not in svg


def test_an_unknown_icon_throws(node_module):
    expr = '(() => { try { icon("nope"); return null; } catch (e) { return e.message; } })()'
    assert "nope" in node_module("icons.js", ["icon"], expr)


def test_the_icon_set_has_every_name_the_workbench_uses(node_module):
    names = node_module("icons.js", ["ICONS"], "Object.keys(ICONS)")
    assert set(ICON_NAMES) <= set(names)


def test_every_data_icon_in_index_is_in_the_set(node_module):
    from netstead.workbench.server import STATIC_DIR

    html = (STATIC_DIR / "index.html").read_text()
    used = set(re.findall(r'data-icon="([\w-]+)"', html))
    assert used  # the header, dock and footer carry icon buttons
    assert used <= set(node_module("icons.js", ["ICONS"], "Object.keys(ICONS)"))


def test_a_prototype_key_is_not_an_icon(node_module):
    expr = '(() => { try { icon("toString"); return null; } catch (e) { return e.message; } })()'
    assert "toString" in node_module("icons.js", ["icon"], expr)


def test_redo_is_blocked_without_a_redo_stack(node_module):
    def blocked(summary):
        return node_module("editmodel.js", ["redoBlocked"], f"redoBlocked({summary})")

    reason = blocked("{redo_count: 0}")
    assert isinstance(reason, str) and reason
    assert blocked("{redo_count: 2}") == ""  # like undoBlocked: "" when allowed
    assert blocked("null") and blocked("{pending: 1}")  # no summary, or no redo_count


# --- netbuf.js: direction arrows (beta item 16) ---------------------------------------------------------------


def test_midpoint_by_length_halves_the_path_length_not_the_vertex_count(node_module):
    mid = "midpointByLength"
    assert node_module("netbuf.js", [mid], f"{mid}([[0,0],[10,0],[10,10]])") == [10, 0]
    assert node_module("netbuf.js", [mid], f"{mid}([[0,0],[4,0]])") == [2, 0]
    assert node_module("netbuf.js", [mid], f"{mid}([[0,0],[10,0],[10,30]])") == [10, 10]  # inside a segment
    # Many short vertices then one long segment: the middle vertex would sit near the start.
    assert node_module("netbuf.js", [mid], f"{mid}([[0,0],[1,0],[2,0],[3,0],[100,0]])") == [50, 0]
    assert node_module("netbuf.js", [mid], f"{mid}([[5,5]])") is None
    assert node_module("netbuf.js", [mid], f"{mid}([[5,5],[5,5]])") == [5, 5]  # zero length: the first point


def test_build_arrows_places_one_arrow_per_link_row_on_its_offset_side(node_module):
    paths = "[[[0,0],[4,0]], [[7,7]], [[0,0],[0,2],[0,4]]]"
    on = node_module("netbuf.js", ["buildArrows"], f"buildArrows({paths}, {{offset: true}})")
    off = node_module("netbuf.js", ["buildArrows"], f"buildArrows({paths}, {{offset: false}})")
    bare = node_module("netbuf.js", ["buildArrows"], f"buildArrows({paths})")
    assert [a["side"] for a in on] == [1, 1]
    assert [a["side"] for a in off] == [0, 0]
    assert [a["side"] for a in bare] == [0, 0]
    # A one-point path has no arrow, but the others keep their link index.
    assert [a["link"] for a in on] == [0, 2]
    # The position is the midpoint by length; the angle turns an up-pointing icon along the travel direction.
    assert [a["position"] for a in on] == [[2, 0], [0, 2]]
    assert [a["angle"] for a in on] == [-90, 0]
    mids = node_module(
        "netbuf.js",
        ["buildArrows", "midpointByLength"],
        f"buildArrows({paths}).map(a => [a.position, midpointByLength({paths}[a.link])])",
    )
    assert all(pos == mid for pos, mid in mids)


def test_link_paths_splits_the_flat_vertex_buffer_per_link(node_module):
    expr = "linkPaths(3, [0, 2, 3, 5], new Float32Array([0,0, 1,1, 2,2, 3,3, 4,4]))"
    assert node_module("netbuf.js", ["linkPaths"], expr) == [[[0, 0], [1, 1]], [[2, 2]], [[3, 3], [4, 4]]]


def test_arrow_pixel_offset_follows_the_right_hand_offset_line(node_module):
    # Screen pixels, +y down (deck's getPixelOffset). The offset is `amt` path widths to the right of travel,
    # the side the PathStyleExtension draws a positive offset on.
    def off(angle, side, width, amt=0.8):
        got = node_module("netbuf.js", ["arrowPixelOffset"], f"arrowPixelOffset({angle}, {side}, {width}, {amt})")
        return [round(v, 6) for v in got]

    assert off(-90, 1, 10) == [0, 8]  # eastbound: right is south, i.e. down the screen
    assert off(0, 1, 10) == [8, 0]  # northbound: right is east
    assert off(90, 1, 10) == [0, -8]  # westbound: right is north
    assert off(180, 1, 5) == [-4, 0]  # southbound: right is west
    assert off(-90, 0, 10) == [0, 0]  # offsets off: on the centreline


@pytest.mark.parametrize(("zoom", "size"), [(12, 8), (13, 8), (14, 10), (15, 12), (18, 18), (22, 18)])
def test_arrow_size_grows_with_zoom_between_8_and_18_px(node_module, zoom, size):
    assert node_module("netbuf.js", ["arrowSize"], f"arrowSize({zoom})") == size


# ---- gridfilter.js: the table's per-column filter model and its chip text ----

_GF = ["createGridFilter", "chipText"]


def _gf(node_module, body: str):
    """Evaluate ``body`` with ``f`` bound to a fresh model."""
    return node_module("gridfilter.js", _GF, f"(() => {{ const f = createGridFilter(); {body} }})()")


def test_grid_filter_conditions_map_each_spec_to_a_server_condition(node_module):
    body = """
      f.setFilter("free_speed", {kind: "num", op: "gt", val: 40});
      f.setFilter("lanes", {kind: "num", op: "between", lo: 35, hi: 45});
      f.setFilter("name", {kind: "text", val: "Pine"});
      f.setFilter("shape", {kind: "empty", op: "isnull"});
      f.setFilter("allowed_uses", {kind: "set", mode: "any_of", values: ["auto", "truck"], listLike: true});
      f.setFilter("facility_type", {kind: "set", mode: "any_of", values: ["primary"], listLike: false});
      f.setFilter("roadway", {kind: "set", mode: "none_of", values: ["residential"], listLike: false});
      return [f.conditions(), f.active()];"""
    conds, active = _gf(node_module, body)
    assert conds == [
        {"col": "free_speed", "op": "gt", "val": 40},
        {"col": "lanes", "op": "between", "val": [35, 45]},
        {"col": "name", "op": "contains", "val": "Pine"},
        {"col": "shape", "op": "isnull", "val": None},
        {"col": "allowed_uses", "op": "any_of", "val": ["auto", "truck"]},
        {"col": "facility_type", "op": "in", "val": ["primary"]},  # a plain column: membership, not tokens
        {"col": "roadway", "op": "none_of", "val": ["residential"]},  # none_of works on plain cells too
    ]
    assert active == ["free_speed", "lanes", "name", "shape", "allowed_uses", "facility_type", "roadway"]


def test_grid_filter_bool_set_sends_booleans_not_their_names(node_module):
    body = (
        'f.setFilter("managed", {kind: "set", mode: "any_of", values: ["True"], listLike: false, bool: true});'
        " return f.conditions();"
    )
    assert _gf(node_module, body) == [{"col": "managed", "op": "in", "val": [True]}]


def test_grid_filter_remove_clear_get_and_a_replaced_column(node_module):
    body = """
      f.setFilter("a", {kind: "num", op: "eq", val: 1});
      f.setFilter("b", {kind: "text", val: "x"});
      f.setFilter("a", {kind: "num", op: "lt", val: 2});   // replaces, keeps its place
      const afterReplace = f.conditions();
      f.remove("b"); const afterRemove = f.active(); const gone = f.get("b"); const kept = f.get("a");
      f.setFilter("c", null); // a null spec is a removal, not an error
      f.setFilter("a", {kind: "num", op: "eq", val: 3}); f.clear();
      return [afterReplace, afterRemove, gone, kept, f.active(), f.conditions()];"""
    after_replace, after_remove, gone, kept, active, conds = _gf(node_module, body)
    assert after_replace == [{"col": "a", "op": "lt", "val": 2}, {"col": "b", "op": "contains", "val": "x"}]
    assert after_remove == ["a"] and gone is None and kept == {"kind": "num", "op": "lt", "val": 2}
    assert active == [] and conds == []


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ('{kind: "num", op: "gt", val: "abc"}', "number"),
        ('{kind: "num", op: "between", lo: 5, hi: 1}', "between"),
        ('{kind: "num", op: "like", val: 1}', "operator"),
        ('{kind: "set", mode: "any_of", values: [], listLike: true}', "value"),
        ('{kind: "set", mode: "all_of", values: ["a"], listLike: false}', "all of"),
        ('{kind: "text", val: "  "}', "text"),
        ('{kind: "colour"}', "kind"),
    ],
)
def test_grid_filter_refuses_a_malformed_spec(node_module, spec, message):
    body = f'try {{ f.setFilter("c", {spec}); return null; }} catch (e) {{ return e.message; }}'
    got = _gf(node_module, body)
    assert got and message in got.lower()


@pytest.mark.parametrize(
    ("col", "spec", "text"),
    [
        ("free_speed", '{kind: "num", op: "gt", val: 40}', "free_speed > 40"),
        ("free_speed", '{kind: "num", op: "gte", val: 40}', "free_speed ≥ 40"),
        ("free_speed", '{kind: "num", op: "ne", val: 40}', "free_speed ≠ 40"),
        ("free_speed", '{kind: "num", op: "between", lo: 35, hi: 45}', "free_speed 35–45"),  # noqa: RUF001 (an en dash)
        ("name", '{kind: "text", val: "Pine"}', 'name contains "Pine"'),
        (
            "allowed_uses",
            '{kind: "set", mode: "any_of", values: ["auto", "truck"], listLike: true}',
            "allowed_uses any of auto, truck",
        ),
        (
            "allowed_uses",
            '{kind: "set", mode: "all_of", values: ["auto", "truck"], listLike: true}',
            "allowed_uses all of auto, truck",
        ),
        (
            "facility_type",
            '{kind: "set", mode: "none_of", values: ["residential"]}',
            "facility_type none of residential",
        ),
        ("lanes", '{kind: "empty", op: "isnull"}', "lanes is empty"),
        ("lanes", '{kind: "empty", op: "notnull"}', "lanes is not empty"),
    ],
)
def test_grid_filter_chip_text(node_module, col, spec, text):
    assert node_module("gridfilter.js", _GF, f'chipText("{col}", {spec})') == text
    assert _gf(node_module, f'f.setFilter("{col}", {spec}); return f.chipText("{col}");') == text


def test_grid_filter_chip_text_elides_a_long_value_list(node_module):
    spec = '{kind: "set", mode: "any_of", values: ["a", "b", "c", "d", "e", "f"], listLike: true}'
    assert node_module("gridfilter.js", _GF, f'chipText("u", {spec})') == "u any of a, b, c, d +2"
