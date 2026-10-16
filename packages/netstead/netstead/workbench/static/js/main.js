// Workbench boot: wire modules to the store and the server's SSE stream.
import { wireActionForm } from "./actform.js";
import { getBuffer, getJSON, netPath, subscribe } from "./api.js";
import { wirePalette } from "./cmdpalette.js";
import { registerCoreCommands } from "./corecmds.js";
import { openContextMenu, wireContextMenus } from "./ctxmenu.js";
import { $, toast } from "./dom.js";
import { registerEditCommands } from "./editcmds.js";
import { onEditJob, onEditsState, registerEditPanels, renderEdits, wireEdits } from "./edits.js";
import { rememberRecent, renderHeader, renderRecent, wireHeader } from "./header.js";
import { showEntry, wireHistory } from "./history.js";
import { hub } from "./hub.js";
import { markFocusedIssue, onIssuesState, registerIssueLayer, reloadIssues, renderEditWarnings, wireIssues } from "./issues.js";
import { loadJobs, onJob, wireJobs } from "./jobs.js";
import { netChange } from "./linking.js";
import { onHistoryEntry, onLLMEvent, onLLMJob, refreshLLM, renderLLMPanel, wireLLM } from "./llm.js";
import { layerRegistry } from "./layers.js";
import { fitBbox, fitLinks, fitNetwork, flyToNode, initMap, render, setBasemap } from "./map.js";
import { decodeNetwork } from "./netbuf.js";
import { populateColorby, renderLegend, renderPluginLayers, syncControls, wirePanels } from "./panels.js";
import { commandReporters } from "./pluginload.js";
import { loadPlugins, reportPluginError } from "./plugins.js";
import { renderPluginsPanel, wirePluginsPanel } from "./pluginspanel.js";
import { registerSelectionPanel, renderSelectionPanel, wireSelectionPanel } from "./selection.js";
import { clearDetails, renderHighlights, showDetails, syncDetailsUndo, wireSide } from "./side.js";
import { cancelRelated, renderRelatedBadges, scheduleRelated } from "./related.js";
import { onSettingsHistory, registerSection, wireSettings } from "./settings.js";
import { activeSelection, store } from "./store.js";
import { onFocusChanged, onNetworkChanged, onNetworkEdited, refreshRows, restoreViewMode, syncScopeControls, tableVisible, wireTable } from "./table.js";
import { onWizardJob, wireWizard } from "./wizard.js";
import { registerCoreSlots, wireWorkspaces } from "./workspaces.js";

const netKeyFor = server => {
  const h = server.networks.find(n => n.id === server.active);
  return h ? { h, key: `${h.id}@${h.version}` } : { h: null, key: null };
};
const inFlight = new Set();

async function loadActiveNetwork() {
  const { server, netKey } = store.get();
  const { h, key } = netKeyFor(server);
  if (key === netKey || inFlight.has(key)) return;
  if (!h) { store.set({ netKey: null, net: null, attrs: null, properties: [], prop: null, marker: null }); return; }
  inFlight.add(key);
  try {
    const [buf, attrs, props] = await Promise.all([
      getBuffer(netPath(h.id, "network.bin")), getJSON(netPath(h.id, "network.attrs.json")), getJSON(netPath(h.id, "properties")),
    ]);
    if (netKeyFor(store.get().server).key !== key) return; // superseded while fetching
    const switched = !netKey || !netKey.startsWith(`${h.id}@`);
    store.set({ netKey: key, net: decodeNetwork(buf), attrs, properties: props.properties, prop: null, marker: null });
    if (switched) fitNetwork({ animate: false });
  } finally {
    inFlight.delete(key);
  }
}

async function loadColorProperty() {
  const { server, net, netKey, prop } = store.get();
  const name = server.style.color_by;
  if (!net || name === "none" || (prop && prop.name === name && prop.netKey === netKey)) return;
  const p = await getJSON(netPath(server.active, `property/${encodeURIComponent(name)}`));
  const now = store.get();
  if (now.server.style.color_by === name && now.netKey === netKey) store.set({ prop: { ...p, netKey } });
}

async function onState(server) {
  store.set({ server });
  try { await loadActiveNetwork(); await loadColorProperty(); } catch (e) { toast(e.message); }
}

function onNavigate(ev) {
  if (ev.bbox) fitBbox(ev.bbox);
  else if (ev.to_network) fitNetwork();
  else if (ev.to_selection) { const sel = activeSelection(store.get()); if (sel) fitLinks(sel.link_ids); }
}

