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
                 plugins: { project_cards: { drafts, preview: null, catalog: null, applied: [] } } };
const store = createStore({ server });
const deps = {
  hostApi: "1.7", store, activeSelection, events: hub, actionTypes: new Set(), actionSchema: () => null,
  frozenCopy: x => JSON.parse(JSON.stringify(x)),
  getJSON: async p => { calls.push(["get", p]);
    return p.includes("transit") ? { supported: false, message: "transit_routing_change is not yet supported" }
                                 : { supported: true, existing: {}, targets: { label: "2 picked links" } }; },
  postJSON: async () => ({}),
  fetch: async p => { calls.push(["fetch", p]); return { ok: true, text: async () => "SUMMARY TEXT" }; },
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
    assert got["panels"] == [["cards", "*", "Cards"]]  # the bare id core reserves for this plugin's tab
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
    assert calls[1] == ["show", "cards"]
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
// A button by its text, its tooltip (an icon button's data-tip) or its accessible name.
const button = (root, text) => walk(root).find(e => e.tagName === "BUTTON" && (e.textContent === text ||
  e.getAttribute("data-tip") === text || e.getAttribute("aria-label") === text));
const elById = (root, id) => walk(root).find(e => e.id === id);
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
    if (p.includes("/catalog")) return LISTING;
    if (p.includes("roadway")) return { supported: true, net_id: "base", existing: {}, targets: { label: "2" },
                                         columns: {}, options: {} };""",
    )
    .replace(
        "const slots = createSlots()",
        """const CHECKS = {
  good: { ok: true, net_id: "base", version: 1,
          changes: [{ describe: "lanes on 2 links", resolved: 2, missing: [], reason: null }] },
  bad: { ok: false, net_id: "base", version: 1,
         changes: [{ describe: "lanes on 3 links", resolved: 2, missing: [999999], reason: null }] },
};
const entry = (card_id, project, applicability) => ({ card_id, path: `/cards/${card_id}`, project, tags: ["t"],
                                                      change_count: 1, applicability });
const CATALOG = { path: "/cards", rejected: [{ card_id: "notes.yml", path: "/cards/notes.yml", error: "not a card" }],
                  cards: [entry("good.yml", "Good", null), entry("bad.yml", "Bad", null)] };
const LISTING = { ...CATALOG, cards: [entry("good.yml", "Good", CHECKS.good), entry("bad.yml", "Bad", CHECKS.bad)] };
const slots = createSlots()""",
    )
    .replace("schemaForm: () => ({ value: () => ({}),", "schemaForm: () => ({ value: () => ({ lanes: 3 }),")
    + FAKE_DOM
    + """
const hold = {};
const root = document.createElement("div");
const { wb } = createWb("project_cards", deps);
activate(wb);
const byId = Object.fromEntries(slots.commands.list().map(c => [c.id, c]));
const settle = () => new Promise(r => setTimeout(r, 0));
"""
)
PANEL_SETUP = PANEL_HARNESS
PANEL_HARNESS = (
    PANEL_SETUP
    + """
const two = { selectionCount: 2, state: server, network: { id: "base" } };
const dispatched = () => calls.filter(c => c[0] === "dispatch").map(c => c[1]);
const setServer = patch => store.set({ server: { ...store.get().server, ...patch } });
const cardsWith = d => ({ project_cards: { drafts: { base: d }, preview: null } });
const got = {};

// 1. An unsupported command leaves the open form on screen, and working.
await byId["project_cards.new_roadway_property_change"].run(two);  // the first show draws the panel
const [compose, draftBox, commitBox, status] =
  ["pc-compose", "pc-draft", "pc-commit", "pc-status"].map(id => elById(root, id));
const dialog = find(root, "DIALOG");
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
got.reloaded = [[...reads], ...shown()];

// 8. The four sections, in order; the catalog lists its cards with badges once the listing is read.
got.sections = root.children.filter(e => e.tagName === "H3").map(e => e.textContent);
const catalogBox = elById(root, "pc-catalog"), appliedBox = elById(root, "pc-applied");
got.noCatalog = catalogBox.textContent.includes("No catalog open");
const applied = [{ card_id: "lanes", net_id: "base", path: "cards/lanes.yml", project: "Lanes",
                   applied_at: "2026-10-21T09:05:00+00:00", changes: 2, source: "committed", edit_ids: [1, 2] }];
setServer({ plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied } } });
await settle(); await settle();  // the listing is read after the store change
got.noApply = walk(catalogBox).filter(e => e.getAttribute("data-card")).map(c => Boolean(button(c, "Apply…")));
setServer({ plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied, enable_apply: true } } });
await settle(); await settle();
const cards = walk(catalogBox).filter(e => e.getAttribute("data-card"));
got.cards = cards.map(c => [c.getAttribute("data-card"), find(c, "SPAN").getAttribute("data-badge"),
                            find(c, "SPAN").getAttribute("title")]);
