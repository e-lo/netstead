// Which commands apply where (the palette, a map feature, a table row, the selection), and the palette's list.
// DOM-free (imports only store.js): unit-tested under node (tests/test_workbench_slots_js.py).
import { activeSelection } from "./store.js";

// Tables drawn on the map: a focused record from one of them is a "feature" as well as a "row".
const MAP_TABLES = new Set(["link", "node"]);

function deepFreeze(v) {
  if (v && typeof v === "object" && !Object.isFrozen(v)) {
    Object.freeze(v);
    for (const k of Object.keys(v)) deepFreeze(v[k]);
  }
  return v;
}

const copies = new WeakMap();
// A deep-frozen copy of a JSON-shaped value (the server state, focus, related records), made once per object: the
// page replaces these on every change rather than editing them, so a copy stays current until its source goes.
export function frozenCopy(v) {
  if (!v || typeof v !== "object") return v;
  if (!copies.has(v)) copies.set(v, deepFreeze(JSON.parse(JSON.stringify(v))));
  return copies.get(v);
}

// What plugin code sees (a command's `when(ctx)` and `run(ctx)`, a panel's or workspace's `badge(ctx)`, and the
// plugin part of a layer's ctx). `target` is the right-clicked map feature or grid row ({table, id}), or the focused
// record for the palette's "For …" group; null otherwise. Everything in it is a frozen copy, `state` (the server
// state) and `selection` deeply: plugin code can't change the page's own (changes go through Actions).
export function commandContext({ server, focus = null, highlights = new Set(), workspace = "inspect" }, target = null) {
  const state = frozenCopy(server);
  const net = state ? state.networks.find(n => n.id === state.active) : null;
  const selection = activeSelection({ server: state }) || null;
  return Object.freeze({
    network: net ? Object.freeze({ id: net.id, version: net.version, derived_from: net.derived_from ?? null }) : null,
    selection, selectionCount: selection ? selection.link_ids.length : 0,
    focus: frozenCopy(focus), highlights: Object.freeze([...highlights]), workspace, target: frozenCopy(target), state,
  });
}

export const commandLabel = c => (c.group ? `${c.group} ▸ ${c.title}` : c.title);

// Commands registered for `context` whose `when(ctx)` holds. A `when` that throws counts as false and is reported
// through `onError(command, error)`: one plugin's bug must not break a menu.
export function applicable(commands, context, ctx, onError = () => {}) {
  return commands.filter(c => c.contexts.includes(context) && holds(c, ctx, onError));
}

function holds(c, ctx, onError) {
  if (!c.when) return true;
  try { return Boolean(c.when(ctx)); } catch (e) { onError(c, e); return false; }
}

const selectionTitle = ctx => `Selection (${ctx.selectionCount} link${ctx.selectionCount === 1 ? "" : "s"})`;

// The context menu for a right-clicked record: its own commands, then the selection's (when it has links; an
// utterance that resolved to nothing leaves a selection with no links, so nothing to act on).
export function menuSections(commands, context, ctx, onError) {
  const own = applicable(commands, context, ctx, onError);
  const sections = own.length ? [{ title: `${ctx.target.table} ${ctx.target.id}`, items: own }] : [];
  if (ctx.selectionCount) {
    const shown = new Set(own.map(c => c.id));
    const sel = applicable(commands, "selection", ctx, onError).filter(c => !shown.has(c.id));
    if (sel.length) sections.push({ title: selectionTitle(ctx), items: sel });
  }
  return sections;
}

// The "Selection actions" menu in the Details panel.
export function selectionSections(commands, ctx, onError) {
  const items = ctx.selectionCount ? applicable(commands, "selection", ctx, onError) : [];
  return items.length ? [{ title: selectionTitle(ctx), items }] : [];
}

// How well `query` matches `text`: 3 prefix, 2 word start, 1 substring, 0.5 in order, -1 not at all (0: no query).
export function matchScore(text, query) {
  const t = String(text).toLowerCase(), q = String(query || "").trim().toLowerCase();
  if (!q) return 0;
  const at = t.indexOf(q);
  if (at === 0) return 3;
  if (at > 0) return /[\s:▸./-]/.test(t[at - 1]) ? 2 : 1;
  let i = 0;
  for (const ch of t) if (i < q.length && ch === q[i]) i++;
  return i === q.length ? 0.5 : -1;
}

