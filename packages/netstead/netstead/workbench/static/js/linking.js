// Map <-> table linking rules. Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).
//
// Three sets drive the views: the recorded server `selection` (link ids, from NL or "Set as selection"),
// the per-tab `highlights` (link ids, clicked or box-selected in Highlight mode), and the per-tab `focus`
// (the one record last clicked on the map or in the table: {table, id, from}). Focus and highlights are
// view state and are never recorded; only an action changes the selection.

export const TABLE_SCOPES = ["all", "selection", "highlighted", "related"];

// The related-records sources: the highlights plus the focused record, as {table: [ids]}.
export function sourcesFor(focus, highlights) {
  const out = {};
  const add = (table, id) => { const ids = (out[table] ||= []); if (!ids.includes(id)) ids.push(id); };
  for (const id of highlights) add("link", id);
  if (focus) add(focus.table, focus.id);
  return out;
}

export const hasSources = sources => Object.values(sources).some(ids => ids.length > 0);

// The POST rows body fields for a table scope, plus the related tint when anything is focused or highlighted.
// `related` + `related_mode: "filter"` narrows the rows; `tint` (or `related_mode: "tint"`) only marks them.
// The Related scope filters by the highlights alone: the focus only tints, so clicking a row in that scope
// (which focuses it) never changes the rows listed.
export function rowsRequest({ scope, table, selection, highlights, focus, hops = 1 }) {
  const sources = sourcesFor(focus, highlights);
  const tinted = hasSources(sources);
  const tint = tinted ? { related: { sources, hops }, related_mode: "tint" } : {};
  const filterBy = (filterSources, filterHops) => ({ related: { sources: filterSources, hops: filterHops },
    related_mode: "filter", ...(tinted ? { tint: { sources, hops } } : {}) });
  if (scope === "selection" || scope === "highlighted") {
    const ids = scope === "selection" ? (selection ? [...selection.link_ids] : []) : [...highlights];
    if (table === "link") return { ids, ...tint };
    return filterBy({ link: ids }, 1);
  }
  if (scope === "related") {
    const highlighted = sourcesFor(null, highlights);
    return hasSources(highlighted) ? filterBy(highlighted, hops) : { ids: [] };
  }
  return tint;
}

// The grid's empty-state hint for a scope (blank when there are rows).
export function scopeHint(scope, total) {
  if (total) return "";
  if (scope === "selection") return "Nothing selected.";
  if (scope === "highlighted") return "Nothing highlighted: turn on Highlight links, then click or shift-drag.";
  if (scope === "related") return "Nothing related: turn on Highlight links and highlight some (a focused record only tints).";
  return "";
}

// The offset to show once a page reports `total` rows: past the end (the set shrank) goes to the last page.
export function clampOffset(offset, limit, total) {
  return total > 0 && offset >= total ? pageOffset(total - 1, limit) : offset;
}

// CSS classes for one grid row. `issues`: how many issues the last validation recorded against this record;
// `warned`: it has live edit warnings.
export function rowMarks({ table, id, selection, highlights, focus, via, issues = 0, warned = false }) {
  const marks = [];
  if (table === "link" && selection && selection.link_ids.includes(id)) marks.push("sel");
  if (table === "link" && highlights.has(id)) marks.push("hl");
  if (focus && focus.table === table && focus.id === id) marks.push("focus");
  if (via) marks.push("rel");
  if (issues) marks.push("iss");
  if (warned) marks.push("warn");
  return marks;
}

export const pageOffset = (index, limit) => Math.floor(index / limit) * limit;

// What a new decoded-network key ("<id>@<version>") means for the view: "switched" (another network, or none),
// "edited" (the same network at a new version: an edit or an undo), or "same".
export function netChange(prevKey, nextKey) {
  if (prevKey === nextKey) return "same";
  const id = k => (k ? k.slice(0, k.lastIndexOf("@")) : null);
  return prevKey && nextKey && id(prevKey) === id(nextKey) ? "edited" : "switched";
}

// A key as the grid shows it (data-* attributes are strings): back to a number when its column is numeric
// and it is a plain integer within the exact range of a JS number (as the server reads it: "007" is 7).
// A text key, "1e3", and anything past 2^53 stay text.
export function coerceId(raw, numeric = true) {
  if (!numeric || !/^-?\d+$/.test(raw)) return raw;
  const n = Number(raw);
  return Number.isSafeInteger(n) ? n : raw;
}
