// The Find dock panel (every workspace): the utterance box (Enter or Select resolves it; Ask, Ctrl+Enter or ⌘Enter
// sends it to the assistant) with the model picker (llm.js), then the selection: status, a stale-pin warning, anchors
// with their candidates (Pin / Unpin), diagnostics, export, and saved selections; the "Anchor candidates" map layer;
// and the selection commands. The highlights and the View controls share the pane (side.js, panels.js).
// Every server string is escaped. Pinning, saving, using and deleting are Actions, so each is recorded.
import { dispatch, getJSON } from "./api.js";
import { ask } from "./assistant.js";
import { barRoute } from "./assistmodel.js";
import { addCommandTo } from "./commands.js";
import { $, esc, toast } from "./dom.js";
import { addLayer, fitLinks } from "./map.js";
import { anchorRows, candidatePoints, exportChoices, selectionBadge, staleNotes, unpinAfterPin } from "./selectmodel.js";
import { ALL_WORKSPACES, CORE, slots } from "./slots.js";
import { activeSelection, store } from "./store.js";
import { addPanel, showPanel } from "./workspaces.js";

const FROM_COLOR = [53, 196, 106], TO_COLOR = [224, 82, 77];
const HINT = '<span class="empty">Type an utterance and press Select, or set highlighted links as the selection.</span>';
const run = action => dispatch(action).catch(e => toast(e.message));
let panel = null;
// The last export's note (schema verdict and warnings), kept for the selection it was made under so a state push
// redraws it rather than wiping it: {key, text, warned}.
let exportNote = null;
const selectionKey = s => {
  const sel = activeSelection(s);
  return sel ? JSON.stringify([s.server && s.server.active, sel.status, sel.link_ids, sel.intent || null, sel.stale_pins || []]) : null;
};

export function registerSelectionPanel() {
  panel = addPanel(CORE, { id: "find", title: "Find", workspace: ALL_WORKSPACES, order: 0,
    badge: ctx => selectionBadge(ctx.selection) });
  addLayer(CORE, "roadway", "candidates", c => {
    const pts = candidatePoints(c.selection);
    return pts.length ? new c.deck.ScatterplotLayer({ id: "candidates", data: pts, getPosition: p => [p.lon, p.lat],
      getRadius: 6, radiusUnits: "pixels", stroked: true, filled: false, lineWidthMinPixels: 2,
      getLineColor: p => (p.role === "from" ? FROM_COLOR : TO_COLOR), parameters: { depthTest: false } }) : null;
  }, { order: 210, title: "Anchor candidates" });
  const add = spec => addCommandTo(slots, store, CORE, { contexts: ["palette"], ...spec });
  add({ id: "save_selection", title: "Save selection…", contexts: ["palette", "selection"],
    when: c => c.selectionCount > 0, run: () => saveSelection() });
  add({ id: "export_selection", title: "Export selection…", when: c => exportChoices(c.selection).resolved,
    run: () => { showPanel("find"); $("sel-export-what").focus(); } });
  add({ id: "show_selection", title: "Show the Find panel", run: () => showPanel("find") });
}

function saveSelection() {
  const name = window.prompt("Save the selection as:");
  if (name && name.trim()) run({ type: "save_selection", name: name.trim() });
}

// Replace `el`'s markup, keeping keyboard focus on the button with the same data-key (state arrives after each
// Pin, Use or Delete, and the list is redrawn under the user's focus). When that button is gone, focus goes to
// the button `fallback(key)` names, else to `el` itself, never to <body>.
function redraw(el, html, fallback = () => null) {
  const key = el.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  el.innerHTML = html;
  if (!key) return;
  const find = k => [...el.querySelectorAll("[data-key]")].find(b => b.dataset.key === k);
  const again = find(key) || find(fallback(key));
  if (again) again.focus(); else el.focus();
}

const roleDot = role => `<span class="dot" style="background:${role === "from" ? "#35c46a" : "#e0524d"}"></span>`;

function anchorHTML(r) {
  const unpin = r.canUnpin
    ? ` <button class="mini ghost" data-unpin="${esc(r.role)}" data-key="unpin-${esc(r.role)}">Unpin</button>` : "";
  const keep = r.canPin
    ? ` <button class="mini ghost" data-pin="${esc(r.role)}" data-node="${esc(JSON.stringify(r.nodeId))}" ` +
      `data-key="pin-${esc(r.role)}-${esc(r.nodeId)}" title="Keep the resolver's choice">Pin node ${esc(r.nodeId)}</button>` : "";
  const cands = r.candidates.length ? `<div class="cands">Or: ${r.candidates.map(c =>
    `<button class="mini ghost" data-pin="${esc(r.role)}" data-node="${esc(JSON.stringify(c.nodeId))}" ` +
    `data-key="pin-${esc(r.role)}-${esc(c.nodeId)}" aria-label="${esc(`Pin the ${r.role} anchor to node ${c.nodeId}`)}">` +
    `node ${esc(c.nodeId)}${c.placed ? "" : " (not on the map)"}</button>`).join(" ")}</div>` : "";
  return `<div class="anc">${roleDot(r.role)}<b>${esc(r.role)}</b> · ${esc(r.anchor)} → node ${esc(r.nodeId)} · ` +
    `${esc(r.kind)} <span class="diag">(${esc(r.detail)})</span>` +
    `${r.unplaced ? ' <span class="diag">· not on the map</span>' : ""}${keep}${unpin}${cands}</div>`;
}

