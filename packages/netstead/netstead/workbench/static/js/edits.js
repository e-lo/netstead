// The Changes and Validate dock panels (registered like a plugin's, in every workspace), and the active network's
// pending edits and live edit warnings (GET /edits), kept in `store.edits`. The Changes pane (design 2c): Undo and
// Redo of the last change, the changes newest first, each staged (applied in this session, not saved) or saved, with
// a checkbox; with changes ticked, Undo selected (the newest run only), Save a copy… (the header's Save dialog,
// savedialog.js) and Make a card… (the card maker a plugin registers: wb.registerCardMaker, Host API 1.11). Clicking a
// change shows its record on the map and in Details. The edit warnings follow (issues.js renders them). Notes and table names are data: every one is escaped.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import {
  cardButton, changeDetail, changeTarget, changeTip, dirtyBadge, editsBadge, pendingLine, redoBlocked, undoBlocked, undoSelection,
  validatedText,
} from "./editmodel.js";
import { issuesBadge } from "./issuelist.js";
import { ALL_WORKSPACES, CORE } from "./slots.js";
import { store } from "./store.js";
import { addPanel } from "./workspaces.js";

let seq = 0, feedKey = null;
// The ticked changes' seqs (per tab; pruned to the listed changes on every render).
let picked = new Set();
// The plugin that makes cards from changes ({owner, fn}; the newest registration wins), or null.
let cardMaker = null;
const fail = e => toast(e.message);
const activeId = () => { const s = store.get().server; return s && s.active; };

// Badges read the server state (ctx.state), so the dock refreshes them on every state event.
export function registerEditPanels() {
  addPanel(CORE, { id: "edit", title: "Changes", icon: "history", workspace: ALL_WORKSPACES, order: 20, badge: c => editsBadge(c.state) });
  addPanel(CORE, { id: "validate", title: "Validate", icon: "shield-check", workspace: ALL_WORKSPACES, order: 30, badge: c => issuesBadge(c.state) });
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

const shownEdits = () => { const e = store.get().edits; return e && e.net_id === activeId() ? e : null; };

// The Changes pane: Undo / Redo of the last change and why Undo is unavailable ("The last change was made by …"),
// the staged count, the changes newest first with their checkboxes, and the selected changes' actions. The pane's
// rail badge comes from editsBadge (registered in registerEditPanels).
export function renderEdits(edits) {
  const e = edits && edits.net_id === activeId() ? edits : null;
  const why = undoBlocked(e);
  setBlocked("ed-undo", why, "Undo last change");
  setBlocked("ed-redo", redoBlocked(e), "Redo");
  $("ed-undo-why").textContent = e && e.pending ? why : "";
  const parts = e ? [dirtyBadge(e) || (e.pending ? "Nothing staged: every change is saved or in a card." : ""),
    e.warnings.length ? `${e.warnings.length} open warning(s): see Edit warnings below.` : "",
    e.check_failed ? "The live check failed: run validation." :
      e.truncated ? "The live check was cut short: run validation." : ""] : [];
  $("ed-status").textContent = parts.filter(Boolean).join(" · ");
  const entries = e ? e.entries : [];
  picked = new Set(entries.map(x => x.seq).filter(n => picked.has(n)));
  $("ed-list").innerHTML = entries.length ? entries.map(x => {
    const line = pendingLine(x), state = x.card ? "in-card" : x.saved ? "saved" : "staged";
    return `<div class="chg ${state}" data-seq="${esc(x.seq)}">` +
      `<input type="checkbox" class="chg-pick" aria-label="${esc(`Select ${line}`)}"${picked.has(x.seq) ? " checked" : ""}>` +
      `<button class="chg-go" data-tip="${esc(changeTip(x))}">` +
      `${esc(line)}<span class="diag">${esc(changeDetail(x))}</span></button></div>`;
  }).join("")
    : '<span class="empty">No changes yet. Double-click a field in Details (or click a cell of the focused row in the table) to edit it; or fix an issue.</span>';
  renderSelectionBar();
}

// The selected changes' actions: shown while any is ticked. Undo selected undoes only the newest run; otherwise it
// is disabled and says why.
function renderSelectionBar() {
  const e = shownEdits(), n = picked.size;
  $("ed-selbar").hidden = !n;
  if (!n) return;
  $("ed-sel-count").textContent = `${n} selected`;
  const plan = undoSelection(e ? e.entries : [], [...picked]);
  setBlocked("ed-undo-sel", plan.reason, plan.count === 1 ? "Undo this change" : `Undo these ${plan.count} changes, newest first`);
  const card = cardButton(Boolean(cardMaker), n);
  setBlocked("ed-card", card.reason, card.tip);
}

// The Changes pane's card maker (wb.registerCardMaker): `fn({net_id, seqs})` opens the plugin's card dialog with the
// ticked changes (oldest first). Returns the disposer.
export const cardMakers = {
  register(owner, fn) {
    const mine = { owner, fn };
    cardMaker = mine;
    if (document.getElementById("ed-selbar")) renderSelectionBar();
    return () => {
      if (cardMaker !== mine) return;
      cardMaker = null;
      if (document.getElementById("ed-selbar")) renderSelectionBar();
    };
  },
};

function makeCard() {
  const e = shownEdits();
  if (!cardMaker || !e || !picked.size) return;
  cardMaker.fn({ net_id: e.net_id, seqs: [...picked].sort((a, b) => a - b) });
}

// Undo the ticked changes: one undo_edit per change, newest first (each is a recorded action, as the Undo button's).
async function undoSelected() {
  const e = shownEdits(), plan = undoSelection(e ? e.entries : [], [...picked]);
  if (plan.reason) { toast(plan.reason); return; }
  try {
    for (let i = 0; i < plan.count; i++) await dispatch({ type: "undo_edit" });
    picked = new Set();
  } catch (err) { toast(err.message); }
}

// A change row clicked: its records are focused (Details shows the first), highlighted when they are links, and
// the map goes to them. A deletion's rows are gone: nothing to show.
function showChange(entry) {
  const t = changeTarget(entry);
  if (!t) { toast("This change removed its rows: there is nothing to show.", "info"); return; }
  const patch = { focus: { table: t.table, id: t.ids[0], from: "panel" } };
  if (t.table === "link") patch.highlights = new Set(t.ids);
  store.set(patch);
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
  $("ed-undo-sel").onclick = undoSelected;
  $("ed-save-sel").onclick = () => $("save-btn").click(); // the header's Save dialog: it writes every staged change
  $("ed-card").onclick = makeCard;
  $("ed-list").onchange = ev => {
    const row = ev.target.closest(".chg");
    if (!row || !ev.target.classList.contains("chg-pick")) return;
    const n = Number(row.dataset.seq);
    if (ev.target.checked) picked.add(n); else picked.delete(n);
    renderSelectionBar();
  };
  $("ed-list").onclick = ev => {
    const go = ev.target.closest(".chg-go"), e = shownEdits();
    if (!go || !e) return;
    const entry = e.entries.find(x => String(x.seq) === go.closest(".chg").dataset.seq);
    if (entry) showChange(entry);
  };
}
