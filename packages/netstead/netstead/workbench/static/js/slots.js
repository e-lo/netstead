// The front end's extension points: workspaces, dock panels and commands. Each is keyed by id and tagged with the
// owner that registered it ("core" or a plugin id), so a plugin's whole contribution can be found or removed.
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

export const CORE = "core";
export const ALL_WORKSPACES = "*";
export const CONTEXTS = ["palette", "feature", "row", "selection"];
export const VIEWS = ["map", "split", "table"];

// A plugin's ids are namespaced like its Action types ("hello.panel"); core's are bare ("inspect", "details").
export function checkId(owner, id) {
  if (typeof id !== "string" || !id) throw new Error(`${owner}: an id is required`);
  if (owner !== CORE && !id.startsWith(`${owner}.`)) {
    throw new Error(`${owner}: id ${JSON.stringify(id)} must start with "${owner}."`);
  }
  if (owner !== CORE && id === `${owner}.`) throw new Error(`${owner}: id ${JSON.stringify(id)} must name something after "${owner}."`);
}

// One ordered, owner-tagged collection: lower `order` first, then registration order.
export function createRegistry(kind) {
  const items = new Map();
  let seq = 0;
  return {
    add(owner, spec) {
      checkId(owner, spec.id);
      if (items.has(spec.id)) throw new Error(`${kind} ${JSON.stringify(spec.id)} is already registered`);
      const item = { ...spec, order: spec.order ?? 0, owner, seq: seq++ };
      items.set(spec.id, item);
      return item;
    },
    get: id => items.get(id) || null,
    // With `item`, only that registration goes: a stale disposer must not remove a later one with the same id.
    remove: (id, item) => (item === undefined || items.get(id) === item) && items.delete(id),
    removeOwner(owner) { for (const [id, item] of items) if (item.owner === owner) items.delete(id); },
    list: () => [...items.values()].sort((a, b) => a.order - b.order || a.seq - b.seq),
  };
}

function need(spec, kind, fields) {
  if (!spec || typeof spec !== "object") throw new Error(`${kind}: expected an object`);
  const missing = fields.filter(f => spec[f] === undefined || spec[f] === null || spec[f] === "");
  if (missing.length) throw new Error(`${kind} ${JSON.stringify(spec.id ?? "?")}: ${missing.join(", ")} required`);
}

function functions(spec, kind, names) {
  for (const name of names) {
    if (spec[name] !== undefined && typeof spec[name] !== "function") throw new Error(`${kind} ${spec.id}: ${name} must be a function`);
  }
}

// {id, title, layout?: {view?: "map" | "split" | "table"}, badge?(ctx), order?}
export function workspaceSpec(spec) {
  need(spec, "workspace", ["id", "title"]);
  functions(spec, "workspace", ["badge"]);
  const view = spec.layout && spec.layout.view;
  if (view !== undefined && !VIEWS.includes(view)) throw new Error(`workspace ${spec.id}: layout.view must be one of ${VIEWS.join(", ")}`);
  return { ...spec, layout: { ...(spec.layout || {}) } };
}

// {workspace: id | "*" | [ids], id, title, render?(el), badge?(ctx), onShow?(), order?}
export function panelSpec(spec) {
  need(spec, "panel", ["id", "title", "workspace"]);
  functions(spec, "panel", ["render", "badge", "onShow"]);
  return { ...spec };
}

// {id, title, run(ctx), contexts?: ["palette" | "feature" | "row" | "selection"], when?(ctx), group?, order?}
export function commandSpec(spec) {
  need(spec, "command", ["id", "title", "run"]);
  functions(spec, "command", ["run", "when"]);
  const contexts = spec.contexts || ["palette"];
  const unknown = contexts.filter(c => !CONTEXTS.includes(c));
  if (unknown.length) throw new Error(`command ${spec.id}: unknown context ${unknown.join(", ")} (use ${CONTEXTS.join(", ")})`);
  return { ...spec, contexts: [...contexts] };
}

// The dock's panels in workspace `id`: its own plus the shared ("*") ones, in order.
export const panelsFor = (panels, id) => panels.filter(p => [].concat(p.workspace).some(w => w === id || w === ALL_WORKSPACES));

// The three registries, with checked adders that return a function undoing the registration (true when it did:
// it is a no-op once the id was removed, or registered again by someone else).
export function createSlots() {
  const workspaces = createRegistry("workspace"), panels = createRegistry("panel"), commands = createRegistry("command");
  const adder = (registry, check) => (owner, spec) => {
    const item = registry.add(owner, check(spec));
    return () => registry.remove(item.id, item);
  };
  return {
    workspaces, panels, commands,
    addWorkspace: adder(workspaces, workspaceSpec),
    addPanel: adder(panels, panelSpec),
    addCommand: adder(commands, commandSpec),
    removeOwner(owner) { for (const r of [workspaces, panels, commands]) r.removeOwner(owner); },
  };
}

// The page's one set of slots.
export const slots = createSlots();
