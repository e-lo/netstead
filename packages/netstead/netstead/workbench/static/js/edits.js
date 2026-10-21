// The Issues and Edits dock panels (registered like a plugin's, in every workspace), and the active network's pending
// edits and live edit warnings (GET /edits), kept in `store.edits`; the Edits panel: pending edits, Undo and Redo
// (Save a copy is the header's Save dialog, savedialog.js). Notes and table names are data: every one is escaped.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { dirtyBadge, editsBadge, pendingLine, redoBlocked, undoBlocked, validatedText } from "./editmodel.js";
import { issuesBadge } from "./issuelist.js";
import { ALL_WORKSPACES, CORE } from "./slots.js";
import { store } from "./store.js";
import { addPanel } from "./workspaces.js";

let seq = 0, feedKey = null;
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

// An icon button is enabled, or disabled with the reason as its tooltip.
function setBlocked(id, reason, tip) {
  const b = $(id);
  b.disabled = Boolean(reason);
  b.dataset.tip = reason || tip;
}

// The Edits panel: pending edits (newest first), why Undo is unavailable, dirty state and the save controls. The
// panel's tab badge comes from editsBadge (registered in registerEditPanels).
export function renderEdits(edits) {
  const e = edits && edits.net_id === activeId() ? edits : null;
  const why = undoBlocked(e);
  setBlocked("ed-undo", why, "Undo last edit");
  setBlocked("ed-redo", redoBlocked(e), "Redo");
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

// Jobs this tab started and reports on when they end (job id → {action, onEnd}), and recently ended jobs, for a
// job that ends before its dispatch resolves.
const mine = new Map(), ended = new Map();

// A validation ended: say what it found, or that a newer one overtook it.
function onValidationEnded(job) {
  if (job.status !== "done") return;
  const text = validatedText(job.result);
  if (text) toast(text, "info");
}

// Dispatch a job action (run_validation here; a save from the Save dialog) and call `onEnd(job, action)` when it
// ends.
export async function dispatchJob(action, onEnd = onValidationEnded) {
  const r = await dispatch(action);
  const id = r && r.job_id;
  if (!id) return r;
  mine.set(id, { action, onEnd });
  if (ended.has(id)) onEditJob(ended.get(id));
  return r;
}

// A job ended: hand it to whoever dispatched it from this tab.
export function onEditJob(job) {
  if (job.status === "running") return;
  ended.set(job.id, job);
  if (ended.size > 20) ended.delete(ended.keys().next().value);
  const tracked = mine.get(job.id);
  if (!tracked) return;
  mine.delete(job.id);
  tracked.onEnd(job, tracked.action);
}

export function wireEdits() {
  $("ed-undo").onclick = () => dispatch({ type: "undo_edit" }).catch(fail);
  $("ed-redo").onclick = () => dispatch({ type: "redo_edit" }).catch(fail);
}