function onLinkClick(linkId) {
  const s = store.get();
  if (!s.highlightMode) { store.set({ focus: { table: "link", id: linkId, from: "map" } }); return; }
  const highlights = new Set(s.highlights);
  if (highlights.has(linkId)) highlights.delete(linkId); else highlights.add(linkId);
  store.set({ highlights });
}

function onNodeClick(nodeId) {
  if (!store.get().highlightMode) store.set({ focus: { table: "node", id: nodeId, from: "map" } });
}

// A box-select fills the highlights; with the table visible, it also becomes the table's filter.
function onBoxSelect() { if (tableVisible()) store.set({ tableScope: "highlighted" }); }

// The focused record: details, its map feature (fly only when the click came from the table), its row.
function onFocus(s) {
  const f = s.focus;
  store.set({ marker: null });
  if (f) {
    showDetails(f.table, f.id);
    if (f.table === "link" && f.from === "table") fitLinks([f.id]);
    if (f.table === "node") flyToNode(f.id, { fly: f.from === "table" });
  } else {
    clearDetails();
  }
  onFocusChanged();
}

// A newly decoded network. Another network resets the view. The same network at a new version (an edit, an undo)
// keeps focus, highlights, the table and its scope, and refreshes what depends on the data.
let shownKey = null;
function onNetKey(key) {
  const kind = netChange(shownKey, key);
  shownKey = key;
  if (kind === "same") return;
  if (kind === "edited") {
    onNetworkEdited();
    scheduleRelated();
    const s = store.get(), f = s.focus;
    if (f && focusGone(f, s.net)) store.set({ focus: null }); // the edit deleted it
    else if (f) showDetails(f.table, f.id);
    return;
  }
  cancelRelated(); onNetworkChanged();
  store.set({ highlights: new Set(), focus: null, related: null, fixTarget: null, issueFocus: null });
  clearDetails(); $("iss-fixer").innerHTML = "";
}

// Whether a focused link or node is missing from the decoded network (other tables are not decoded: their
// Details say so when the record has gone).
const focusGone = (f, net) => Boolean(net) && ((f.table === "link" && !net.id2idx.has(f.id)) ||
  (f.table === "node" && !net.nodeId2idx.has(f.id)));

// A viz.* setting (from the Settings dialog, Python, or the assistant) may change the basemap: swap it in place.
async function onSettingChanged(entry) {
  const a = entry.action;
  if (!entry.ok || a.type !== "set_setting" || !/^viz(\.|$)/.test(a.key)) return;
  const cfg = await getJSON("/api/config");
  if (JSON.stringify(cfg.style) === JSON.stringify(store.get().basemap)) return;
  store.set({ basemap: cfg.style });
  setBasemap(cfg.style);
}

function wireMapButtons() {
  $("btn-fitnet").onclick = () => fitNetwork();
  $("btn-fitsel").onclick = () => { const sel = activeSelection(store.get()); if (sel) fitLinks(sel.link_ids); };
  $("btn-highlight").onclick = () => store.set({ highlightMode: !store.get().highlightMode });
}

// Server state arrives for any change (style too); the grid reloads only when the selection did.
let lastSelKey = null;
function onSelectionMaybeChanged(sel) {
  const key = JSON.stringify(sel ? [sel.net_id, sel.link_ids] : null);
  if (key === lastSelKey) return;
  lastSelKey = key;
  refreshRows({ restart: true });
}

