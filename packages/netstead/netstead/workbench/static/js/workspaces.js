// Workspace tabs (header) and the dock (the left rail and its drawer), rendered from the slots registry. Core's Inspect
// workspace registers here exactly as a plugin's would (the Details panel is not a dock panel: detailspanel.js; the dock's core panels register from their own
// modules: Find in selection.js, Changes and Validate in edits.js, Assistant in assistant.js). The workspace strip shows only
// with two or more tabs. The rail is a vertical icon strip: clicking an entry opens its pane in the drawer, clicking the
// open entry closes the drawer. Both follow the WAI-ARIA tabs pattern (arrows, Home, End).
import { commandContext } from "./commands.js";
import { $, esc, toast } from "./dom.js";
import { hub } from "./hub.js";
import { CORE, RESERVED_IDS, panelsFor, slots } from "./slots.js";
import { store } from "./store.js";
import { currentViewMode, savedViews, setViewMode } from "./table.js";
import { ICONS, icon } from "./icons.js";
import { nextIndex, normalizeBadge, openRail, railGroups, stripVisibility, tabLabel, toggleRail, viewFor, workspaceBadge } from "./tabs.js";

const WS_KEY = "netstead.workspace";
const rendered = new Set(); // panels whose render(el) has run (once, the first time each shows)
const views = {};           // workspace id -> the view mode the user left it in (this page; savedViews() across reloads)
let lastShown = null;
let report = (owner, phase, error) => toast(`${owner}: ${(error && error.message) || error}`);

const paneFor = id => document.getElementById(`dock-${id}`);

export function registerCoreSlots() {
  slots.addWorkspace(CORE, { id: "inspect", title: "Inspect", order: 0 });
}

export function addWorkspace(owner, spec) {
  const dispose = slots.addWorkspace(owner, spec);
  renderStrips();
  return () => {
    if (!dispose()) return; // already gone, or the id belongs to a later registration now
    if (store.get().workspace === spec.id) showWorkspace("inspect"); else renderStrips();
  };
}

// A panel's pane is created now (hidden) unless the markup already has one (core's Details); render(el) runs the
// first time it shows.
export function addPanel(owner, spec) {
  const dispose = slots.addPanel(owner, spec);
  if (!paneFor(spec.id)) {
    const pane = document.createElement("section");
    pane.id = `dock-${spec.id}`;
    pane.className = "dock-pane";
    pane.hidden = true;
    pane.dataset.panel = spec.id;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", `dtab-${spec.id}`);
    $("side").appendChild(pane);
  }
  renderStrips();
  return {
    refreshBadge: () => renderStrips(),
    dispose() {
      if (!dispose()) return; // already gone, or the id belongs to a later registration now
      const pane = paneFor(spec.id);
      if (pane) pane.remove();
      rendered.delete(spec.id);
      if (lastShown === spec.id) lastShown = null;
      renderStrips();
    },
  };
}

export function showWorkspace(id) {
  const s = store.get();
  const target = slots.workspaces.get(id) ? id : "inspect";
  if (target === s.workspace) { renderStrips(); return; }
  views[s.workspace] = currentViewMode();
  const mode = viewFor(slots.workspaces.get(target), { ...savedViews(), ...views }, currentViewMode());
  store.set({ workspace: target });
  if (mode !== currentViewMode()) setViewMode(mode);
  try { localStorage.setItem(WS_KEY, target); } catch (e) { /* storage unavailable: the workspace isn't remembered */ }
}

// Show panel `id`, switching to a workspace that has it when the current one doesn't.
export function showPanel(id) {
  const p = slots.panels.get(id);
  if (!p) return;
  if (!panelsFor([p], store.get().workspace).length) showWorkspace([].concat(p.workspace)[0]);
  const s = store.get(), next = openRail(id);
  store.set({ dockPanel: { ...s.dockPanel, [s.workspace]: next.current }, drawerOpen: next.open });
}

// A click on a rail entry: open its pane, or close the drawer when it is the open one.
function toggleDock(id) {
  const s = store.get(), next = toggleRail({ open: s.drawerOpen, current: (selectedPanel(currentPanels()) || {}).id }, id);
  store.set({ dockPanel: { ...s.dockPanel, [s.workspace]: next.current }, drawerOpen: next.open });
}

// After plugins load: reopen the workspace this browser last used, if it still exists.
export function restoreWorkspace() {
  let id = null;
  try { id = localStorage.getItem(WS_KEY); } catch (e) { /* storage unavailable */ }
  if (id && slots.workspaces.get(id)) showWorkspace(id);
}

const currentPanels = () => panelsFor(slots.panels.list(), store.get().workspace);
const selectedPanel = panels => panels.find(p => p.id === store.get().dockPanel[store.get().workspace]) || panels[0] || null;

// A plugin's badge(ctx), contained: one that throws shows no badge and is reported.
function badgeOf(item) {
  if (!item.badge) return null;
  const s = store.get();
  const ctx = commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace });
  try { return normalizeBadge(item.badge(ctx)); } catch (e) { report(item.owner, "badge", e); return null; }
}

// One pill per badge: its count, orange ("warn") when something is unsaved; a dirty badge with no count is a dot.
const badgeTone = b => (b.tone === "error" ? " error" : b.dirty || b.tone === "warn" ? " warn" : "");
const badgeHTML = b => (b && !b.quiet ? ` <span class="pcount${badgeTone(b)}" aria-hidden="true">${esc(b.text)}</span>` : "");