got.catalogLine = elById(root, "pc-catalog-line").textContent;
got.rejected = find(catalogBox, "SUMMARY", "Not cards (1)") !== undefined;
got.applied = walk(appliedBox).filter(e => e.tagName === "LI").map(e => e.textContent);

// 9. Apply… on a ✗ card opens the options dialog with ignore_missing checked; Apply dispatches apply_card.
await button(cards[1], "Apply…").onclick();
const ignore = elById(dialog, "pc-ignore"), conflict = elById(dialog, "pc-conflict");
got.applyDialog = [dialog.open, ignore.checked, conflict.value, find(dialog, "H3").textContent];
await button(dialog, "Apply").onclick();
const applyAction = calls.find(c => c[0] === "dispatch" && c[1] === "project_cards.apply_card");
got.applied2 = [dialog.open, Boolean(applyAction), status.textContent.startsWith("Applied")];
await button(cards[0], "Apply…").onclick();
got.applyGood = [dialog.open, elById(dialog, "pc-ignore").checked];
dialog.close();

// 10. Preview highlights the card's links through wb.highlight; closing the preview clears them.
hub.emit("project_cards.card_selection", { card_id: "good.yml", links: [1, 2], nodes: [] });
setServer({ plugins: { project_cards: { drafts: {}, preview: { net_id: "base-2", base: "base", describe: "card",
  stale: false }, catalog: CATALOG, applied } } });
got.highlighted = [...store.get().highlights];
got.keep = store.get().highlightsKeep; // survives the preview network's switch once
got.backLine = Boolean(button(elById(root, "pc-preview"), "Back to the network"));
setServer({ plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied } } });
got.cleared = [...store.get().highlights];
got.keepCleared = store.get().highlightsKeep;

// 11. Copy reads the summary route and writes it to the clipboard.
const copied = [];
Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async t => copied.push(t) } },
                                                  configurable: true });
await button(elById(root, "pc-summary"), "Copy the session summary").onclick();
got.copied = [copied, calls.filter(c => c[0] === "fetch").map(c => c[1]), status.textContent];
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
    # The catalog, applied and summary sections.
    assert got["sections"] == ["Catalog", "Draft", "Applied this session", "Session summary"]
    assert got["noCatalog"] is True
    assert got["cards"] == [
        ["good.yml", "✓", "Applies to base"],
        ["bad.yml", "✗", "lanes on 3 links: missing 999999"],
    ]
    assert got["catalogLine"] == "/cards" and got["rejected"] is True
    assert got["applied"] == ["Lanes [committed] 2 change(s) on base at 09:05"]
    assert got["noApply"] == [False, False]  # enable_apply is off: no Apply…
    assert got["applyDialog"] == [True, True, "error", 'Apply "Bad"']
    assert got["applied2"] == [False, True, True]
    assert got["applyGood"] == [True, False]
    assert got["highlighted"] == [1, 2] and got["backLine"] is True
    assert got["cleared"] == []
    assert got["keep"] is True and got["keepCleared"] is False
    assert got["copied"] == [
        ["SUMMARY TEXT"],
        ["/api/plugins/project_cards/summary.txt?net_id=base"],
        "Copied the session summary",
    ]


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
const compose = elById(root, "pc-compose"), status = elById(root, "pc-status");
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


