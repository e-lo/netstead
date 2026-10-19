"""cardmodel.js and controller.js under node: the change a form describes, the badge, and the panel's flow."""

from pathlib import Path

import netstead_project_cards

STATIC = Path(netstead_project_cards.__file__).parent / "static"
MODEL = {"cardmodel.js": STATIC / "cardmodel.js"}
BOTH = {**MODEL, "controller.js": STATIC / "controller.js"}


def test_a_property_change_from_form_values(run_js):
    got = run_js(
        MODEL,
        """
import { propertyChange } from "./cardmodel.js";
const set = propertyChange({ lanes: 3, name: "", free_speed: null }, { mode: "set", note: "widened" }, { lanes: 1 });
const delta = propertyChange({ lanes: "1" }, { mode: "change", conflict: "warn" }, {});
const errors = [];
for (const [columns, options] of [[{ lanes: "" }, {}], [{ lanes: "two" }, { mode: "change" }]]) {
  try { propertyChange(columns, options, {}); } catch (e) { errors.push(e.message); }
}
console.log(JSON.stringify({ set, delta, errors }));
""",
    )
    assert got["set"] == {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
        "note": "widened",
    }
    assert got["delta"] == {"type": "roadway_property_change", "property_changes": {"lanes": {"change": 1}}}
    assert got["errors"] == [
        "give a new value for at least one property",
        "lanes: change is a number, the delta to add",
    ]


def test_deletion_addition_and_the_action(run_js):
    got = run_js(
        MODEL,
        """
import { addition, changeAction, deletion, needsSelection } from "./cardmodel.js";
const del = deletion({ clean_nodes: true });
const add = addition({ link_id: 340, from_node_id: 1, to_node_id: 122, name: "", lanes: 1 },
                     { node_id: 122, x_coord: 1, y_coord: 2 });
const linkOnly = addition({ link_id: 340, from_node_id: 1, to_node_id: 2 }, {});
console.log(JSON.stringify({
  del, add, linkOnly,
  onSelection: changeAction("add_change", del),
  onSaved: changeAction("preview", del, { saved: "ski", netId: "base" }),
  addAction: changeAction("add_change", add),
  needs: ["roadway_property_change", "roadway_deletion", "roadway_addition"].map(needsSelection),
}));
""",
    )
    assert got["del"] == {"type": "roadway_deletion", "clean_nodes": True}
    assert got["add"]["links"] == [{"link_id": 340, "from_node_id": 1, "to_node_id": 122, "lanes": 1}]
    assert got["add"]["nodes"] == [{"node_id": 122, "x_coord": 1, "y_coord": 2}] and got["linkOnly"]["nodes"] == []
    assert got["onSelection"] == {"type": "project_cards.add_change", "change": got["del"], "use_selection": True}
    assert got["onSaved"] == {
        "type": "project_cards.preview",
        "change": got["del"],
        "net_id": "base",
        "saved_selection": "ski",
    }
    assert got["addAction"] == {"type": "project_cards.add_change", "change": got["add"]}
    assert got["needs"] == [True, True, False]


def test_the_badge_follows_the_draft_and_the_preview(run_js):
    got = run_js(
        MODEL,
        """
import { draftBadge, draftNet, draftOf, openPreview, warningLines } from "./cardmodel.js";
const state = { plugins: { project_cards: {
  drafts: { base: { count: 2, project: "", changes: [] }, named: { count: 0, project: "Lanes", changes: [] } },
  preview: { net_id: "base-2", base: "base", describe: "x", stale: true } } } };
console.log(JSON.stringify({
  badge: draftBadge(state, "base"), onPreview: draftBadge(state, "base-2"), none: draftBadge(state, "other"),
  named: draftBadge(state, "named"), net: draftNet(state, "base-2"), same: draftNet(state, "other"),
  draft: draftOf(state, "base-2").count, missing: draftOf({}, "base"),
  preview: openPreview(state), noPreview: openPreview({ plugins: {} }),
  lines: warningLines([{ table: "link", key: 1, message: "to_node_id 424242 is not a node" }]),
}));
""",
    )
    badge = {"text": "2", "dirty": True, "title": "2 uncommitted card change(s)"}
    assert got["badge"] == badge and got["onPreview"] == badge  # a preview shows its base's draft
    assert got["none"] is None and got["named"] is None
    assert got["net"] == "base" and got["same"] == "other" and got["draft"] == 2 and got["missing"] is None
    assert got["preview"] == {"net_id": "base-2", "base": "base", "describe": "x", "stale": True}
    assert got["noPreview"] is None
    assert got["lines"] == ["link 1: to_node_id 424242 is not a node"]


