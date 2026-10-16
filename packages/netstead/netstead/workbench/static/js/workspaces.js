// Workspace tabs (header) and the dock (the right drawer), rendered from the slots registry. Core's Inspect workspace
// and Details panel register here exactly as a plugin's would. Each strip shows only with two or more tabs, so a
// Workbench with no plugins looks as it always has. Both strips follow the WAI-ARIA tabs pattern (arrows, Home, End).
import { commandContext } from "./commands.js";
import { $, esc, toast } from "./dom.js";
import { ALL_WORKSPACES, CORE, panelsFor, slots } from "./slots.js";
import { store } from "./store.js";
import { currentViewMode, savedViews, setViewMode } from "./table.js";
import { nextIndex, normalizeBadge, stripVisibility, tabLabel, viewFor, workspaceBadge } from "./tabs.js";

const WS_KEY = "netstead.workspace";
const rendered = new Set(); // panels whose render(el) has run (once, the first time each shows)
const views = {};           // workspace id -> the view mode the user left it in (this page; savedViews() across reloads)
let lastShown = null;
let report = (owner, phase, error) => toast(`${owner}: ${(error && error.message) || error}`);

const paneFor = id => document.getElementById(`dock-${id}`);

export function registerCoreSlots() {
  slots.addWorkspace(CORE, { id: "inspect", title: "Inspect", order: 0 });
  slots.addPanel(CORE, { id: "details", title: "Details", workspace: ALL_WORKSPACES, order: 0 }); // adopts #dock-details
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
  const s = store.get();
  store.set({ dockPanel: { ...s.dockPanel, [s.workspace]: id } });
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

const badgeHTML = b => (b && b.text ? ` <span class="pcount">${esc(b.text)}</span>` : "") +
  (b && b.dirty ? ' <span class="dirty" aria-hidden="true">●</span>' : "");

// Every plugin-supplied string (id, title, badge) is escaped.
function tabHTML(prefix, item, selected, badge, controls) {
  return `<button role="tab" id="${prefix}-${esc(item.id)}" data-id="${esc(item.id)}" aria-selected="${selected}" ` +
    `aria-controls="${esc(controls)}" tabindex="${selected ? 0 : -1}" aria-label="${esc(tabLabel(String(item.title), badge))}"` +
    `${selected ? ' class="on"' : ""}>${esc(item.title)}${badgeHTML(badge)}</button>`;
}

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
  $("dock-tabs").hidden = !show.dock;
  redraw($("dock-tabs"), show.dock ? panels.map(p => tabHTML("dtab", p, p === current, badgeOf(p), `dock-${p.id}`)).join("") : "");
  for (const pane of $("side").querySelectorAll(".dock-pane")) {
    pane.hidden = !current || pane.dataset.panel !== current.id;
    setPanelRole(pane, show.dock ? `dtab-${pane.dataset.panel}` : null);
  }
  if (current) showPane(current);
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

// Arrow keys, Home and End move along a strip from the focused tab. The dock's tabs activate as focus moves (a
// panel is cheap to show); workspace tabs only move focus, and Enter or Space (the button's click) switches, because
// a switch can change the whole stage's layout.
function onStripKey(e, items, activate, prefix, { manual = false } = {}) {
  const from = e.target.closest('[role="tab"]');
  const n = nextIndex(items.findIndex(x => from && x.id === from.dataset.id), e.key, items.length);
  if (n === null) return;
  e.preventDefault();
  if (!manual) activate(items[n].id);
  const tab = document.getElementById(`${prefix}-${items[n].id}`);
  if (tab) tab.focus();
}

export function wireWorkspaces({ onError } = {}) {
  if (onError) report = onError;
  $("ws-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) showWorkspace(b.dataset.id); };
  $("dock-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) showPanel(b.dataset.id); };
  $("ws-tabs").onkeydown = e => onStripKey(e, slots.workspaces.list(), showWorkspace, "wtab", { manual: true });
  $("dock-tabs").onkeydown = e => onStripKey(e, currentPanels(), showPanel, "dtab");
  store.subscribe(["workspace", "dockPanel", "server"], () => renderStrips());
  renderStrips();
}