CREATE_HARNESS = (
    PANEL_SETUP
    + """
panels[0].render(root);
const create = elById(root, "pc-create"), compose = elById(root, "pc-compose");
const setServer = patch => store.set({ server: { ...store.get().server, ...patch } });
const steps = () => walk(create).filter(e => e.getAttribute("data-step"))
  .map(e => `${e.getAttribute("data-step")}:${e.getAttribute("data-state")}`);
const got = {};
await button(create, "New card").onclick();
got.types = walk(create).filter(e => e.tagName === "BUTTON").map(b => [b.textContent, b.disabled]);
got.typeSteps = steps();
await button(create, "Property change").onclick();
got.selectSteps = steps();
got.none = [elById(create, "pc-select-count").textContent, button(create, "Use these 0 links").disabled];
setServer({ selection: { net_id: "base", status: "resolved", link_ids: [4, 5], projectcard: { picked: true } },
            saved_selections: [{ name: "ski", net_id: "base", links: 3 }] });
got.live = [elById(create, "pc-select-count").textContent, button(create, "Use these 2 links").disabled,
            Boolean(button(create, "Use saved"))];
elById(create, "pc-utterance").value = "main street";
await button(create, "Select").onclick();
await button(create, "Use these 2 links").onclick();
got.detailsSteps = steps();
got.form = compose.textContent.startsWith("New change: Property change");
await button(compose, "Preview").onclick();
got.addSteps = steps();
await button(compose, "Add to card").onclick();
got.added = [steps(), create.textContent.includes("Added to the draft card")];
await button(create, "Add another change").onclick();
await button(create, "Addition").onclick();
got.addition = [steps(), compose.textContent.startsWith("New change: Addition")];
await button(create, "Cancel").onclick();
got.cancelled = [Boolean(button(create, "New card")), compose.children.length];
got.dispatched = calls.filter(c => c[0] === "dispatch").map(c => c[1]);
got.errors = calls.filter(c => c[0] === "error");
console.log(JSON.stringify(got));
"""
)


def test_the_new_card_mode_under_a_fake_dom(run_js):
    got = run_js(FILES, CREATE_HARNESS)
    assert got["errors"] == []
    assert got["types"][:3] == [["Property change", False], ["Deletion", False], ["Addition", False]]
    assert ["Transit routing change (not yet supported)", True] in got["types"]
    assert got["types"][-1] == ["Cancel", False]
    assert got["typeSteps"] == ["type:current", "select:todo", "details:todo", "add:todo"]
    assert got["selectSteps"] == ["type:done", "select:current", "details:todo", "add:todo"]
    assert got["none"] == ["Nothing selected on this network yet.", True]
    assert got["live"] == ["2 links selected.", False, True]  # the count follows the shared selection
    assert got["detailsSteps"] == ["type:done", "select:done", "details:current", "add:todo"]
    assert got["form"] is True
    assert got["addSteps"] == ["type:done", "select:done", "details:done", "add:current"]
    assert got["added"] == [["type:done", "select:done", "details:done", "add:done"], True]
    assert got["addition"] == [["type:done", "select:skipped", "details:current", "add:todo"], True]
    assert got["cancelled"] == [True, 0]
    assert got["dispatched"] == ["select", "project_cards.preview", "project_cards.add_change"]


