// The Issues and Edits dock panels (registered like a plugin's, in every workspace), and the active network's pending
// edits and live edit warnings (GET /edits), kept in `store.edits`; the Edits panel: pending edits, Undo, and Save a
// copy. Notes and table names are data: every one is escaped.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { dirtyBadge, editsBadge, pendingLine, savedText, savePrompt, undoBlocked, validatedText } from "./editmodel.js";
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
    e.check_failed ? "The live check failed: run validation." :
      e.truncated ? "The live check was cut short: run validation." : ""] : [];
  $("ed-status").textContent = parts.filter(Boolean).join(" · ");
  $("ed-list").innerHTML = e && e.entries.length ? e.entries.map(x =>
    `<div class="chg${x.saved ? "" : " unsaved"}">${esc(pendingLine(x))}<div class="diag">` +
    `${esc(Object.entries(x.rows).map(([t, n]) => `${n} ${t}`).join(", "))} · v${esc(x.version)}${x.saved ? " · saved" : ""}</div></div>`).join("")
    : '<span class="empty">No edits yet. Click a row, then a cell, to edit it; or fix an issue.</span>';
}

// Jobs this tab started and reports on when they end (job id → action), and recently ended jobs, for a job that
// ends before its dispatch resolves.
const mine = new Map(), ended = new Map();

// Dispatch a job action (save_network, run_validation) and report its outcome here when it ends.
export async function dispatchJob(action) {
  const r = await dispatch(action);
  const id = r && r.job_id;
  if (!id) return r;
  mine.set(id, action);
  if (ended.has(id)) onEditJob(ended.get(id));
  return r;
}

// A job ended: a save's result (where, and what was or was not marked saved), a refused save's confirmation (save
// anyway, explicitly), and a validation that a newer one overtook.
export function onEditJob(job) {
  if (job.status === "running") return;
  ended.set(job.id, job);
  if (ended.size > 20) ended.delete(ended.keys().next().value);
  const action = mine.get(job.id);
  if (!action) return;
  mine.delete(job.id);
  if (action.type === "run_validation" && job.status === "done") {
    const text = validatedText(job.result);
    if (text) toast(text);
  } else if (action.type === "save_network" && job.status === "done") {
    toast(savedText(job.result));
  } else if (action.type === "save_network" && job.status === "failed" && job.payload && "check_failed" in job.payload) {
    const prompt = savePrompt(job.payload);
    if (prompt && window.confirm(prompt)) dispatchJob({ ...action, accept_warnings: true }).catch(fail);
  }
}

// Save a copy into `folder`. Open warnings, or a live check that failed or stopped early, are confirmed first, and
// saving anyway is an explicit, recorded choice. The server checks again, so a refusal is confirmed then too.
function save(folder) {
  const e = store.get().edits;
  const action = { type: "save_network", output_dir: folder, name: $("ed-save-name").value.trim(),
    output_format: $("ed-save-format").value };
  const prompt = savePrompt(e && e.net_id === activeId() ? e : null);
  if (prompt) {
    if (!window.confirm(prompt)) return;
    action.accept_warnings = true;
  }
  toast("Saving a copy… (see Jobs)");
  dispatchJob(action).catch(fail);
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
