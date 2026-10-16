// The command palette (Ctrl+K / ⌘K, or "Commands…"): every applicable command, searchable, keyboard first. The input
// is a combobox over a listbox (aria-activedescendant); Enter runs, Escape closes, focus returns to the opener.
import { $, esc } from "./dom.js";
import { commandContext, commandLabel, isMacPlatform, paletteGroups, paletteShortcutAllowed, runCommand } from "./commands.js";
import { closeMenu } from "./ctxmenu.js";
import { trapTab } from "./modal.js";
import { slots } from "./slots.js";
import { store } from "./store.js";
import { nextIndex } from "./tabs.js";

let opener = null, items = [], active = 0;
let report = () => {}, reportRun = () => {};

export const paletteOpen = () => !$("cmdk").hidden;

export function openPalette() {
  if (paletteOpen()) return;
  closeMenu(); // a context menu gives way (focus goes back to what opened it, which then becomes the opener)
  opener = document.activeElement;
  $("cmdk").hidden = false;
  $("cmdk-input").value = "";
  refresh();
  $("cmdk-input").focus();
}

export function closePalette() {
  if (!paletteOpen()) return;
  $("cmdk").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

// Group titles and command labels may come from plugins: both are escaped.
function refresh() {
  const s = store.get();
  const ctx = commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace });
  const groups = paletteGroups(slots.commands.list(), ctx, $("cmdk-input").value, report);
  items = groups.flatMap(g => g.items);
  active = 0;
  let i = 0;
  $("cmdk-list").innerHTML = groups.length
    ? groups.map(g => `<li role="group" aria-label="${esc(g.title)}"><div class="cmdk-head" aria-hidden="true">${esc(g.title)}</div>` +
      '<ul role="presentation">' + g.items.map(it =>
      `<li role="option" id="cmdk-opt-${i}" data-i="${i++}" aria-selected="false">${esc(commandLabel(it.command))}</li>`).join("") +
      "</ul></li>").join("")
    : '<li role="presentation" class="empty">No matching commands.</li>';
  mark();
}

function mark() {
  for (const li of $("cmdk-list").querySelectorAll('[role="option"]')) {
    const on = Number(li.dataset.i) === active;
    li.setAttribute("aria-selected", String(on));
    li.classList.toggle("on", on);
    if (on) li.scrollIntoView({ block: "nearest" });
  }
  if (items.length) $("cmdk-input").setAttribute("aria-activedescendant", `cmdk-opt-${active}`);
  else $("cmdk-input").removeAttribute("aria-activedescendant");
}

function choose(i) {
  const it = items[i];
  if (!it) return;
  closePalette();
  runCommand(it.command, it.ctx, reportRun);
}

// Another dialog (Settings, Open / Import, an Action's form) is open: the shortcut leaves it alone.
const otherDialogOpen = () => [...document.querySelectorAll(".modal")].some(m => m.id !== "cmdk" && !m.hidden);

// `onError` gets a `when` that throws; `onRunError` a command that fails when run.
export function wirePalette({ onError, onRunError = onError }) {
  report = onError;
  reportRun = onRunError;
  const mac = isMacPlatform(navigator);
  $("cmd-btn").onclick = () => openPalette();
  $("cmd-btn").title = `Command palette (${mac ? "⌘K" : "Ctrl+K"})`;
  $("cmd-btn").setAttribute("aria-keyshortcuts", mac ? "Meta+K" : "Control+K");
  $("cmdk-input").oninput = refresh;
  $("cmdk-input").onkeydown = e => {
    if (e.key === "Enter") { e.preventDefault(); choose(active); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return; // Home/End keep moving the caret
    const n = nextIndex(active, e.key, items.length, "vertical");
    if (n !== null) { e.preventDefault(); active = n; mark(); }
  };
  $("cmdk-list").onmousedown = e => e.preventDefault(); // a click keeps focus in the input
  $("cmdk-list").onclick = e => { const li = e.target.closest('[role="option"]'); if (li) choose(Number(li.dataset.i)); };
  $("cmdk").addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePalette(); return; }
    trapTab($("cmdk"), e);
  });
  $("cmdk").addEventListener("pointerdown", e => { if (e.target === $("cmdk")) closePalette(); }); // the backdrop
  document.addEventListener("keydown", e => {
    if (!paletteShortcutAllowed(e, { mac, otherDialog: otherDialogOpen() })) return; // one dialog at a time
    e.preventDefault();
    if (paletteOpen()) closePalette(); else openPalette();
  });
}