INSPECT_HARNESS = (
    PANEL_SETUP
    + """
const change = (index, links) => ({ index, type: "roadway_property_change", note: null,
  facility: { table: "link", ids: links }, property_changes: { lanes: { set: 3, existing: 1 } },
  targets: { links, nodes: [] }, check: { describe: "x", resolved: links.length, missing: [], reason: null } });
const DETAIL = { source: "catalog", card_id: "good.yml", path: "/cards/good.yml", net_id: "base", project: "Good",
  tags: ["t"], dependencies: { prerequisites: [], corequisites: [], conflicts: [] }, notes: null, applies: true,
  yaml: "project: Good\\n", changes: [change(0, [1, 2]), change(1, [])] };
const DRAFT = { ...DETAIL, source: "draft", card_id: null, path: null, changes: [{ ...change(0, [7]), check: null }] };
const read = deps.getJSON;
deps.getJSON = async p => {
  if (!p.includes("/card?")) return read(p);
  calls.push(["get", p]);
  return p.includes("draft") ? DRAFT : DETAIL;
};
panels[0].render(root);
const setServer = patch => store.set({ server: { ...store.get().server, ...patch } });
const withDraft = n => ({ project_cards: { drafts: { base: { count: n, project: "", changes: [] } }, preview: null,
                                           catalog: CATALOG, applied: [] } });
setServer({ plugins: withDraft(1) });
await settle(); await settle();
const inspectBox = elById(root, "pc-inspect"), status = elById(root, "pc-status");
const card = walk(elById(root, "pc-catalog")).find(e => e.getAttribute("data-card") === "good.yml");
const got = {};
await button(card, "Inspect").onclick();
const shows = text => inspectBox.textContent.includes(text);
got.view = [shows("Inspect: good.yml"), shows("Project: Good"), shows("lanes: 1 → 3"),
            shows("Applies: 2 records found"), !elById(inspectBox, "pc-inspect-yaml")];
button(inspectBox, "YAML").onclick();
got.yaml = [elById(inspectBox, "pc-inspect-yaml").textContent, button(inspectBox, "YAML").getAttribute("aria-pressed")];
const pick = i => walk(inspectBox).find(e => e.tagName === "BUTTON" && e.getAttribute("data-index") === String(i));
pick(0).onclick();
got.highlighted = [[...store.get().highlights], status.textContent];
pick(1).onclick();
got.nothing = status.textContent;
await button(inspectBox, "Close").onclick();
got.closed = [inspectBox.children.length, [...store.get().highlights]];
await button(elById(root, "pc-draft"), "Inspect").onclick();
got.draft = [inspectBox.textContent.includes("Inspect: the draft on base"),
             inspectBox.textContent.includes("In the draft: applied to base")];
setServer({ plugins: withDraft(2) });  // the draft changed: the view reads it again
await settle(); await settle();
deps.getJSON = async p => {
  if (p.includes("/card?")) throw new Error("the draft card on base has no changes");
  return read(p);
};
setServer({ plugins: withDraft(0) });  // emptied: the view closes
await settle(); await settle();
got.emptied = inspectBox.children.length;
got.reads = calls.filter(c => c[0] === "get" && c[1].includes("/card?")).map(c => c[1]);
got.errors = calls.filter(c => c[0] === "error");
console.log(JSON.stringify(got));
"""
)


def test_inspect_under_a_fake_dom(run_js):
    got = run_js(FILES, INSPECT_HARNESS)
    assert got["errors"] == []
    assert got["view"] == [True, True, True, True, True]
    assert got["yaml"] == ["project: Good\n", "true"]
    assert got["highlighted"] == [
        [1, 2],
        "Highlighted 2 links of change 1 on the map (the map does not zoom to them)",
    ]
    assert got["nothing"] == "Change 2 has no links to highlight"
    assert got["closed"] == [0, []]  # closing clears the highlights it made
    assert got["draft"] == [True, True]
    assert got["emptied"] == 0
    assert got["reads"] == [
        "/api/plugins/project_cards/card?card_id=good.yml&net_id=base",
        "/api/plugins/project_cards/card?draft=true&net_id=base",
        "/api/plugins/project_cards/card?draft=true&net_id=base",
    ]


