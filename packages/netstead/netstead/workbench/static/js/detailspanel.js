// The right-hand Details panel (#details-panel): it opens whenever a record is focused (a map click, a table row, a
// finding's zoom-to, a Find result) and closes with its × button or Escape, which clears the focus. It is not a rail
// entry. The record's fields and actions are drawn into #details by side.js.
import { $ } from "./dom.js";
import { store } from "./store.js";

const MODAL = '.modal:not([hidden]), dialog[open], #ctxmenu:not([hidden])';
const EDITABLE = "input, textarea, select, [contenteditable]";

export function closeDetails() { store.set({ focus: null }); }

function sync(s) {
  const open = Boolean(s.focus);
  $("details-panel").hidden = !open;
  $("app").dataset.details = open ? "open" : "closed";
}

// Escape closes the panel unless something else owns the key: a dialog, a menu, or text being typed outside it.
function onKey(e) {
  if (e.key !== "Escape" || e.defaultPrevented || !store.get().focus) return;
  if (document.querySelector(MODAL)) return;
  const t = e.target;
  if (t && t.closest && t.closest(EDITABLE) && !$("details-panel").contains(t)) return;
  e.preventDefault();
  closeDetails();
}

export function wireDetailsPanel() {
  $("details-close").onclick = closeDetails;
  document.addEventListener("keydown", onKey);
  store.subscribe(["focus"], sync);
  sync(store.get());
}
