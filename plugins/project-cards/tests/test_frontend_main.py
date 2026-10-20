"""main.js under node, against the Workbench's real wb factory and slots (registration, whens, badge, commands).

HARNESS runs `activate(wb)` with no `document`, which pins that activating builds no DOM: the panel draws itself the
first time it shows. PANEL_HARNESS then draws the panel into a small fake DOM (only what main.js uses) and clicks
through it; the real look and the native <dialog> are checked in a browser.
"""

import json
from pathlib import Path

import netstead_project_cards
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead.workbench.server import STATIC_DIR

STATIC = Path(netstead_project_cards.__file__).parent / "static"
FORM = "/api/plugins/project_cards/form"
CORE = ("wbhost.js", "slots.js", "layers.js", "store.js", "hub.js", "commands.js")
FILES = {
    **{name: STATIC_DIR / "js" / name for name in CORE},
    **{name: STATIC / name for name in ("main.js", "cardmodel.js", "controller.js")},
}

HARNESS = """
import { activate } from "./main.js";
import { createWb } from "./wbhost.js";
import { createSlots } from "./slots.js";
import { createLayerRegistry } from "./layers.js";
import { activeSelection, createStore } from "./store.js";
import { createHub } from "./hub.js";
import { menuSections, paletteGroups } from "./commands.js";

const slots = createSlots(), hub = createHub(), calls = [], panels = [];
const drafts = { base: { count: 2, project: "", changes: [] } };
const preview = { net_id: "base-2", base: "base", describe: "x", stale: false };
const server = { networks: [], active: "base", selection: null,
                 plugins: { project_cards: { drafts, preview: null } } };
const store = createStore({ server });
const deps = {
  hostApi: "1.5", store, activeSelection, events: hub, actionTypes: new Set(), actionSchema: () => null,
  frozenCopy: x => JSON.parse(JSON.stringify(x)),
  getJSON: async p => { calls.push(["get", p]);
    return p.includes("transit") ? { supported: false, message: "transit_routing_change is not yet supported" }
                                 : { supported: true, existing: {}, targets: { label: "2 picked links" } }; },
  postJSON: async () => ({}), fetch: async () => ({}),
  dispatch: async a => { calls.push(["dispatch", a.type, ...(a.net_id ? [a.net_id] : [])]); return {}; },
  addCommand: (owner, spec) => slots.addCommand(owner, spec), layers: createLayerRegistry(),
  dock: { addWorkspace: (o, s) => slots.addWorkspace(o, s),
          addPanel: (o, s) => { panels.push(s); return { dispose: slots.addPanel(o, s), refreshBadge() {} }; },
          showPanel: id => calls.push(["show", id]) },
  schemaForm: () => ({ value: () => ({}), errors: () => [], set() {}, focus() {} }),
  toast: m => calls.push(["toast", m]),
  onError: (id, phase, e) => calls.push(["error", id, phase, String(e.message || e)]),
};
const { wb } = createWb("project_cards", deps);
activate(wb);
const commands = slots.commands.list();
const byId = Object.fromEntries(commands.map(c => [c.id, c]));
const ctxOf = (n, state = server, net = "base") => ({ selectionCount: n, state, network: { id: net } });
const none = ctxOf(0), two = ctxOf(2);
const onPreview = { ...server, active: "base-2", plugins: { project_cards: { drafts, preview } } };
const titles = sections => sections.map(s => [s.title, s.items.map(c => c.id)]);
const target = { table: "link", id: 7 };
const menus = {
  none: titles(menuSections(commands, "feature", { ...none, target })),
  two: titles(menuSections(commands, "feature", { ...two, target })),
  palette: paletteGroups(commands, { ...two, focus: null }, "").map(g => [g.title, g.items.map(i => i.command.id)]),
};
await byId["project_cards.new_roadway_property_change"].run(two);
await byId["project_cards.new_transit_routing_change"].run(two);
await byId["project_cards.undo"].run(two);
console.log(JSON.stringify({
  panels: slots.panels.list().map(p => [p.id, p.workspace, p.title]),
  commands: commands.map(c => [c.id, c.group || null, c.contexts]),
  whenNone: commands.map(c => (c.when ? c.when(none) : true)),
  whenTwo: commands.map(c => (c.when ? c.when(two) : true)),
  badge: panels[0].badge(two),
  badgeOnPreview: panels[0].badge(ctxOf(0, onPreview, "base-2")),
  badgeElsewhere: panels[0].badge(ctxOf(0, server, "other")),
  undoElsewhere: byId["project_cards.undo"].when(ctxOf(0, server, "other")),
  menus, calls,
}));
"""