EDIT_HARNESS = (
    PANEL_SETUP
    + """
const actions = [];
const real = deps.dispatch;
deps.dispatch = async a => { actions.push(a); return a.type === "project_cards.edit_card"
  ? { card_id: a.card_id, count: 2 } : a.type === "project_cards.remove_change"
  ? { removed: "lanes on link 2", readded: 0 } : real(a); };
panels[0].render(root);
const setServer = patch => store.set({ server: { ...store.get().server, ...patch } });
const source = { card_id: "good.yml", path: "/cards/good.yml" };
const DRAFT = { count: 2, project: "Good", tags: ["t", "u"], source,
  changes: [{ index: 0, describe: "lanes on link 1" }, { index: 1, describe: "lanes on link 2" }] };
setServer({ plugins: { project_cards: { drafts: { base: DRAFT }, preview: null, catalog: CATALOG, applied: [] } } });
await settle(); await settle();
const draftBox = elById(root, "pc-draft"), commitBox = elById(root, "pc-commit"), status = elById(root, "pc-status");
const dialog = find(root, "DIALOG");
const got = {};
got.source = elById(draftBox, "pc-draft-source").textContent;
got.filled = ["pc-project", "pc-tags", "pc-path"].map(id => elById(commitBox, id).value);
const second = walk(draftBox).find(e => e.getAttribute("data-change") === "1");
await button(second, "Remove").onclick();
got.removed = [actions.at(-1), status.textContent];
await button(commitBox, "Commit…").onclick();
const choice = key => walk(dialog).find(e => e.getAttribute("data-choice") === key);
const confirm = find(dialog, "INPUT");
confirm.checked = true; confirm.onchange();
got.choices = [choice("over").textContent, choice("over").disabled, choice("new").textContent, choice("new").disabled,
               dialog.textContent.includes("Give a different card file")];
await choice("over").onclick();
got.savedOver = [dialog.open, actions.at(-1)];
// Edit on a catalog card while the draft has changes asks first, then replaces the draft.
const card = walk(elById(root, "pc-catalog")).find(e => e.getAttribute("data-card") === "bad.yml");
await button(card, "Edit").onclick();
got.ask = [dialog.open, find(dialog, "H3").textContent, dialog.textContent.includes("Edit undoes them")];
await button(dialog, "Replace the draft").onclick();
got.edited = [dialog.open, actions.at(-1), status.textContent];
// With an empty draft and a card that applies, Edit asks nothing.
setServer({ plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied: [] } } });
await settle(); await settle();
const good = walk(elById(root, "pc-catalog")).find(e => e.getAttribute("data-card") === "good.yml");
await button(good, "Edit").onclick();
got.direct = [dialog.open, actions.at(-1)];
got.errors = calls.filter(c => c[0] === "error");
console.log(JSON.stringify(got));
"""
)


def test_editing_a_card_under_a_fake_dom(run_js):
    got = run_js(FILES, EDIT_HARNESS)
    assert got["errors"] == []
    assert got["source"] == "Editing good.yml (/cards/good.yml)"
    assert got["filled"] == ["Good", "t, u", "/cards/good.yml"]  # the commit fields take the card's
    assert got["removed"] == [
        {"type": "project_cards.remove_change", "index": 1, "net_id": "base"},
        "Removed: lanes on link 2",
    ]
    assert got["choices"] == ["Save over good.yml", False, "Save as new file", True, True]
    assert got["savedOver"][0] is False
    commit = got["savedOver"][1]
    assert commit["type"] == "project_cards.commit" and commit["path"] == "/cards/good.yml"
    assert commit["overwrite"] is True and commit["project"] == "Good" and commit["tags"] == ["t", "u"]
    assert got["ask"] == [True, 'Edit "Bad"', True]
    assert got["edited"] == [
        False,
        {"type": "project_cards.edit_card", "card_id": "bad.yml", "replace_draft": True, "net_id": "base"},
        "Editing bad.yml: 2 change(s) in the draft. Save over it from Commit…",
    ]
    assert got["direct"] == [
        False,
        {"type": "project_cards.edit_card", "card_id": "good.yml", "replace_draft": False, "net_id": "base"},
    ]


