// Context menus: commands for a right-clicked map feature or grid row (plus the selection's), and the "Selection
// actions" menu in the Details panel. A menu opens only when a command applies, so with none registered the map and
// the grid behave as before (MapLibre's right-drag; the browser's own menu on a row). WAI-ARIA menu keys.
import { $, esc } from "./dom.js";
import { applicable, commandContext, commandLabel, menuSections, runCommand, selectionSections } from "./commands.js";
import { slots } from "./slots.js";
import { store } from "./store.js";
import { nextIndex } from "./tabs.js";

let entries = [], menuCtx = null, opener = null;
let report = () => {}, reportRun = () => {};

const contextNow = target => {
  const s = store.get();
  return commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace }, target);
};
const items = () => [...$("ctxmenu").querySelectorAll('[role="menuitem"]')];

export const menuOpen = () => !$("ctxmenu").hidden;

// The menu for a right-clicked map feature ("feature") or grid row ("row") at viewport point `at`; false when no
// command applies (the caller then leaves the event alone).
export function openContextMenu(context, target, at) {
  const ctx = contextNow(target);
  return show(menuSections(slots.commands.list(), context, ctx, report), ctx, at);
}

function openSelectionMenu() {
  const ctx = contextNow(null), r = $("sel-cmds").getBoundingClientRect();
  if (show(selectionSections(slots.commands.list(), ctx, report), ctx, { x: r.left, y: r.bottom + 4 })) {
    $("sel-cmds").setAttribute("aria-expanded", "true");
  }
}

// Section titles and command labels may come from plugins: both are escaped.
function show(sections, ctx, at) {
  if (!sections.length) return false;
  opener = document.activeElement;
  entries = sections.flatMap(sec => sec.items);
  menuCtx = ctx;
  let i = 0;
  const menu = $("ctxmenu");
  menu.innerHTML = sections.map(sec => `<div role="group" aria-label="${esc(sec.title)}">` +
    `<div class="ctx-head" aria-hidden="true">${esc(sec.title)}</div>` +
    sec.items.map(c => `<button role="menuitem" tabindex="-1" data-i="${i++}">${esc(commandLabel(c))}</button>`).join("") +
    "</div>").join("");
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(at.x, innerWidth - box.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(at.y, innerHeight - box.height - 4))}px`;
  items()[0].focus();
  return true;
}

export function closeMenu({ restore = true } = {}) {
  if (!menuOpen()) return;
  $("ctxmenu").hidden = true;
  $("sel-cmds").setAttribute("aria-expanded", "false");
  if (restore && opener && opener.isConnected) opener.focus();
  opener = null;
}

function choose(i) {
  const command = entries[i], ctx = menuCtx;
  closeMenu();
  if (command) runCommand(command, ctx, reportRun);
}

// "Selection actions" shows only while a selection command applies (core registers none).
function syncSelectionButton() {
  const ctx = contextNow(null);
  $("sel-cmds-wrap").hidden = !(ctx.selection && applicable(slots.commands.list(), "selection", ctx, report).length);
}

// `onError` gets a `when` that throws; `onRunError` a command that fails when run.
export function wireContextMenus({ onError, onRunError = onError }) {
  report = onError;
  reportRun = onRunError;
  const menu = $("ctxmenu");
  menu.onclick = e => { const b = e.target.closest('[role="menuitem"]'); if (b) choose(Number(b.dataset.i)); };
  menu.onkeydown = e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); return; }
    if (e.key === "Tab") { e.preventDefault(); closeMenu(); return; }
    const list = items(), n = nextIndex(list.indexOf(document.activeElement), e.key, list.length, "vertical");
    if (n !== null) { e.preventDefault(); list[n].focus(); }
  };
  menu.oncontextmenu = e => e.preventDefault(); // a right-click inside the menu is not a new one
  document.addEventListener("pointerdown", e => { if (menuOpen() && !menu.contains(e.target)) closeMenu({ restore: false }); }, true);
  window.addEventListener("blur", () => closeMenu({ restore: false }));
  $("sel-cmds").onclick = () => openSelectionMenu();
  store.subscribe(["server", "commandSeq"], syncSelectionButton);
}