def test_main_registers_the_panel_and_the_commands(run_js):
    got = run_js(FILES, HARNESS)
    assert got["panels"] == [["project_cards.draft", "*", "Card"]]
    ids = [c[0] for c in got["commands"]]
    assert ids == [
        "project_cards.new_roadway_property_change",
        "project_cards.new_roadway_deletion",
        "project_cards.new_roadway_addition",
        "project_cards.new_transit_property_change",
        "project_cards.new_transit_routing_change",
        "project_cards.new_transit_route_addition",
        "project_cards.new_transit_service_deletion",
        "project_cards.undo",
    ]
    groups_contexts = {c[0]: (c[1], c[2]) for c in got["commands"]}
    assert groups_contexts["project_cards.new_roadway_property_change"] == ("New change", ["selection", "palette"])
    assert groups_contexts["project_cards.new_roadway_addition"] == ("New change", ["palette"])
    assert groups_contexts["project_cards.new_transit_property_change"] == ("New change", ["palette"])
    assert got["whenNone"][:3] == [False, False, True]  # property change and deletion need a selection
    assert got["whenTwo"][:3] == [True, True, True]


def test_the_badge_follows_the_draft_of_the_network_or_the_base_a_preview_stands_for(run_js):
    got = run_js(FILES, HARNESS)
    badge = {"text": "2", "dirty": True, "title": "2 uncommitted card change(s)"}
    assert got["badge"] == badge and got["badgeOnPreview"] == badge
    assert got["badgeElsewhere"] is None and got["undoElsewhere"] is False


def test_the_context_menu_offers_new_change_only_with_a_selection(run_js):
    menus = run_js(FILES, HARNESS)["menus"]
    assert menus["none"] == []  # nothing applies to a right-clicked link without a selection: no menu at all
    assert menus["two"] == [
        ["Selection (2 links)", ["project_cards.new_roadway_property_change", "project_cards.new_roadway_deletion"]]
    ]
    (commands,) = [items for title, items in menus["palette"] if title == "Commands"]
    assert "project_cards.new_roadway_addition" in commands and "project_cards.undo" in commands


def test_commands_open_the_form_or_say_not_yet_supported(run_js):
    calls = run_js(FILES, HARNESS)["calls"]
    assert calls[0] == ["get", "/api/plugins/project_cards/form?type=roadway_property_change&use_selection=true"]
    assert calls[1] == ["show", "project_cards.draft"]
    assert calls[2] == ["get", "/api/plugins/project_cards/form?type=transit_routing_change"]
    assert calls[3] == ["toast", "project_cards: transit_routing_change is not yet supported"]
    assert calls[4] == ["dispatch", "project_cards.undo_change", "base"]
    assert not [c for c in calls if c[0] == "error"]


# Just the DOM main.js touches: elements with attributes, children, text, focus and a <dialog>'s open state.
FAKE_DOM = """
class Text { constructor(t) { this.data = t; this.parent = null; } get textContent() { return this.data; } }
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.parent = null;
    this.style = {}; this.disabled = false; this.checked = false; this.value = ""; this.open = false; }
  get id() { return this.attrs.id || ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  append(...kids) {
    for (const k of kids) { const n = typeof k === "string" ? new Text(k) : k; n.parent = this; this.children.push(n); }
  }
  replaceChildren(...kids) { for (const c of this.children) c.parent = null; this.children = []; this.append(...kids); }
  get textContent() { return this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this.replaceChildren(String(v)); }
  addEventListener() {}
  contains(n) { for (; n; n = n.parent) if (n === this) return true; return false; }
  focus() { document.activeElement = this; }
  showModal() { if (this.open) throw new Error("InvalidStateError: already open"); this.open = true; }
  close() { this.open = false; }
}
globalThis.document = { activeElement: null, createElement: tag => new El(tag) };
const walk = el => [el, ...el.children.filter(c => c instanceof El).flatMap(walk)];
const find = (root, tag, text) =>
  walk(root).find(e => e.tagName === tag && (text === undefined || e.textContent === text));
const button = (root, text) => find(root, "BUTTON", text);
"""

