// Focus handling shared by the Workbench's dialogs (Settings, the command palette, an Action's form): Tab cycles
// inside the dialog. Each dialog also remembers what opened it and returns focus there on close.
export const focusables = root => [...root.querySelectorAll("button, input, select, textarea, a[href], [tabindex]")]
  .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);

export function trapTab(root, e) {
  if (e.key !== "Tab") return;
  const els = focusables(root);
  if (!els.length) return;
  const first = els[0], last = els[els.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}
