// The right-hand Details panel (#details-panel): it opens whenever a record is focused (a map click, a table row, a
// finding's zoom-to, a Find result) or a plugin shows a view in it (Host API 1.10: wb.showDetails, detailsview.js),
// one at a time, and closes with its × button or Escape, which clear the focus or dismiss the view. It is not a rail
// entry. The record's fields and actions are drawn into #details by side.js; a plugin's view into #details-plugin.
import { $ } from "./dom.js";
import { createDetailsViews } from "./detailsview.js";
import { store } from "./store.js";

const MODAL = '.modal:not([hidden]), dialog[open], #ctxmenu:not([hidden])';
const EDITABLE = "input, textarea, select, [contenteditable]";
const TITLE = "Details";

function sync() {
  const open = Boolean(store.get().focus || detailsViews.current());
  $("details-panel").hidden = !open;
  $("app").dataset.details = open ? "open" : "closed";
}

export const detailsViews = createDetailsViews({
  mount(title) {
    const el = $("details-plugin");
    el.replaceChildren();
    el.hidden = false;
    $("details").hidden = true;
    $("details-title").textContent = title;
    sync();
    return el;
  },
  unmount() {
    $("details-plugin").replaceChildren();
    $("details-plugin").hidden = true;
    $("details").hidden = false;
    $("details-title").textContent = TITLE;
    sync();
  },
  clearFocus: () => store.set({ focus: null }),
  onError: e => console.error(e),
});

// The × and Escape close whichever is showing: a plugin's view, or the focused record.
export function closeDetails() {
  if (!detailsViews.dismiss()) store.set({ focus: null });
}

const isOpen = () => Boolean(store.get().focus || detailsViews.current());

// Escape closes the panel unless something else owns the key: a dialog, a menu, or text being typed outside it.
function onKey(e) {
  if (e.key !== "Escape" || e.defaultPrevented || !isOpen()) return;
  if (document.querySelector(MODAL)) return;
  const t = e.target;
  if (t && t.closest && t.closest(EDITABLE) && !$("details-panel").contains(t)) return;
  e.preventDefault();
  closeDetails();
}

// A focused record replaces a plugin's view.
function onFocus(s) {
  if (s.focus) detailsViews.dismiss();
  sync();
}

export function wireDetailsPanel() {
  $("details-close").onclick = closeDetails;
  document.addEventListener("keydown", onKey);
  store.subscribe(["focus"], onFocus);
  sync();
}