STUB_WB = """
import { createController } from "./controller.js";
const calls = [];
const forms = {
  roadway_property_change: { supported: true, net_id: "base", existing: { lanes: 1 }, targets: { count: 2 } },
  roadway_deletion: { supported: true, net_id: "base", targets: { count: 1 } },
  roadway_addition: { supported: true, net_id: "base", next_ids: { link_id: 340, node_id: 122 } },
  transit_property_change: { supported: false, message: "transit_property_change is not yet supported" },
};
const refuse = new Set();
const wb = {
  api: {
    get: async path => { calls.push(["get", path]);
      if (path.startsWith("/commit-check")) return { warnings: [{ code: "edit.fk_missing" }], problems: [], yaml: "" };
      return forms[new URLSearchParams(path.split("?")[1]).get("type")]; },
    dispatch: async action => { calls.push(["dispatch", action]);
      if (refuse.has(action.type)) throw new Error(`${action.type} refused`);
      return action.type === "project_cards.preview" ? { preview: "net-2" } : { count: 1, describe: "x" }; },
  },
  toast: message => calls.push(["toast", message]),
};
const ctl = createController(wb);
"""

CONTROLLER_HARNESS = (
    STUB_WB
    + """
const values = { columns: { lanes: 3 }, options: { mode: "set" } };
await ctl.start("transit_property_change");
await ctl.start("roadway_property_change");
await ctl.preview(values);
const afterPreview = ctl.state.preview;
await ctl.add(values);
await ctl.check({ project: "Lanes", net_id: "base" });
await ctl.commit({ project: "Lanes", path: "cards/lanes.yml", tags: [] });
console.log(JSON.stringify({ calls, afterPreview, compose: ctl.state.compose, preview: ctl.state.preview }));
"""
)


def test_the_controller_reads_the_form_then_dispatches_actions(run_js):
    got = run_js(BOTH, CONTROLLER_HARNESS)
    calls = got["calls"]
    assert calls[0] == ["get", "/form?type=transit_property_change"]
    assert calls[1] == ["toast", "transit_property_change is not yet supported"]
    assert calls[2] == ["get", "/form?type=roadway_property_change&use_selection=true"]
    change = {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
    }
    # The form was read for "base": the Action names it, so it lands where the form looked.
    on_base = {"change": change, "net_id": "base", "use_selection": True}
    assert calls[3] == ["dispatch", {"type": "project_cards.preview", **on_base}]
    assert got["afterPreview"] == {"preview": "net-2"}
    assert calls[4] == ["dispatch", {"type": "project_cards.add_change", **on_base}]
    assert calls[5] == ["dispatch", {"type": "project_cards.close_preview"}]  # adding closes the preview
    assert calls[6] == ["get", "/commit-check?project=Lanes&net_id=base"]
    assert calls[7] == [
        "dispatch",
        {
            "type": "project_cards.commit",
            "project": "Lanes",
            "path": "cards/lanes.yml",
            "tags": [],
            "confirm_warnings": True,
        },
    ]  # the dialog showed a warning: committing confirms it
    assert len(calls) == 8
    assert got["compose"] is None and got["preview"] is None


def test_a_refused_add_keeps_the_form_and_the_preview(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
await ctl.preview({ options: { clean_nodes: false } });
refuse.add("project_cards.add_change");
let error = null;
try { await ctl.add({ options: {} }); } catch (e) { error = e.message; }
const kept = { compose: ctl.state.compose !== null, preview: ctl.state.preview };
await ctl.cancel();
await ctl.start("roadway_addition");
const addition = ctl.changeFrom({ link: { link_id: 340, from_node_id: 1, to_node_id: 2 }, node: {} });
await ctl.undo();
await ctl.commit({ project: "No check", path: "a.yml" });
await ctl.importCard("cards/a.yml");
await ctl.cancel();  // no preview open: nothing to close
console.log(JSON.stringify({ calls, error, kept, addition, after: ctl.state }));
""",
    )
    calls = got["calls"]
    assert calls[0] == ["get", "/form?type=roadway_deletion&saved=ski"]
    deletion = {"type": "roadway_deletion", "clean_nodes": False}
    assert calls[1][1] == {
        "type": "project_cards.preview",
        "change": deletion,
        "net_id": "base",
        "saved_selection": "ski",
    }
    assert got["error"] == "project_cards.add_change refused"
    assert got["kept"] == {"compose": True, "preview": {"preview": "net-2"}}  # nothing closed on a refusal
    assert calls[3] == ["dispatch", {"type": "project_cards.close_preview"}]  # cancel closes the preview
    assert calls[4] == ["get", "/form?type=roadway_addition"]
    assert got["addition"] == {
        "type": "roadway_addition",
        "links": [{"link_id": 340, "from_node_id": 1, "to_node_id": 2}],
        "nodes": [],
    }
    assert calls[5:] == [
        ["dispatch", {"type": "project_cards.undo_change"}],
        # No check was read: nothing was shown, so nothing is confirmed.
        [
            "dispatch",
            {"type": "project_cards.commit", "project": "No check", "path": "a.yml", "confirm_warnings": False},
        ],
        ["dispatch", {"type": "project_cards.import", "path": "cards/a.yml"}],
    ]
    assert got["after"] == {"compose": None, "preview": None, "check": None}
