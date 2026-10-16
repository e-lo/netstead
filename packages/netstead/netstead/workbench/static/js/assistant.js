// The Assistant dock panel (every workspace) and the command bar's Ask: one conversation, held by the server
// (GET /api/assistant and the `assistant` SSE event) for this session only. Read-only steps have already run when a
// turn arrives; the steps that change a network wait in one draft: Approve runs them all, Reject drops them, and each
// step's Edit… opens its Action form prefilled (running it there is an ordinary manual dispatch, which leaves the
// draft pending; approving afterwards is refused as stale if the network moved).
// Model text is set with textContent, never as markup; every other server string in markup goes through esc().
import { getJSON, postJSON } from "./api.js";
import { openActionForm } from "./actform.js";
import { addCommandTo } from "./commands.js";
import { $, esc, toast } from "./dom.js";
import { assistBadge, budgetText, pendingDraft, stepRows } from "./assistmodel.js";
import { ALL_WORKSPACES, CORE, slots } from "./slots.js";
import { store } from "./store.js";
import { addPanel, showPanel } from "./workspaces.js";

let panel = null, catalog = null;
let asking = null; // the question in flight (shown with "Thinking…" until the answer arrives), or null

export function registerAssistantPanel() {
  panel = addPanel(CORE, { id: "assistant", title: "Assistant", workspace: ALL_WORKSPACES, order: 30,
    badge: () => assistBadge(store.get().assistant) });
  addCommandTo(slots, store, CORE, { id: "ask_assistant", title: "Ask the assistant…", contexts: ["palette"],
    run: () => { showPanel("assistant"); $("as-input").focus(); } });
}

function setBusy(busy) {
  for (const id of ["as-send", "ask"]) {
    $(id).disabled = busy;
    $(id).textContent = busy ? "Asking…" : "Ask";
  }
}

// Ask `text` (from the panel's box, or the header's with `fromBar`). The answer arrives as the `assistant` event; the
// conversation is reloaded too, so a missed event never leaves the panel behind.
export async function ask(text, { fromBar = false } = {}) {
  const q = (text || "").trim();
  if (!q || asking) return;
  showPanel("assistant");
  asking = q;
  setBusy(true);
  renderAssistant(store.get().assistant);
  try {
    await postJSON("/api/assistant/ask", { text: q });
    if (!fromBar) $("as-input").value = "";
  } catch (e) {
    toast(e.message);
  } finally {
    asking = null;
    setBusy(false);
    await loadAssistant();
    renderAssistant(store.get().assistant);
  }
}

function stepElement(r, editIndex) {
  const row = document.createElement("div");
  row.className = `as-step ${r.status}`;
  row.innerHTML = `<span class="as-label">${esc(r.label)}</span> <span class="as-sum">${esc(r.summary)}</span>` +
    (r.result ? ` <span class="diag">${esc(r.result)}</span>` : "") +
    (editIndex != null ? ` <button class="mini ghost" data-edit="${esc(editIndex)}" ` +
      `aria-label="${esc("Edit step: " + r.summary)}">Edit…</button>` : "") +
    (r.python ? `<code>${esc(r.python)}</code>` : "");
  return row;
}

function turnElement(turn, draft) {
  const el = document.createElement("div");
  el.className = `as-turn ${turn.role === "user" ? "user" : "assistant"}`;
  if (turn.error || turn.text) {
    const text = document.createElement("div");
    text.className = "as-text";
    text.textContent = turn.error ? `⚠ ${turn.error}` : turn.text; // model text: never markup
    el.appendChild(text);
  }
  const editable = draft && draft.turn_id === turn.id && draft.status === "pending";
  let drafted = 0;
  for (const r of stepRows(turn)) el.appendChild(stepElement(r, editable && r.status === "drafted" ? drafted++ : null));
  if (turn.role !== "user" && !turn.error && !turn.text && !(turn.steps || []).length) {
    const none = document.createElement("div");
    none.className = "as-text diag";
    none.textContent = "(no answer)";
    el.appendChild(none);
  }
  return el;
}

function pendingElement(q) {
  const wrap = document.createDocumentFragment();
  const user = document.createElement("div");
  user.className = "as-turn user";
  const text = document.createElement("div");
  text.className = "as-text";
  text.textContent = q;
  user.appendChild(text);
  const wait = document.createElement("div");
  wait.className = "as-turn assistant";
  wait.innerHTML = '<div class="as-text diag">Thinking…</div>';
  wrap.append(user, wait);
  return wrap;
}

function noteText(st) {
  if (st.error) return st.error;
  if (st.provider === "stub") {
    return "Offline assistant (a few fixed commands). Choose a language model in the header for more.";
  }
  const sends = (st.sends || []).length ? st.sends.join("; ") : "nothing beyond your question";
  return `${st.provider}${st.model ? ` (${st.model})` : ""}${st.local ? ", on this machine," : ""} receives: ${sends}.`;
}

export function renderAssistant(payload) {
  const log = $("as-log");
  const turns = (payload && payload.turns) || [];
  const draft = payload ? payload.draft : null;
  if (!turns.length && !asking) {
    log.innerHTML = '<span class="empty">Ask for what you want done, e.g. “colour by lanes and zoom to the selection”.</span>';
  } else {
    log.replaceChildren(...turns.map(t => turnElement(t, draft)));
    if (asking) log.appendChild(pendingElement(asking));
  }
  log.scrollTop = log.scrollHeight;
  if (payload) {
    const st = payload.status;
    $("as-note").textContent = noteText(st);
    $("as-usage").textContent = st.provider === "stub" ? "" : budgetText(st);
    if (!asking) setBusy(Boolean(st.busy));
  }
  const d = pendingDraft(payload);
  $("as-draft").innerHTML = d ? `<div class="row wrap as-draftbar"><b>${esc(d.steps)} step${d.steps === 1 ? "" : "s"} ` +
    "waiting for approval</b>" +
    '<button class="mini" data-draft="approve">Approve</button>' +
    '<button class="mini ghost" data-draft="reject">Reject</button></div>' : "";
  if (panel) panel.refreshBadge();
}

async function editStep(i) {
  const draft = store.get().assistant && store.get().assistant.draft;
  const a = draft && draft.actions[i];
  if (!a) return;
  catalog = catalog || (await getJSON("/api/actions")).actions;
  const entry = catalog.find(c => c.type === a.type);
  if (!entry || !entry.schema) { toast(`no form for ${a.type}`); return; }
  const { type, ...prefill } = a;
  openActionForm({ type, label: entry.name, description: entry.description, schema: entry.schema }, prefill);
}

function decide(verdict) {
  const d = pendingDraft(store.get().assistant);
  if (!d) return;
  postJSON(`/api/assistant/drafts/${d.id}/${verdict}`, {})
    .catch(err => toast(err.message))
    .finally(() => loadAssistant());
}

export function wireAssistant() {
  $("as-send").onclick = () => ask($("as-input").value);
  $("as-input").onkeydown = e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask($("as-input").value); }
  };
  $("as-clear").onclick = () => postJSON("/api/assistant/clear", {}).catch(e => toast(e.message));
  $("as-draft").onclick = e => {
    const b = e.target.closest("button[data-draft]");
    if (b) decide(b.dataset.draft);
  };
  $("as-log").onclick = e => {
    const b = e.target.closest("button[data-edit]");
    if (b) editStep(Number(b.dataset.edit)).catch(err => toast(err.message));
  };
}

export async function loadAssistant() {
  try { store.set({ assistant: await getJSON("/api/assistant") }); } catch (e) { /* the panel keeps what it shows */ }
}