// Every plugin-supplied string (id, title, badge) is escaped. A rail entry (prefix "dtab") is an icon with its title as
// the tooltip (the title's first letter when the panel has no known icon); `selected` marks the open pane, `tabbable`
// the one entry the Tab key reaches (the others take arrows).
function tabHTML(prefix, item, selected, badge, controls, tabbable = selected) {
  const rail = prefix === "dtab";
  const iconic = rail && typeof item.icon === "string" && Object.hasOwn(ICONS, item.icon);
  const label = tabLabel(String(item.title), badge);
  const glyph = iconic ? icon(item.icon, { size: 20 }) : `<span class="railtxt" aria-hidden="true">${esc(String(item.title).slice(0, 1).toUpperCase())}</span>`;
  const body = rail ? `${glyph}${badgeHTML(badge)}` : `${esc(item.title)}${badgeHTML(badge)}`;
  const classes = [selected ? "on" : "", rail ? "icontab" : ""].join(" ").trim();
  return `<button role="tab" id="${prefix}-${esc(item.id)}" data-id="${esc(item.id)}" aria-selected="${selected}" ` +
    `aria-controls="${esc(controls)}" tabindex="${tabbable ? 0 : -1}" aria-label="${esc(label)}"` +
    `${rail ? ` data-tip="${esc(label)}" data-tip-side="right"` : ""}${classes ? ` class="${classes}"` : ""}>` +
    `${body}</button>`;
}

const isPluginPanel = p => p.owner !== CORE && RESERVED_IDS[p.id] !== p.owner;

// Redraw a strip without losing keyboard focus from the tab that had it (state arrives while a user tabs around).
function redraw(el, html) {
  const focused = el.contains(document.activeElement) ? document.activeElement.id : null;
  el.innerHTML = html;
  if (focused && document.getElementById(focused)) document.getElementById(focused).focus();
}

// A tab's panel carries role="tabpanel" only while its strip shows, so with no plugins the page's roles are as before.
function setPanelRole(el, tabId) {
  if (tabId) { el.setAttribute("role", "tabpanel"); el.setAttribute("aria-labelledby", tabId); }
  else { el.removeAttribute("role"); el.removeAttribute("aria-labelledby"); }
}

export function renderStrips() {
  const s = store.get(), workspaces = slots.workspaces.list();
  const panels = currentPanels(), current = selectedPanel(panels);
  const show = stripVisibility(workspaces.length, panels.length);
  $("ws-tabs").hidden = !show.workspaces;
  redraw($("ws-tabs"), show.workspaces ? workspaces.map(w => tabHTML("wtab", w, w.id === s.workspace,
    workspaceBadge(badgeOf(w), panelsFor(slots.panels.list(), w.id).map(badgeOf)), "stage")).join("") : "");
  setPanelRole($("stage"), show.workspaces ? `wtab-${s.workspace}` : null);
  const open = s.drawerOpen && Boolean(current);
  $("app").dataset.drawer = open ? "open" : "closed";
  redraw($("dock-tabs"), railGroups(panels, isPluginPanel).map(p => p.divider ? '<div class="rail-sep" aria-hidden="true"></div>'
    : tabHTML("dtab", p, open && p === current, badgeOf(p), `dock-${p.id}`, p === current)).join(""));
  for (const pane of $("side").querySelectorAll(".dock-pane")) {
    pane.hidden = !open || pane.dataset.panel !== current.id;
    setPanelRole(pane, `dtab-${pane.dataset.panel}`);
  }
  if (open) showPane(current);
  hub.emit("core.panels", slots.panels.list().map(p => p.id)); // core controls that need a plugin's panel listen
}

function showPane(p) {
  const pane = paneFor(p.id);
  if (!pane) return;
  if (!rendered.has(p.id)) {
    rendered.add(p.id);
    if (p.render) {
      try {
        const out = p.render(pane);
        if (out && typeof out.catch === "function") out.catch(e => failed(p, pane, e));
      } catch (e) { failed(p, pane, e); }
    }
  }
  if (lastShown !== p.id && p.onShow) { try { p.onShow(); } catch (e) { report(p.owner, "panel", e); } }
  lastShown = p.id;
}

function failed(p, pane, e) {
  report(p.owner, "panel", e);
  pane.innerHTML = `<p class="empty">This panel failed to load: ${esc((e && e.message) || e)}</p>`;
}

// Arrow keys, Home and End move along a strip from the focused tab. Both strips only move focus: Enter or Space (the
// button's click) switches a workspace or toggles a rail entry, because either changes the page's layout.
function onStripKey(e, items, activate, prefix, { manual = false, axis = "horizontal" } = {}) {
  const from = e.target.closest('[role="tab"]');
  const n = nextIndex(items.findIndex(x => from && x.id === from.dataset.id), e.key, items.length, axis);
  if (n === null) return;
  e.preventDefault();
  if (!manual) activate(items[n].id);
  const tab = document.getElementById(`${prefix}-${items[n].id}`);
  if (tab) tab.focus();
}

export function wireWorkspaces({ onError } = {}) {
  if (onError) report = onError;
  $("ws-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) showWorkspace(b.dataset.id); };
  $("dock-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) toggleDock(b.dataset.id); };
  $("ws-tabs").onkeydown = e => onStripKey(e, slots.workspaces.list(), showWorkspace, "wtab", { manual: true });
  $("dock-tabs").onkeydown = e => onStripKey(e, currentPanels(), toggleDock, "dtab", { manual: true, axis: "vertical" });
  store.subscribe(["workspace", "dockPanel", "drawerOpen", "server"], () => renderStrips());
  renderStrips();
}
