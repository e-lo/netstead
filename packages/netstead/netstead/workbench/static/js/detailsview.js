// What the right-hand Details panel shows besides a focused record: one plugin view at a time (Host API 1.10,
// wb.showDetails). A view and a focused record never show together: showing a view clears the focus, and a focus
// (or the panel's × and Escape) dismisses the view. Import-free and DOM-free (unit-tested under node);
// detailspanel.js passes the real parts.
//
// parts: {mount(title) -> element (emptied, shown), unmount(), clearFocus(), onError(error)}
export function createDetailsViews({ mount, unmount, clearFocus, onError }) {
  let current = null; // {owner, id, title, cleanup, onClose}
  const run = fn => {
    if (typeof fn !== "function") return;
    try { fn(); } catch (e) { onError(e); }
  };
  // Takes the view out of the panel: its cleanup always runs, its onClose only when it was dismissed or replaced.
  const leave = notify => {
    const view = current;
    if (!view) return false;
    current = null;
    run(view.cleanup);
    if (notify) run(view.onClose);
    unmount();
    return true;
  };
  return {
    current: () => (current ? { owner: current.owner, id: current.id, title: current.title } : null),
    // Show `owner`'s view {id, title, render(el) -> cleanup?, onClose?}. Showing the same view again redraws it
    // (a new title, say) without calling its onClose; another view, or a focused record, is replaced.
    show(owner, { id, title, render, onClose } = {}) {
      if (typeof id !== "string" || !id) throw new Error(`${owner}: showDetails needs an id`);
      if (typeof render !== "function") throw new Error(`${owner}: showDetails needs render(el)`);
      const same = Boolean(current && current.owner === owner && current.id === id);
      leave(!same);
      current = { owner, id, title: String(title || "Details"), cleanup: null, onClose };
      clearFocus(); // after current is set, so the panel stays open
      const view = current;
      const out = render(mount(view.title));
      if (current === view && typeof out === "function") view.cleanup = out;
      else if (typeof out === "function") run(out); // render itself replaced the view
    },
    // `owner` closes its own view (`id`, or any of its views): its cleanup runs, not its onClose.
    close(owner, id = null) {
      if (!current || current.owner !== owner || (id !== null && current.id !== id)) return false;
      return leave(false);
    },
    // The user closed the panel (× or Escape), or a record was focused: the view's onClose and cleanup run.
    dismiss: () => leave(true),
  };
}
