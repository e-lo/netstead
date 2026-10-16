// The Details dock panel: the focused record's details and the interactive highlights (the selection's status,
// anchors, export and saved selections are in the Selection panel: selection.js).
import { dispatch, getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { editSummary, undoBlocked } from "./editmodel.js";
import { deleteRecord, openFixEditor } from "./fixeditor.js";
import { store } from "./store.js";

const DETAILS_HINT = '<span class="empty">Click a link or node on the map, or a row in the table.</span>';

export function clearDetails() { $("details").innerHTML = DETAILS_HINT; }

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
