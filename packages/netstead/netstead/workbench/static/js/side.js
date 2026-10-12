// Right-hand panel: selection result, link details, and interactive highlights.
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { editSummary, undoBlocked } from "./editmodel.js";
import { deleteRecord, openFixEditor } from "./fixeditor.js";
import { store } from "./store.js";

const HINT = '<span class="empty">Click a feature or row for details; type an utterance to select.</span>';
const DETAILS_HINT = '<span class="empty">Click a link or node on the map, or a row in the table.</span>';

export function clearDetails() { $("details").innerHTML = DETAILS_HINT; }

export function renderSelection(sel) {
  $("status-wrap").innerHTML = sel
    ? `<span class="status ${esc(sel.status)}">${esc(sel.status.replace("_", " "))}</span>` +
      (sel.utterance ? ` <span class="diag">${esc(sel.utterance)}</span>` : "")
    : HINT;
  // Anchors whose node has no coordinates (`anchors_unplaced`) are listed too, as not on the map.
  const anchors = sel ? [...sel.anchors, ...(sel.anchors_unplaced || []).map(a => ({ ...a, unplaced: true }))] : [];
  $("anchors").innerHTML = anchors.length
    ? anchors.map(a => `<div><span class="dot" style="background:${a.role === "from" ? "#35c46a" : "#e0524d"}"></span>` +
        `<b>${esc(a.role)}</b> · node ${esc(a.node_id)} · ${esc(a.kind)} <span class="diag">(${esc(a.detail)})</span>` +
        `${a.unplaced ? ' <span class="diag">· not on the map</span>' : ""}</div>`).join("")
    : '<span class="empty">—</span>';
  $("fragment").textContent = sel && sel.fragment ? JSON.stringify(sel.fragment, null, 2) : "—";
  const diags = sel ? sel.diagnostics : [];
  $("diag").innerHTML = diags.length ? diags.map(d => `<div class="diag">• ${esc(d)}</div>`).join("") : '<span class="empty">—</span>';
}

let detailsShown = Promise.resolve();
// The last showDetails render (a command that opens the editor in Details waits for it).
export const detailsSettled = () => detailsShown;

export function showDetails(table, id) {
  detailsShown = renderDetails(table, id);
  return detailsShown;
}

// Details' Undo button: shown only while core can undo the active network's last edit (so a network with no edits
// looks as it did before editing existed).
export function syncDetailsUndo(server) {
  const b = document.querySelector('#details [data-act="undo"]');
  if (b) b.hidden = Boolean(undoBlocked(editSummary(server)));
}

async function renderDetails(table, id) {
  const el = $("details"), head = `<div class="lid">${esc(table)} ${esc(id)}</div>`;
  el.innerHTML = `${head}<span class="empty">loading…</span>`;
  try {
    const j = await getJSON(netPath(store.get().server.active, `feature/${encodeURIComponent(table)}/${encodeURIComponent(id)}`));
    const rows = Object.entries(j.attributes).filter(([k, v]) => k !== j.pk && v !== null && v !== "")
      .map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join("");
    // Edit… and Delete… act on this (the focused) record; R8 keeps them out of the right-click menus.
    const actions = `<div class="row det-actions"><button class="mini ghost" data-act="edit">Edit…</button>` +
      `<button class="mini ghost" data-act="delete">Delete…</button>` +
      `<button class="mini ghost" data-act="undo" hidden>Undo last edit</button></div><div class="det-edit"></div>`;
    el.innerHTML = `${head}${actions}<table>${rows}</table>`;
    el.querySelector('[data-act="edit"]').onclick = () =>
      openFixEditor(el.querySelector(".det-edit"), { table, id }).catch(e => toast(e.message));
    el.querySelector('[data-act="delete"]').onclick = () => deleteRecord(table, id);
    el.querySelector('[data-act="undo"]').onclick = () => dispatch({ type: "undo_edit" }).catch(e => toast(e.message));
    syncDetailsUndo(store.get().server);
  } catch (e) {
    el.innerHTML = `${head}<span class="empty">error: ${esc(e.message)}</span>`;
  }
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
}