PANEL_HARNESS = (
    HARNESS.split("const { wb } = createWb")[0]
    .replace("dispatch: async a => {", "dispatch: async a => { if (a.type in hold) await hold[a.type];")
    .replace(
        'showPanel: id => calls.push(["show", id])',
        'showPanel: id => { calls.push(["show", id]); if (!root.children.length) panels[0].render(root); }',
    )
    .replace(
        "getJSON: async p => {",
        """getJSON: async p => { if (p.includes("commit-check")) {
      return { net_id: "base", warnings: [{ table: "link", key: 3, message: "x" }], problems: [], yaml: "" }; }
    if (p.includes("roadway")) return { supported: true, net_id: "base", existing: {}, targets: { label: "2" },
                                         columns: {}, options: {} };""",
    )
    .replace("schemaForm: () => ({ value: () => ({}),", "schemaForm: () => ({ value: () => ({ lanes: 3 }),")
    + FAKE_DOM
    + """
const hold = {};
const root = document.createElement("div");
const { wb } = createWb("project_cards", deps);
activate(wb);
const byId = Object.fromEntries(slots.commands.list().map(c => [c.id, c]));
const two = { selectionCount: 2, state: server, network: { id: "base" } };
const dispatched = () => calls.filter(c => c[0] === "dispatch").map(c => c[1]);
const setServer = patch => store.set({ server: { ...store.get().server, ...patch } });
const cardsWith = d => ({ project_cards: { drafts: { base: d }, preview: null } });
const got = {};

// 1. An unsupported command leaves the open form on screen, and working.
await byId["project_cards.new_roadway_property_change"].run(two);  // the first show draws the panel
const [compose, , draftBox, commitBox, status, dialog] = root.children;
await byId["project_cards.new_transit_routing_change"].run(two);
got.formKept = compose.textContent.startsWith("New change: Property change");
await button(compose, "Preview").onclick();
got.afterUnsupported = [dispatched().at(-1), status.textContent];

// 2. A second click while Add is in flight does not add twice; the buttons are disabled meanwhile.
let release;
hold["project_cards.add_change"] = new Promise(r => { release = r; });
const addButton = button(compose, "Add to card");
const clicks = [addButton.onclick(), addButton.onclick()];
got.disabledWhileAdding = ["Preview", "Add to card", "Cancel"].map(t => button(compose, t).disabled);
release();
await Promise.all(clicks);
got.adds = dispatched().filter(t => t === "project_cards.add_change").length;
got.afterAdd = status.textContent;

// 3. A redraw keeps focus in the box it was in.
setServer({ plugins: cardsWith({ count: 1, project: "", changes: [{ describe: "a" }] }) });
button(draftBox, "Undo last change").focus();
setServer({ plugins: cardsWith({ count: 2, project: "", changes: [{ describe: "a" }, { describe: "b" }] }) });
got.focus = [document.activeElement.textContent, draftBox.contains(document.activeElement)];

// 5. The dialog's check goes stale when the draft changes elsewhere: Commit is disabled until checked again.
find(commitBox, "INPUT").value = "Lanes";
walk(commitBox).filter(e => e.tagName === "INPUT")[2].value = "cards/lanes.yml";
await button(commitBox, "Commit…").onclick();
const confirm = find(dialog, "INPUT");
confirm.checked = true; confirm.onchange();
const commitButton = () => button(dialog, "Commit anyway");
got.beforeStale = [dialog.open, commitButton().disabled];
setServer({ plugins: cardsWith({ count: 3, project: "", changes: [] }) });
got.stale = [commitButton().disabled, dialog.textContent.includes("The draft changed since this check")];
await button(dialog, "Check again").onclick();
const confirm2 = find(dialog, "INPUT");
confirm2.checked = true; confirm2.onchange();
got.rechecked = [dialog.open, commitButton().disabled];
let releaseCommit;
hold["project_cards.commit"] = new Promise(r => { releaseCommit = r; });
const commitClick = commitButton().onclick();
got.disabledWhileCommitting = commitButton().disabled;
releaseCommit();
await commitClick;
got.committed = [dialog.open, dispatched().filter(t => t === "project_cards.commit").length];

// 6. An empty import path is caught here.
await button(commitBox, "Import").onclick();
got.emptyImport = [status.textContent, dispatched().includes("project_cards.import")];

// 7. A selection change outdates a form read from the selection: Preview and Add wait for "Reload form".
await byId["project_cards.new_roadway_property_change"].run(two);
button(compose, "Add to card").focus();
setServer({ selection: { net_id: "base", status: "resolved", link_ids: [9], projectcard: { picked: true } } });
const shown = () => [button(compose, "Preview").disabled, button(compose, "Add to card").disabled,
  compose.textContent.includes("The selection changed"), Boolean(button(compose, "Reload form")) &&
  !button(compose, "Reload form").hidden];
got.outdated = [...shown(), document.activeElement.textContent];
const read = deps.getJSON, reads = [];
deps.getJSON = p => { reads.push(p); return read(p); };
await button(compose, "Reload form").onclick();
got.reloaded = [reads, ...shown()];
got.errors = calls.filter(c => c[0] === "error");
console.log(JSON.stringify(got));
"""
)