FILES_HARNESS = (
    PANEL_SETUP
    + """
const actions = [];
const RESULTS = {
  "project_cards.rename_card": a => ({ action: "renamed", card_id: a.card_id, to: a.new_name }),
  "project_cards.duplicate_card": a => ({ action: "duplicated", card_id: a.card_id, to: "good-copy.yml" }),
  "project_cards.delete_card": a => ({ action: "deleted", card_id: a.card_id, to: ".trash/" }),
  "project_cards.restore_card": a => ({ action: "restored", card_id: a.card_id, to: a.card_id }),
  "project_cards.update_card_meta": a => ({ action: "updated", card_id: a.card_id, to: "project, tags" }),
};
deps.dispatch = async a => { actions.push(a); if (a.type === "project_cards.rename_card" && a.new_name === "bad.yml")
  throw new Error("bad.yml already exists in the catalog"); return RESULTS[a.type](a); };
const read = deps.getJSON;
deps.getJSON = async p => (p.includes("/card?") ? { project: "Good", tags: ["t"],
  dependencies: { prerequisites: ["Bridge"], corequisites: [], conflicts: [] } } : read(p));
panels[0].render(root);
store.set({ server: { ...store.get().server,
  plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied: [] } } } });
await settle(); await settle();
const status = elById(root, "pc-status"), dialog = find(root, "DIALOG");
const row = () => walk(elById(root, "pc-catalog")).find(e => e.getAttribute("data-card") === "good.yml");
const file = () => row();
const got = {};
// Rename: a refusal stays in the dialog; then it goes through.
await button(file(), "Rename…").onclick();
got.renameStart = [dialog.open, elById(dialog, "pc-rename").value];
elById(dialog, "pc-rename").value = "good.yml";
await button(dialog, "Rename").onclick();
got.sameName = [dialog.open, dialog.textContent.includes("already has that name")];
elById(dialog, "pc-rename").value = "bad.yml";
await button(dialog, "Rename").onclick();
got.refused = [dialog.open, dialog.textContent.includes("bad.yml already exists")];
elById(dialog, "pc-rename").value = "main-street.yml";
await button(dialog, "Rename").onclick();
got.renamed = [dialog.open, status.textContent];
// Duplicate needs no dialog.
await button(file(), "Duplicate").onclick();
got.duplicated = status.textContent;
// Edit metadata: prefilled from the card, saved with every field.
await button(file(), "Edit metadata…").onclick();
got.metaStart = ["project", "tags", "prerequisites"].map(k => elById(dialog, `pc-meta-${k}`).value);
elById(dialog, "pc-meta-project").value = "";
await button(dialog, "Save").onclick();
got.noProject = [dialog.open, dialog.textContent.includes("Give the card a project name")];
elById(dialog, "pc-meta-project").value = "Good street";
elById(dialog, "pc-meta-tags").value = "t, bike";
await button(dialog, "Save").onclick();
got.meta = [dialog.open, status.textContent];
// Delete, then Undo delete from the status line.
await button(file(), "Delete").onclick();
got.deleted = [status.textContent.startsWith("Moved good.yml to the catalog's .trash/ folder"),
               Boolean(button(status, "Undo delete"))];
await button(status, "Undo delete").onclick();
got.restored = status.textContent;
// The Details toggle shows the card's file, and stays open across a redraw.
const details = () => walk(row()).find(e => e.getAttribute("class") === "pc-details");
const before = details();
got.detailsStart = [before.hidden, button(row(), "Details").getAttribute("aria-expanded")];
button(row(), "Details").onclick();
got.detailsOpen = [before.hidden, button(row(), "Details").getAttribute("aria-expanded"),
                   before.textContent.includes("/cards/good.yml")];
store.set({ server: { ...store.get().server,
  plugins: { project_cards: { drafts: {}, preview: null, catalog: { ...CATALOG }, applied: [], files: [{}] } } } });
got.stillOpen = [details() !== before, details().hidden];
got.iconButtons = walk(row()).filter(e => e.tagName === "BUTTON").map(b => [b.getAttribute("class"),
  b.getAttribute("data-tip"), b.getAttribute("aria-label")]);
got.actions = actions;
got.errors = calls.filter(c => c[0] === "error");
console.log(JSON.stringify(got));
"""
)


