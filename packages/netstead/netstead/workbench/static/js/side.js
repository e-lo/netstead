// The focused record in the right-hand Details panel (design 2b: Details is where you edit), and the interactive
// highlights (the selection's status, anchors, export and saved selections are in the Selection panel:
// selection.js). Each field is read-only until double-clicked, Enter is pressed on it, or its pencil is clicked;
// then Enter or Cmd/Ctrl+Enter applies it as one EditCells action and Escape cancels. A field holding a staged value
// (applied in this session, not saved) keeps a yellow outline until a saved copy holds it or it is undone. Column
// names and values are data: every one is escaped.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, mountIcons, toast } from "./dom.js";
import {
  STAGED_TIP, STATE_WORDS, cellAction, cellEditable, cellOptions, cellSample, editKey, editSummary, fieldControl,
  isStaged, redoBlocked, shouldApplyOnLeave, stagedCells, undoBlocked,
} from "./editmodel.js";
import { deleteRecord, openFixEditor } from "./fixeditor.js";
import { store } from "./store.js";

const DETAILS_HINT = '<span class="empty">Click a link or node on the map, or a row in the table.</span>';
const EDIT_TIP = "Double-click or press Enter to edit";

// The record Details shows: {net, table, id, attrs, pk, cols (name -> schema column)}; null when none is shown.
let shown = null;
// The field being edited: {field, busy}; null when none is. `refocusField`: the field to focus once Details redraws.
let editing = null, refocusField = null, renders = 0;

export function clearDetails() { renders++; shown = null; editing = null; $("details").innerHTML = DETAILS_HINT; }

let detailsShown = Promise.resolve();
// The last showDetails render (a command that opens the editor in Details waits for it).
export const detailsSettled = () => detailsShown;

export function showDetails(table, id) {
  detailsShown = renderDetails(table, id);
  return detailsShown;
}

// An icon button is enabled with `tip`, or disabled with the reason as its tooltip.
function setBlocked(b, reason, tip) {
  b.disabled = Boolean(reason);
  b.dataset.tip = reason || tip;
}

// Details' header Undo and Redo (design 2b): always there, disabled with the reason while core can't undo or redo
// the active network's last change.
export function syncDetailsUndo(server) {
  const summary = editSummary(server);
  setBlocked($("details-undo"), undoBlocked(summary), "Undo last change");
  setBlocked($("details-redo"), redoBlocked(summary), "Redo");
}

// Outline the shown record's fields that hold a staged value (from GET /edits, store.edits).
export function markStagedDetails() {
  if (!shown) return;
  const s = store.get();
  const staged = s.edits && s.edits.net_id === shown.net ? stagedCells(s.edits) : {};
  for (const td of $("details").querySelectorAll("td.v[data-field]")) {
    const on = isStaged(staged, shown.table, shown.id, td.dataset.field);
    td.classList.toggle("staged", on);
    const editable = td.hasAttribute("tabindex");
    if (on) td.dataset.tip = editable ? `${STAGED_TIP} ${EDIT_TIP}.` : STAGED_TIP;
    else if (editable) td.dataset.tip = EDIT_TIP;
    else delete td.dataset.tip;
  }
}

function fieldRow(field, value) {
  const col = shown.cols.get(field), editable = cellEditable(col, shown.pk);
  const text = value === null || value === "" ? '<span class="empty">·</span>' : esc(value);
  const attrs = editable
    ? ` tabindex="0" role="button" data-tip="${EDIT_TIP}" aria-label="${esc(`${field}: ${value ?? "empty"}. ${EDIT_TIP}`)}"`
    : "";
  const pencil = editable
    ? `<button class="iconbtn det-pencil" data-icon="pencil" data-tip="Edit ${esc(field)}" aria-label="Edit ${esc(field)}"></button>`
    : "";
  return `<tr><td class="k">${esc(field)}</td><td class="v" data-field="${esc(field)}"${attrs}>${text}</td>` +
    `<td class="e">${pencil}</td></tr>`;
}

async function renderDetails(table, id) {
  const el = $("details"), head = `<div class="lid">${esc(table)} ${esc(id)}</div>`;
  const net = store.get().server.active, mine = ++renders;
  editing = null;
  el.innerHTML = `${head}<span class="empty">loading…</span>`;
  try {
    const [j, schema] = await Promise.all([
      getJSON(netPath(net, `feature/${encodeURIComponent(table)}/${encodeURIComponent(id)}`)),
      getJSON(netPath(net, `table/${encodeURIComponent(table)}/schema`)).catch(() => ({ columns: [] })),
    ]);
    if (mine !== renders) return; // a newer record (or version) is being drawn
    shown = { net, table, id, attrs: j.attributes, pk: j.pk, cols: new Map(schema.columns.map(c => [c.name, c])) };
    const s = store.get();
    const staged = s.edits && s.edits.net_id === net ? stagedCells(s.edits) : {};
    // Empty fields are left out (a link has many), unless an edit just emptied them.
    const rows = Object.entries(j.attributes)
      .filter(([k, v]) => k !== j.pk && ((v !== null && v !== "") || isStaged(staged, table, id, k)))
      .map(([k, v]) => fieldRow(k, v)).join("");
    // Edit all fields… (the record form, empty fields included) and Delete… act on this (the focused) record; R8 keeps
    // them out of the right-click menus.
    const actions = `<div class="row det-actions">` +
      `<button class="iconbtn" data-act="edit" data-icon="pencil" data-tip="Edit all fields…" aria-label="Edit all fields"></button>` +
      `<button class="iconbtn" data-act="delete" data-icon="trash" data-tip="Delete…" aria-label="Delete this record"></button>` +
      `</div><div class="det-edit"></div>`;
    el.innerHTML = `${head}${actions}<table class="det-fields">${rows}</table>`;
    mountIcons(el);
    el.querySelector('[data-act="edit"]').onclick = () =>
      openFixEditor(el.querySelector(".det-edit"), { table, id }).catch(e => toast(e.message));
    el.querySelector('[data-act="delete"]').onclick = () => deleteRecord(table, id);
    for (const td of el.querySelectorAll("td.v[tabindex]")) {
      const begin = () => beginEdit(td);
      td.ondblclick = begin;
      td.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); begin(); } };
      td.parentNode.querySelector(".det-pencil").onclick = begin;
    }
    markStagedDetails();
    if (refocusField) { refocus(refocusField); refocusField = null; }
  } catch (e) {
    if (mine !== renders) return;
    shown = null;
    el.innerHTML = `${head}<span class="empty">error: ${esc(e.message)}</span>`;
  }
}

