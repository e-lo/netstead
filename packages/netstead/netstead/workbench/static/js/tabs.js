// Tab strips and menus: keyboard movement, badges, which view a workspace opens in, and the network switcher's
// mark. Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

const STEPS = { horizontal: { ArrowRight: 1, ArrowLeft: -1 }, vertical: { ArrowDown: 1, ArrowUp: -1 } };

// The index a key moves to in a strip or menu of `count` items (arrows wrap; Home and End jump), or null when the
// key doesn't move (the caller then leaves the event alone).
export function nextIndex(index, key, count, axis = "horizontal") {
  if (!count) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  const step = STEPS[axis][key];
  return step ? (index + step + count) % count : null;
}

const blank = v => v === null || v === undefined || v === false || v === "" || v === 0;

// A badge as registered (a count, a string, {text, dirty, title}, or nothing) -> {text, dirty, title} or null.
export function normalizeBadge(b) {
  if (blank(b)) return null;
  if (typeof b === "string" || typeof b === "number") return { text: String(b), dirty: false, title: "" };
  if (typeof b !== "object") return null;
  const text = blank(b.text) ? "" : String(b.text);
  if (!text && !b.dirty) return null;
  return { text, dirty: Boolean(b.dirty), title: b.title ? String(b.title) : "" };
}

// A workspace tab's badge: its own, else a dirty mark when any of its panels is dirty (UX principle 7).
export function workspaceBadge(own, panelBadges) {
  const b = normalizeBadge(own);
  if (b) return b;
  const dirty = panelBadges.map(normalizeBadge).filter(x => x && x.dirty);
  if (!dirty.length) return null;
  return { text: "", dirty: true, title: dirty.map(d => d.title).filter(Boolean).join("; ") || "Unsaved changes" };
}

// What a screen reader hears for a tab: "Edits, 2 unsaved", "Issues, 5", "Details".
export function tabLabel(title, badge) {
  const b = normalizeBadge(badge);
  if (!b) return title;
  const parts = [title];
  if (b.title) parts.push(b.title);
  else {
    if (b.text) parts.push(b.text);
    if (b.dirty) parts.push("unsaved changes");
  }
  return parts.join(", ");
}

// Which tab strips show: each only with two or more tabs, so a Workbench with no plugins has neither (nor their
// tabpanel roles).
export const stripVisibility = (workspaceCount, panelCount) => ({ workspaces: workspaceCount >= 2, dock: panelCount >= 2 });

// The view (map | split | table) a workspace opens in: where the user left it, else its layout's, else the current.
export const viewFor = (workspace, remembered, current) =>
  remembered[workspace.id] || (workspace.layout && workspace.layout.view) || current;

// The saved views (localStorage "netstead.views": workspace id -> view) after `workspace` was shown in `mode`. Each
// workspace has its own entry, so a plugin workspace's layout never becomes Inspect's view after a reload.
export const rememberView = (views, workspace, mode) => ({ ...views, [workspace]: mode });

// The view Inspect reopens in: its own saved one, else the single key used before views were per workspace.
export function restoredView(views, legacy) {
  const ok = v => ["map", "split", "table"].includes(v);
  return ok(views && views.inspect) ? views.inspect : ok(legacy) ? legacy : null;
}

// The network switcher's mark for a derived (non-base) network, such as a plugin's preview: "" for a base network.
export const networkBadge = summary => (summary && summary.derived_from ? "derived" : "");
// The mark's tooltip: the base network's label while it is open, else its id and "(closed)". Text, not markup.
export function derivedTitle(summary, networks) {
  if (!networkBadge(summary)) return "";
  const base = networks.find(n => n.id === summary.derived_from);
  return base ? `derived from ${base.label}` : `derived from ${summary.derived_from} (closed)`;
}