def test_the_catalog_files_under_a_fake_dom(run_js):
    got = run_js(FILES, FILES_HARNESS)
    assert got["errors"] == []
    assert got["renameStart"] == [True, "good.yml"]
    assert got["sameName"] == [True, True]  # caught here: nothing dispatched
    assert got["refused"] == [True, True]
    assert got["renamed"] == [False, "Renamed good.yml to main-street.yml"]
    assert got["duplicated"] == "Duplicated good.yml as good-copy.yml"
    assert got["metaStart"] == ["Good", "t", "Bridge"]
    assert got["noProject"] == [True, True]
    assert got["meta"] == [False, "Saved good.yml's project, tags"]
    assert got["deleted"] == [True, True]
    assert got["restored"] == "Restored good.yml"
    assert got["detailsStart"] == [True, "false"]
    assert got["detailsOpen"] == [False, "true", True]
    assert got["stillOpen"] == [True, False]  # redrawn, and open again
    # Every action of a card is an icon button with a tooltip, named for the card it acts on.
    assert got["iconButtons"] == [
        ["iconbtn", "Preview", "Preview Good (good.yml)"],
        ["iconbtn", "Inspect", "Inspect Good (good.yml)"],
        ["iconbtn", "Edit", "Edit Good (good.yml) in the draft"],
        ["iconbtn on", "Details", "Details of Good (good.yml)"],
        ["iconbtn", "Rename…", "Rename Good (good.yml)…"],
        ["iconbtn", "Duplicate", "Duplicate Good (good.yml)"],
        ["iconbtn", "Edit metadata…", "Edit the metadata of Good (good.yml)…"],
        ["iconbtn", "Delete", "Delete Good (good.yml)"],
    ]
    assert got["actions"] == [
        {"type": "project_cards.rename_card", "card_id": "good.yml", "new_name": "bad.yml", "overwrite": False},
        {"type": "project_cards.rename_card", "card_id": "good.yml", "new_name": "main-street.yml", "overwrite": False},
        {"type": "project_cards.duplicate_card", "card_id": "good.yml"},
        {
            "type": "project_cards.update_card_meta",
            "card_id": "good.yml",
            "project": "Good street",
            "tags": ["t", "bike"],
            "dependencies": {"prerequisites": ["Bridge"], "corequisites": [], "conflicts": []},
        },
        {"type": "project_cards.delete_card", "card_id": "good.yml"},
        {"type": "project_cards.restore_card", "card_id": "good.yml"},
    ]


ICONS_HARNESS = (
    PANEL_SETUP
    + """
// A host that draws icons: core's by name (one it lacks throws, as core's icon() does), the panel's own for the rest.
deps.icon = name => { if (name === "search") throw new Error(`unknown icon "${name}"`); return `<svg>${name}</svg>`; };
panels[0].render(root);
store.set({ server: { ...store.get().server,
  plugins: { project_cards: { drafts: {}, preview: null, catalog: CATALOG, applied: [] } } } });
await settle(); await settle();
const row = walk(elById(root, "pc-catalog")).find(e => e.getAttribute("data-card") === "good.yml");
const shown = tip => { const b = button(row, tip); return [b.innerHTML || null, b.textContent]; };
console.log(JSON.stringify({ preview: shown("Preview"), trash: shown("Delete"), missing: shown("Inspect"),
  errors: calls.filter(c => c[0] === "error") }));
"""
)


def test_icon_buttons_draw_the_hosts_icons_and_fall_back_to_text(run_js):
    got = run_js(FILES, ICONS_HARNESS)
    assert got["errors"] == []
    assert got["preview"] == ["<svg>play</svg>", ""]  # core's icon
    assert got["trash"][0].startswith("<svg") and "<polyline" in got["trash"][0]  # the panel's own
    assert got["missing"] == [None, "Inspect"]  # a name the host lacks: the text
