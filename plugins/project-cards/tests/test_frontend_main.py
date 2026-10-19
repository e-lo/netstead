"""main.js under node, against the Workbench's real wb factory and slots (registration, whens, badge, commands).

Its DOM (the panel, the commit dialog) is checked in a browser (Task 17); here `activate(wb)` runs with no
`document`, which also pins that activating builds no DOM: the panel draws itself the first time it shows.
"""

from pathlib import Path

import netstead_project_cards
from netstead.workbench.server import STATIC_DIR

STATIC = Path(netstead_project_cards.__file__).parent / "static"
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
