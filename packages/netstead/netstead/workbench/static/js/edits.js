// The Issues and Edits dock panels (registered like a plugin's, in every workspace), and the active network's pending
// edits and live edit warnings (GET /edits), kept in `store.edits`; the Edits panel: pending edits, Undo, and Save a
// copy. Notes and table names are data: every one is escaped.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { dirtyBadge, editsBadge, pendingLine, undoBlocked } from "./editmodel.js";
import { createFileBrowser } from "./filebrowser.js";
import { issuesBadge } from "./issuelist.js";
import { ALL_WORKSPACES, CORE } from "./slots.js";
import { store } from "./store.js";
import { addPanel } from "./workspaces.js";

let seq = 0, feedKey = null, saver = null;
const fail = e => toast(e.message);
const activeId = () => { const s = store.get().server; return s && s.active; };

// Badges read the server state (ctx.state), so the dock refreshes them on every state event.
export function registerEditPanels() {
  addPanel(CORE, { id: "issues", title: "Issues", workspace: ALL_WORKSPACES, order: 10, badge: c => issuesBadge(c.state) });
  addPanel(CORE, { id: "edits", title: "Edits", workspace: ALL_WORKSPACES, order: 20, badge: c => editsBadge(c.state) });
}

export async function loadEdits() {
  const id = activeId(), mine = ++seq;
  if (!id) { store.set({ edits: null }); return; }
  const j = await getJSON(netPath(id, "edits"));
  if (mine === seq && activeId() === id) store.set({ edits: j });
}

// Server state arrived: reload when the active network, its version, or its edit summary moved.
export function onEditsState(server) {
  const id = server.active, h = server.networks.find(n => n.id === id);
  const key = JSON.stringify([id, h && h.version, server.edits && server.edits[id]]);
  if (key === feedKey) return;
  feedKey = key;
  loadEdits().catch(e => toast(e.message));
}

// The Edits panel: pending edits (newest first), why Undo is unavailable, dirty state and the save controls. The
// panel's tab badge comes from editsBadge (registered in registerEditPanels).
export function renderEdits(edits) {
  const e = edits && edits.net_id === activeId() ? edits : null;
  const why = undoBlocked(e);
  $("ed-undo").disabled = Boolean(why);
  $("ed-undo-why").textContent = e && e.pending ? why : "";
  const parts = e ? [dirtyBadge(e) || (e.pending ? "Every edit is in a saved copy." : ""),
    e.warnings.length ? `${e.warnings.length} open warning(s): see Issues.` : "",
    e.truncated ? "The live check was cut short: run validation." : ""] : [];
  $("ed-status").textContent = parts.filter(Boolean).join(" · ");
  $("ed-list").innerHTML = e && e.entries.length ? e.entries.map(x =>
    `<div class="chg${x.saved ? "" : " unsaved"}">${esc(pendingLine(x))}<div class="diag">` +
    `${esc(Object.entries(x.rows).map(([t, n]) => `${n} ${t}`).join(", "))} · v${esc(x.version)}${x.saved ? " · saved" : ""}</div></div>`).join("")
    : '<span class="empty">No edits yet. Click a row, then a cell, to edit it; or fix an issue.</span>';
}

// The confirmation shown before saving while edit warnings are open ("" when there are none).
function warningsPrompt(warnings) {
  const n = warnings.length;
  if (!n) return "";
  const shown = warnings.slice(0, 10).map(w => `• ${w.message}`).join("\n");
  const more = n > 10 ? `\n… and ${n - 10} more` : "";
  return `${n} edit warning${n === 1 ? " is" : "s are"} still open:\n${shown}${more}\n\nSave the copy anyway?`;
}

// Save a copy into `folder`. Open warnings are listed first, and saving anyway is an explicit, recorded choice.
function save(folder) {
  const e = store.get().edits, warnings = e && e.net_id === activeId() ? e.warnings : [];
  const action = { type: "save_network", output_dir: folder, name: $("ed-save-name").value.trim(),
    output_format: $("ed-save-format").value };
  const prompt = warningsPrompt(warnings);
  if (prompt) {
    if (!window.confirm(prompt)) return;
    action.accept_warnings = true;
  }
  dispatch(action).then(() => toast("Saving a copy… (see Jobs)")).catch(fail);
}

export function wireEdits() {
  $("ed-undo").onclick = () => dispatch({ type: "undo_edit" }).catch(fail);
  $("ed-save").onclick = () => {
    if (!$("ed-save-name").value.trim()) { toast("Name the copy first."); $("ed-save-name").focus(); return; }
    const fb = $("ed-save-fb");
    fb.hidden = !fb.hidden;
    $("ed-save").setAttribute("aria-expanded", String(!fb.hidden));
    if (fb.hidden) return;
    saver ||= createFileBrowser(fb, { kinds: [], pickFolder: true, onPick: entry => {
      fb.hidden = true; $("ed-save").setAttribute("aria-expanded", "false"); save(entry.path);
    } });
    saver.show();
  };
}