function rank(list, query) {
  return list.map((c, i) => ({ c, i, s: matchScore(commandLabel(c), query) })).filter(x => x.s >= 0)
    .sort((a, b) => b.s - a.s || a.i - b.i).map(x => x.c);
}

// The palette: groups of {command, ctx}, best match first. "Commands" (palette context), then "For <table> <id>"
// (the focused record's feature and row commands: the keyboard way to reach a context menu), then the selection's.
// A command shows once, in the first group it fits.
export function paletteGroups(commands, ctx, query, onError) {
  const groups = [], seen = new Set();
  const add = (title, list, itemCtx) => {
    const fresh = list.filter(c => !seen.has(c.id));
    for (const c of fresh) seen.add(c.id);
    const items = rank(fresh, query).map(command => ({ command, ctx: itemCtx }));
    if (items.length) groups.push({ title, items });
  };
  add("Commands", applicable(commands, "palette", ctx, onError), ctx);
  if (ctx.focus) {
    const fctx = { ...ctx, target: { table: ctx.focus.table, id: ctx.focus.id } };
    const list = [];
    for (const k of MAP_TABLES.has(ctx.focus.table) ? ["feature", "row"] : ["row"]) {
      for (const c of applicable(commands, k, fctx, onError)) if (!list.includes(c)) list.push(c);
    }
    add(`For ${ctx.focus.table} ${ctx.focus.id}`, list, fctx);
  }
  if (ctx.selectionCount) add(selectionTitle(ctx), applicable(commands, "selection", ctx, onError), ctx);
  return groups;
}

// Register a command in `slots` (core's or a plugin's) and bump `store`'s commandSeq, on adding and on removing it,
// so menus and the "Selection actions" button re-check what applies. Returns the disposer (true when it removed).
export function addCommandTo(slots, store, owner, spec) {
  const off = slots.addCommand(owner, spec);
  const changed = () => store.set({ commandSeq: store.get().commandSeq + 1 });
  changed();
  return () => {
    const removed = off();
    if (removed) changed();
    return removed;
  };
}

// Run a command; a throw or a rejected promise goes to `onError(command, error)`.
export async function runCommand(command, ctx, onError) {
  try { await command.run(ctx); } catch (e) { onError(command, e); }
}

// Whether `nav` (the page's navigator) is a Mac's, where the palette's modifier is ⌘ rather than Ctrl.
export const isMacPlatform = nav =>
  /mac|iphone|ipad/i.test(String((nav && nav.userAgentData && nav.userAgentData.platform) || (nav && nav.platform) || ""));

// Ctrl+K (⌘K on a Mac) opens the palette, even from a text box. On a Mac, Ctrl+K stays the text box's own (delete to
// the end of the line), and elsewhere ⌘ isn't a modifier the page sees. `mac` left out accepts either.
export function isPaletteShortcut(e, mac) {
  const mod = mac === undefined ? e.ctrlKey || e.metaKey : mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  return Boolean(mod) && !e.altKey && !e.shiftKey && String(e.key).toLowerCase() === "k";
}

// Whether the shortcut toggles the palette now: never while another dialog (Settings, Open / Import, an Action's
// form) is open.
export const paletteShortcutAllowed = (e, { mac, otherDialog }) => isPaletteShortcut(e, mac) && !otherDialog;

// One palette command per plugin Action, opening its schema form ("Hello: Greet…"): a plugin with no front end
// still has a UI (plugins design, "declarative first"). An Action whose schema the server couldn't generate
// (`schema: null`) gets none: there is nothing to build its form from.
export function actionCommands(catalog, statuses) {
  const names = new Map(statuses.map(s => [s.id, s.name || s.id]));
  return catalog.filter(a => a.plugin && names.has(a.plugin) && a.schema).map(a => {
    const label = `${names.get(a.plugin)}: ${a.name}`;
    return { owner: a.plugin, id: `${a.type}:form`, title: `${label}…`, contexts: ["palette"],
      entry: { type: a.type, label, description: a.description, schema: a.schema } };
  });
}