function savedHTML(x) {
  return `<div class="row saved"><span class="lbl">${esc(x.name)}</span>` +
    `<span class="diag">${esc(x.net_id)} · ${esc(x.links)} links · ${esc(x.by)}` +
    `${x.stale ? ' · <span class="stale" title="Saved after an edit took its pinned node off the facility">stale pin</span>' : ""}</span>` +
    `<button class="mini ghost" data-use="${esc(x.name)}" data-key="use-${esc(x.name)}">Use</button>` +
    `<button class="mini ghost" data-export="${esc(x.name)}" data-key="export-${esc(x.name)}">Export</button>` +
    `<button class="mini ghost" data-del="${esc(x.name)}" data-key="del-${esc(x.name)}">Delete</button></div>`;
}

export function renderSelectionPanel(s) {
  const sel = activeSelection(s);
  $("status-wrap").innerHTML = sel
    ? `<span class="status ${esc(sel.status)}">${esc(sel.status.replace("_", " "))}</span>` +
      (sel.utterance ? ` <span class="diag">${esc(sel.utterance)}</span>` : "")
    : HINT;
  const stale = staleNotes(sel);
  $("sel-stale").hidden = !stale.length;
  $("sel-stale").innerHTML = stale.map(t => `<p><b>Stale pin.</b> ${esc(t)}</p>`).join("");
  redraw($("anchors"), anchorRows(sel).map(anchorHTML).join("") || '<span class="empty">—</span>', unpinAfterPin);
  const diags = sel ? sel.diagnostics : [];
  $("diag").innerHTML = diags.length ? diags.map(d => `<div class="diag">• ${esc(d)}</div>`).join("") : '<span class="empty">—</span>';
  $("fragment").textContent = sel && sel.fragment ? JSON.stringify(sel.fragment, null, 2) : "—";
  const choice = exportChoices(sel);
  $("sel-export-go").disabled = !choice.resolved;
  $("sel-export-form").querySelector('[value="query"]').disabled = !choice.query;
  if (!choice.query && $("sel-export-form").value === "query") $("sel-export-form").value = "resolved";
  const note = exportNote && exportNote.key === selectionKey(s) ? exportNote : { text: choice.reason, warned: stale.length > 0 };
  $("sel-export-note").textContent = note.text;
  $("sel-export-note").classList.toggle("warn-note", note.warned);
  const saved = (s.server && s.server.saved_selections) || [];
  redraw($("sel-saved"), saved.length ? saved.map(savedHTML).join("") : '<span class="empty">None saved.</span>');
  $("sel-save").disabled = !(sel && sel.link_ids.length);
  if (panel) panel.refreshBadge();
}

function saveFile(filename, content) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], { type: "application/json" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Export the current selection (or the saved one called `name`) in the chosen form, and say what the server found.
async function download(name) {
  const params = new URLSearchParams({ what: $("sel-export-what").value, form: $("sel-export-form").value });
  if (name) params.set("name", name);
  const key = selectionKey(store.get());
  try {
    const j = await getJSON(`/api/selection/export?${params}`);
    saveFile(j.filename, j.content);
    const checked = j.valid === false ? `Schema problems: ${j.problems.join("; ")}.` :
      j.valid ? "Valid against the GMNS selection schema." : `Downloaded ${j.filename}.`;
    const warnings = (j.warnings || []).map(w => ` Warning: ${w}.`).join("");
    exportNote = { key, text: checked + warnings, warned: Boolean(warnings) || j.valid === false };
    toast(exportNote.text, exportNote.warned ? "error" : "info");
    renderSelectionPanel(store.get());
  } catch (e) { toast(e.message); }
}

// The utterance box: Enter selects, as it always has; Ask, Ctrl+Enter or ⌘Enter sends the text to the assistant.
function wireUtterance() {
  const select = async () => {
    const utterance = $("utterance").value.trim();
    if (!utterance) return;
    $("go").disabled = true;
    try {
      const sel = await dispatch({ type: "select", utterance });
      fitLinks(sel.link_ids);
    } catch (e) { toast(e.message); }
    $("go").disabled = false;
  };
  $("go").onclick = select;
  $("ask").onclick = () => ask($("utterance").value, { fromBar: true });
  $("utterance").onkeydown = e => {
    if (e.isComposing) return;
    const route = barRoute(e);
    if (route === "select") select();
    else if (route === "ask") { e.preventDefault(); ask($("utterance").value, { fromBar: true }); }
  };
}

export function wireSelectionPanel() {
  wireUtterance();
  $("anchors").onclick = e => {
    const pin = e.target.closest("[data-pin]"), unpin = e.target.closest("[data-unpin]");
    if (pin) run({ type: "pin_anchor", role: pin.dataset.pin, node_id: JSON.parse(pin.dataset.node) });
    else if (unpin) run({ type: "pin_anchor", role: unpin.dataset.unpin, node_id: null });
  };
  $("sel-export-go").onclick = () => download(null);
  $("sel-save").onclick = () => saveSelection();
  $("sel-saved").onclick = e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.use) run({ type: "use_selection", name: b.dataset.use });
    else if (b.dataset.export) download(b.dataset.export);
    else if (b.dataset.del && window.confirm(`Delete the saved selection "${b.dataset.del}"?`)) {
      run({ type: "delete_selection", name: b.dataset.del });
    }
  };
}