// The column's existing values for a text field's completion (/distinct), with the spec's allowed values first.
async function fillCompletion(list, col) {
  try {
    const j = await getJSON(netPath(shown.net, `table/${encodeURIComponent(shown.table)}/distinct?col=${encodeURIComponent(col.name)}`));
    list.innerHTML = cellOptions(col, j.values).map(v => `<option value="${esc(v)}">`).join("");
  } catch (e) { /* no completion: the field still takes typed text */ }
}

// Switch one field to editing: a select of its allowed values, or a text input (completing from the column's values).
// Enter or Cmd/Ctrl+Enter applies; Escape cancels; leaving the field applies changed text (as the table's cell editor
// and the card details do). A refusal keeps the field open with what was typed.
function beginEdit(td) {
  if (!shown || editing) return;
  const field = td.dataset.field, col = shown.cols.get(field), current = shown.attrs[field] ?? null;
  const { select, list } = fieldControl(col, current);
  const label = esc(`${field} of ${shown.table} ${shown.id}`), listId = `det-opts-${field}`, html = td.innerHTML;
  td.innerHTML = select
    ? `<select class="det-in" aria-label="${label}">${select.map(v => `<option value="${esc(v)}">${v === "" ? "(empty)" : esc(v)}</option>`).join("")}</select>`
    : `<input class="det-in" aria-label="${label}"${list ? ` list="${esc(listId)}"` : ""}>` +
      (list ? `<datalist id="${esc(listId)}"></datalist>` : "");
  td.insertAdjacentHTML("beforeend", `<span class="det-state">${STATE_WORDS.typing}: Enter applies, Esc cancels</span>`);
  const input = td.querySelector(".det-in"), { table, id } = shown;
  input.value = current == null ? "" : String(current);
  if (list) fillCompletion(td.querySelector("datalist"), col);
  const me = editing = { field, busy: false };
  // Back to the read-only value (its handlers are the cell's own, so they stay); Escape keeps the focus on it.
  const stop = ({ focus = false } = {}) => {
    if (editing !== me) return;
    editing = null;
    td.innerHTML = html;
    if (focus) td.focus();
  };
  const apply = async ({ focus = false } = {}) => {
    if (me.busy || editing !== me) return;
    if (!shouldApplyOnLeave(input.value, current)) { stop({ focus }); return; }
    let action;
    try { action = cellAction({ table, ids: [id], column: field, raw: input.value, current: cellSample(current, col.kind) }); }
    catch (err) { toast(err.message); input.focus(); return; }
    me.busy = true; input.disabled = true;
    try {
      await dispatch(action);
      editing = null; // the edit bumps the version: Details redraws with the new value, outlined as staged
      if (focus) refocusField = field;
    } catch (err) {
      me.busy = false; input.disabled = false;
      toast(err.message);
      input.focus();
    }
  };
  input.onkeydown = e => {
    const what = editKey(e);
    if (!what) return;
    e.preventDefault();
    e.stopPropagation(); // Escape cancels the edit, not the panel
    if (what === "cancel") stop({ focus: true }); else apply({ focus: true });
  };
  input.ondblclick = e => e.stopPropagation();
  input.onblur = () => setTimeout(() => { if (editing === me && !me.busy && document.activeElement !== input) apply(); }, 150);
  input.focus();
  if (typeof input.select === "function") input.select();
}

// After a field applied from the keyboard, the redrawn Details puts the focus back on it.
function refocus(field) {
  const td = [...$("details").querySelectorAll("td.v[data-field]")].find(x => x.dataset.field === field);
  if (td && td.hasAttribute("tabindex")) td.focus();
}

export function renderHighlights(highlights) {
  $("hl-count").textContent = highlights.size;
  $("hl-hint").style.display = highlights.size ? "none" : "";
  $("hl-actions").style.display = highlights.size ? "flex" : "none";
}

export function wireSide() {
  $("hl-set").onclick = async () => {
    const ids = [...store.get().highlights];
    if (!ids.length) return;
    try { await dispatch({ type: "select", link_ids: ids }); } catch (e) { toast(e.message); }
  };
  $("hl-clear").onclick = () => store.set({ highlights: new Set() });
  $("details-undo").onclick = () => dispatch({ type: "undo_edit" }).catch(e => toast(e.message));
  $("details-redo").onclick = () => dispatch({ type: "redo_edit" }).catch(e => toast(e.message));
}
