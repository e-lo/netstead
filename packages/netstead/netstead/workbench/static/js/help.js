// Dismissible help text: a panel's explanatory paragraph hides behind its "×" and comes back from an info button,
// and the choice is remembered per browser (localStorage `wb.help.<tab>`). Import-free and DOM-free in its logic
// (elements and storage are passed in): unit-tested under node (tests/test_workbench_js.py).

export const helpKey = tab => `wb.help.${tab}`;

// Whether the user hid this help. Storage may be missing or throw (private windows, blocked site data): then shown.
export function helpHidden(storage, key) {
  try { return storage.getItem(key) === "hidden"; } catch (e) { return false; }
}

// Remember a choice; a failed write only costs the memory, not the toggle.
export function rememberHelp(storage, key, hidden) {
  try {
    if (hidden) storage.setItem(key, "hidden");
    else storage.removeItem(key);
  } catch (e) { /* storage unavailable */ }
}

// Wire a help paragraph `el` with its hide button and the info button that shows it again. The storage defaults to
// the browser's localStorage, looked up lazily because merely reading it can throw.
export function wireDismissibleHelp(el, key, { hideBtn, showBtn, storage } = {}) {
  const store = storage || (() => { try { return globalThis.localStorage; } catch (e) { return null; } })();
  const apply = hidden => { el.hidden = hidden; showBtn.hidden = !hidden; };
  hideBtn.onclick = () => { rememberHelp(store, key, true); apply(true); };
  showBtn.onclick = () => { rememberHelp(store, key, false); apply(false); };
  apply(helpHidden(store, key));
}
