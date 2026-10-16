"""The hello example's front end, run under node against the real wb factory (Workbench plugins, Part 2)."""

import shutil
from pathlib import Path

import pytest
from netstead.workbench.server import STATIC_DIR

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HELLO_JS = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "workbench-plugin-hello"
    / "netstead_hello"
    / "static"
    / "main.js"
)
MODULES = ("wbhost.js", "slots.js", "layers.js", "store.js", "hub.js")

HARNESS = """
import { activate } from "./hello.js";
import { createWb } from "./wbhost.js";
import { createSlots } from "./slots.js";
import { createLayerRegistry } from "./layers.js";
import { activeSelection, createStore } from "./store.js";
import { createHub } from "./hub.js";

export async function run() {
  const slots = createSlots(), hub = createHub(), calls = [], panels = [];
  const store = createStore({ server: { networks: [], active: null, selection: null, plugins: {} } });
  const deps = {
    hostApi: "1.1", store, activeSelection, events: hub, actionTypes: new Set(["hello.greet"]),
    actionSchema: t => ({ properties: { type: { const: t }, name: { type: "string" } }, required: ["name"] }),
    getJSON: async p => { calls.push(["get", p]); return { greeted: 2 }; },
    postJSON: async () => ({}), fetch: async () => ({}),
    dispatch: async a => { calls.push(["dispatch", a.type, a.name]); return `Hello, ${a.name}!`; },
    addCommand: (o, s) => slots.addCommand(o, s), layers: createLayerRegistry(),
    dock: { addWorkspace: (o, s) => slots.addWorkspace(o, s),
            addPanel: (o, s) => { panels.push(s); return { dispose: slots.addPanel(o, s), refreshBadge() {} }; },
            showPanel() {} },
    schemaForm: () => ({ value: () => ({ name: "Ada" }), errors: () => [] }),
    toast: m => calls.push(["toast", m]),
    onError: (id, phase, e) => calls.push(["error", id, phase, String(e.message || e)]),
  };
  const { wb } = createWb("hello", deps);
  activate(wb);
  await new Promise(r => setTimeout(r, 0));
  const ctx = { target: { table: "link", id: 7 }, selection: { link_ids: [1, 2] }, selectionCount: 2 };
  const whens = slots.commands.list().map(c => (c.when ? c.when(ctx) : true));
  for (const c of slots.commands.list()) await c.run(ctx);
  hub.emit("hello.greeted", { name: "Ada" });
  await new Promise(r => setTimeout(r, 0));
  return { panels: slots.panels.list().map(p => [p.id, p.workspace]),
           commands: slots.commands.list().map(c => [c.id, c.contexts]),
           whens, badge: panels[0].badge(), calls };
}
"""


def test_hello_registers_a_panel_three_commands_and_a_live_badge(node_module, tmp_path):
    root = tmp_path / "hello"
    root.mkdir()
    for name in MODULES:
        shutil.copy(STATIC_DIR / "js" / name, root / name)
    shutil.copy(HELLO_JS, root / "hello.js")
    (root / "harness.js").write_text(HARNESS, encoding="utf-8")
    got = node_module(root / "harness.js", ["run"], "await run()")
    assert got["panels"] == [["hello.panel", "inspect"]]
    assert got["commands"] == [
        ["hello.greet", ["palette"]],
        ["hello.greet_record", ["feature", "row"]],
        ["hello.greet_selection", ["selection"]],
    ]
    assert got["whens"] == [True, True, True]
    assert got["badge"] == 2  # read from GET /api/plugins/hello/count after the "greeted" event
    assert got["calls"] == [
        ["get", "/api/plugins/hello/count"],
        ["dispatch", "hello.greet", "world"],
        ["dispatch", "hello.greet", "link 7"],
        ["dispatch", "hello.greet", "2 selected links"],
        ["get", "/api/plugins/hello/count"],
    ]