def test_the_panel_under_a_fake_dom(run_js):
    got = run_js(FILES, PANEL_HARNESS)
    assert got["errors"] == []
    assert got["formKept"] is True
    assert got["afterUnsupported"][0] == "project_cards.preview"
    assert "no change form is open" not in got["afterUnsupported"][1]
    assert got["disabledWhileAdding"] == [True, True, True]
    assert got["adds"] == 1 and got["afterAdd"].startswith("Added")
    assert got["focus"] == ["Undo last change", True]
    assert got["beforeStale"] == [True, False]
    assert got["stale"] == [True, True]
    assert got["rechecked"] == [True, False]
    assert got["disabledWhileCommitting"] is True
    assert got["committed"] == [False, 1]
    assert got["emptyImport"] == ["Give a card or edit log path", False]
    assert got["outdated"] == [True, True, True, True, "Reload form"]  # focus left the now disabled Add
    reread = ["/api/plugins/project_cards/form?type=roadway_property_change&use_selection=true"]
    assert got["reloaded"] == [reread, False, False, False, False]


# The real addition form (from GET /form) through core's real schemaform.js: field parsing and formErrors as the
# Workbench runs them, where the stubs above report no errors at all.
ADDITION_HARNESS = (
    HARNESS.split("const { wb } = createWb")[0]
    .replace(
        'import { activate } from "./main.js";',
        'import { activate } from "./main.js";\nimport { fieldsFrom, fillShown, formErrors } from "./schemaform.js";',
    )
    .replace("getJSON: async p => {", 'getJSON: async p => { if (p.includes("roadway_addition")) return ADDITION;')
    .replace("dispatch: async a => {", "dispatch: async a => { actions.push(a);")
    .replace(
        'showPanel: id => calls.push(["show", id])',
        'showPanel: id => { calls.push(["show", id]); if (!root.children.length) panels[0].render(root); }',
    )
    .replace(
        "schemaForm: () => ({ value: () => ({}), errors: () => [], set() {}, focus() {} }),",
        """schemaForm: (el, schema, start = {}) => {
    const shown = fillShown(fieldsFrom(schema, start), start);
    const form = { current: shown.value, value: () => form.current, set(v) { form.current = v; }, focus() {},
                   errors: () => formErrors(shown.fields, form.current) };
    made.push(form);
    return form;
  },""",
    )
    + FAKE_DOM
    + """
const made = [], actions = [];
const root = document.createElement("div");
const { wb } = createWb("project_cards", deps);
activate(wb);
const byId = Object.fromEntries(slots.commands.list().map(c => [c.id, c]));
await byId["project_cards.new_roadway_addition"].run({ selectionCount: 0, state: server, network: { id: "base" } });
const [compose, , , , status] = root.children;
const [link, node] = made;  // the link, node and options forms, in that order
link.set({ ...link.value(), from_node_id: 1, to_node_id: 2, name: "New St", facility_type: "local", lanes: 1,
           allowed_uses: "all" });
const add = () => button(compose, "Add to card").onclick();
node.set({ node_id: 122 });  // half a node: its coordinates are still required
await add();
const halfNode = [status.textContent, actions.length];
node.set({});  // no node: a link between existing nodes
await add();
console.log(JSON.stringify({ halfNode, linkStart: made[0] === link && link.value().link_id, status: status.textContent,
  actions, errors: calls.filter(c => c[0] === "error") }));
"""
)


def test_an_addition_needs_no_node_but_a_half_filled_node_is_reported(run_js, session):
    response = TestClient(build_app(session)).get(FORM, params={"type": "roadway_addition"})
    assert response.status_code == 200, response.text
    harness = f"const ADDITION = {json.dumps(response.json())};\n" + ADDITION_HARNESS
    got = run_js({**FILES, "schemaform.js": STATIC_DIR / "js" / "schemaform.js"}, harness)
    assert got["errors"] == []
    assert got["halfNode"] == ["x_coord is required; y_coord is required", 0]
    assert got["linkStart"] == 340
    (action,) = got["actions"]
    assert action["type"] == "project_cards.add_change"
    assert action["change"]["nodes"] == []
    assert action["change"]["links"] == [
        {
            "link_id": 340,
            "from_node_id": 1,
            "to_node_id": 2,
            "name": "New St",
            "facility_type": "local",
            "lanes": 1,
            "allowed_uses": "all",
        }
    ]
    assert got["status"].startswith("Added")