function wireStore() {
  store.subscribe(["server", "net", "prop", "highlights", "marker", "focus", "related", "hiddenLayers", "layerSeq"], () => render());
  store.subscribe(["layerSeq", "hiddenLayers"], s => renderPluginLayers(layerRegistry.toggleable(), s.hiddenLayers));
  store.subscribe(["server"], s => {
    renderHeader(s.server); syncControls(s.server.style); renderSelectionPanel(s);
    onSelectionMaybeChanged(activeSelection(s));
  });
  store.subscribe(["server", "prop"], s => renderLegend(s.server.style, s.prop));
  store.subscribe(["properties"], s => { populateColorby(s.properties); syncControls(s.server.style); });
  store.subscribe(["netKey"], s => onNetKey(s.netKey));
  store.subscribe(["focus"], s => onFocus(s));
  // New highlights change the rows of every scope but All (back to the first page); a focus only re-tints.
  store.subscribe(["highlights"], s => refreshRows({ restart: s.tableScope !== "all" }));
  store.subscribe(["focus"], () => refreshRows());
  store.subscribe(["tableScope", "relHops"], s => { syncScopeControls(s); refreshRows({ restart: true }); });
  store.subscribe(["highlights"], s => renderHighlights(s.highlights));
  store.subscribe(["focus", "highlights", "relHops"], () => scheduleRelated());
  store.subscribe(["related"], s => renderRelatedBadges(s.related));
  store.subscribe(["server"], s => onEditsState(s.server));
  store.subscribe(["server"], s => onIssuesState(s));
  store.subscribe(["issueFilter"], s => { $("iss-this").checked = s.issueFilter.thisRecord; reloadIssues({ restart: true }); });
  store.subscribe(["focus"], s => { if (s.issueFilter.thisRecord) reloadIssues({ restart: true }); });
  store.subscribe(["issueFocus"], () => markFocusedIssue());
  store.subscribe(["issues", "issueFocus"], () => render());
  store.subscribe(["issues"], () => refreshRows());
  store.subscribe(["edits"], s => renderEditWarnings(s.edits));
  store.subscribe(["edits"], () => refreshRows()); // the cell and row warning marks follow the live check
  store.subscribe(["edits"], s => renderEdits(s.edits));
  store.subscribe(["server"], s => syncDetailsUndo(s.server));
  store.subscribe(["highlightMode"], s => { $("btn-highlight").classList.toggle("on", s.highlightMode); $("map").classList.toggle("highlighting", s.highlightMode); });
}

async function boot() {
  // Every plugin-code failure (and core's own, which only toasts) goes to the Plugins section: a passive one (a badge,
  // a layer, a `when`) once per message, a command the user ran every time.
  const { whenError, runError } = commandReporters(reportPluginError);
  registerCoreSlots();
  registerSelectionPanel();
  registerEditPanels();
  wireWorkspaces({ onError: reportPluginError });
  wireContextMenus({ onError: whenError, onRunError: runError });
  registerCoreCommands();
  registerEditCommands();
  wirePalette({ onError: whenError, onRunError: runError });
  wireActionForm();
  wireStore(); wirePanels(); wireSide(); wireTable(); wireHeader(); wireHistory(); wireMapButtons(); wireJobs(); wireWizard(); wireLLM(); wireSettings(); wireIssues(); wireEdits(); wireSelectionPanel();
  registerIssueLayer(); // after wireStore: the layerSeq subscription that renders Overlays is in place
  registerSection("llm", "Language models", "llm-panel", () => renderLLMPanel());
  registerSection("plugins", "Plugins", "plugins-panel", () => renderPluginsPanel());
  wirePluginsPanel();
  renderRecent();
  const [cfg, server, history] = await Promise.all([getJSON("/api/config"), getJSON("/api/state"), getJSON("/api/history")]);
  store.set({ server, basemap: cfg.style });
  await loadJobs();
  restoreViewMode();
  loadPlugins().catch(e => toast(`Plugins: ${e.message}`)); // not awaited: a slow plugin never delays the map
  refreshLLM().catch(e => toast(e.message));
  if (history.entries.length) showEntry(history.entries[history.entries.length - 1]);
  // Subscribe now, not on map load, so job/history events are never missed. Until the map is ready a
  // `state` event is only stored (header, panels and table render from it; the map draws nothing, as no
  // network is decoded yet); onReady then loads the network for whatever state is latest.
  // `navigate` needs the map and is dropped until then.
  let mapReady = false;
  subscribe({
    state: e => { if (mapReady) onState(e.state); else store.set({ server: e.state }); },
    history: e => { showEntry(e.entry); rememberRecent(e.entry); onHistoryEntry(e.entry); onSettingsHistory(e.entry);
      onSettingChanged(e.entry).catch(err => toast(err.message)); hub.emit("core.history", e.entry);
    },
    navigate: e => { if (mapReady) onNavigate(e); },
    job: e => { onJob(e.job); onEditJob(e.job); onWizardJob(e.job); onLLMJob(e.job); hub.emit("core.job", e.job); },
    plugin: e => hub.emit(`${e.plugin}.${e.name}`, e.payload),
    llm: onLLMEvent,
  });
  initMap(cfg.style, {
    onLinkClick, onNodeClick, onBoxSelect,
    onLayerError: e => reportPluginError(e.owner, "layer", e.error),
    onContextMenu: (target, at) => openContextMenu("feature", target, at),
    onReady: () => { mapReady = true; return onState(store.get().server); },
  });
}

boot().catch(e => toast(e.message));
