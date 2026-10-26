// Edit rules: cell values, edit actions, the record form's schema, and how pending edits and warnings read. Ported
// in part from the offline report's "Fix locally" editor (map/templates/map_component.js: coerceLikely).
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).

import { countOf } from "./tabs.js";

const JSON_TYPE = { num: "number", bool: "boolean", str: "string" };

// The typed text as the value to send. Empty clears the cell (null). A number when the current value is a
// number; true/false for a yes/no value; otherwise the text as typed (a text column keeps "007"). The server
// coerces to the column's real type and refuses a value that does not fit.
export function parseCell(input, sample) {
  const text = input == null ? "" : String(input).trim();
  if (text === "") return null;
  if (typeof sample === "boolean") {
    if (/^(true|1|yes)$/i.test(text)) return true;
    if (/^(false|0|no)$/i.test(text)) return false;
    throw new Error(`Enter true or false (got "${text}").`);
  }
  if (typeof sample === "number") {
    const n = Number(text);
    if (Number.isFinite(n)) return n;
  }
  return text;
}

// Whether a grid cell of schema column `col` ({name, kind}) can be edited in place: never the key (a changed key
// would orphan every row that points at it; decision 2), and only the kinds the editor parses (not geometry).
export const cellEditable = (col, pk) => Boolean(col) && col.name !== pk && Boolean(JSON_TYPE[col.kind]);

// What the typed text is parsed against: the cell's value, or for an empty cell a value of its column's kind (so
// "yes" in an empty boolean cell is true and "maybe" is refused rather than sent as text).
export const cellSample = (current, kind) => (current != null ? current : kind === "num" ? 0 : kind === "bool" ? false : "");

const sortedStrings = values => [...new Set((values || []).map(String))].sort();

// The values a text cell's editor offers (its datalist): the spec's allowed values (`col.enum`) first, then the
// values the column already holds (`/distinct`), each sorted, without duplicates.
export function cellOptions(col, distinct) {
  const allowed = sortedStrings(col && col.enum);
  const seen = new Set(allowed);
  return [...allowed, ...sortedStrings(distinct).filter(v => !seen.has(v))];
}

// A comma-list cell's datalist as the text grows: each token completes the last segment, keeping what is typed
// before the last comma (so "auto, b" offers "auto, bike"); with no comma yet, the tokens themselves.
export function listOptions(value, tokens) {
  const text = value == null ? "" : String(value), at = text.lastIndexOf(",");
  const head = at < 0 ? "" : text.slice(0, at + 1) + (/^\s+/.test(text.slice(at + 1)) ? " " : "");
  return tokens.map(t => head + t);
}

// Whether leaving a cell editor (its input losing the focus) applies what was typed, as a spreadsheet does: only
// when the text no longer reads as the cell's value, so an untouched editor closes without sending anything.
export function shouldApplyOnLeave(typed, current) {
  const before = current == null ? "" : String(current);
  return (typed == null ? "" : String(typed)).trim() !== before.trim();
}

// The EditCells action: one column of one or more rows of one table.
export function cellAction({ table, ids, column, raw, current, note = null }) {
  const action = { type: "edit_cells", table, ids: [...ids], values: { [column]: parseCell(raw, current) } };
  if (note) action.note = note;
  return action;
}

// `version` is the delete plan's: the server refuses the deletion if the network changed since it was confirmed.
export function deleteAction(table, ids, { note = null, version = null } = {}) {
  const action = { type: "delete_rows", table, ids: [...ids] };
  if (note) action.note = note;
  if (version != null) action.expected_version = version;
  return action;
}


// The record editor's JSON Schema (for wb.schemaForm) from a table's /schema columns ({name, kind}): every column
// but the key and geometry, each nullable (empty clears the cell), or only `only` (a fix names one column).
export function recordSchema(columns, pk, only = null) {
  const properties = {};
  for (const c of columns) {
    if (c.name === pk || !JSON_TYPE[c.kind] || (only && !only.includes(c.name))) continue;
    properties[c.name] = { title: c.name, anyOf: [{ type: JSON_TYPE[c.kind] }, { type: "null" }] };
  }
  return { type: "object", properties };
}

// The values to send: each of `keys` whose form value differs from the record's. A field cleared in the form is
// missing from its value (schemaForm drops a blank optional field): that clears the cell (null).
export function changedValues(before, after, keys) {
  const out = {};
  for (const k of keys) {
    const a = after[k] ?? null;
    if (a !== (before[k] ?? null)) out[k] = a;
  }
  return out;
}

// The columns the cell editor offers for a record: everything but its key and its geometry.
export const editableColumns = (attributes, pk) => Object.keys(attributes).filter(k => k !== pk && k !== "geometry");

// One table's edit warnings, indexed for the grid: {"<key>": {"<column>": ["message", ...]}}.
export function cellWarnings(warnings, table) {
  const out = {};
  for (const w of warnings || []) {
    if (w.table !== table) continue;
    const row = (out[String(w.key)] ||= {});
    (row[w.column || ""] ||= []).push(w.message);
  }
  return out;
}

const rowCount = (n, table) => `${n} ${table} row${n === 1 ? "" : "s"}`;

// The confirmation for a deletion (from POST …/delete-plan), or the reason it is refused.
export function deletePlanText(plan) {
  if (plan.refused) return plan.refused;
  const also = Object.entries(plan.rows).filter(([t]) => t !== plan.table).map(([t, n]) => rowCount(n, t));
  return `Delete ${rowCount(plan.rows[plan.table] || 0, plan.table)}?` +
    (also.length ? ` This also deletes ${also.join(", ")}.` : "") + " Undo is in the Edits panel.";
}

// One pending edit in the Edits panel; another source's carries its name.
export const pendingLine = e => `#${e.seq} ${e.note}${e.source === "workbench" ? "" : ` [${e.source}]`}`;

// The active network's edit summary (session state `edits[active]`), or null.
export const editSummary = state => (state && state.edits && state.edits[state.active]) || null;

// The dirty text for an edit summary: "" when clean.
export function dirtyBadge(summary) {
  if (!summary || !summary.dirty) return "";
  return summary.unsaved ? `${summary.unsaved} unsaved` : "changed since saved";
}

// The Edits panel's badge (Part 2 `badge(ctx)`, from `ctx.state`): {text, dirty, title} while dirty, else null.
// The dock's workspace tab shows the dirty mark from it whenever another workspace is open.
export function editsBadge(state) {
  const s = editSummary(state);
  if (!s || !s.dirty) return null;
  return { text: s.unsaved ? String(s.unsaved) : "", dirty: true, title: s.unsaved ? countOf(s.unsaved, "unsaved edit") : dirtyBadge(s) };
}

// Why core's Undo is unavailable, or "" when it can undo.
export function undoBlocked(summary) {
  if (!summary || !summary.pending) return "Nothing to undo.";
  if (summary.last_source === "python") return "The last change was made from Python; undo it there.";
  if (summary.last_source !== "workbench") return `The last change was made by ${summary.last_source}; undo it there.`;
  return "";
}

// Why core's Redo is unavailable (no undone edit is waiting in the summary's `redo_count`), or "" when it can redo.
export const redoBlocked = summary => (summary && summary.redo_count ? "" : "Nothing to redo.");

// The confirmation before saving a copy, from the Edits data or a refused save's payload ({check_failed,
// truncated, warnings, count?}); "" when there is nothing to confirm. A failed or cut-short live check is said
// as such: its warning count may be 0 and proves nothing.
export function savePrompt(info) {
  if (!info) return "";
  const warnings = info.warnings || [];
  const n = info.count ?? warnings.length;
  const listed = warnings.slice(0, 10).map(w => `• ${w.message}`).join("\n");
  const more = n > Math.min(warnings.length, 10) ? `\n… and ${n - Math.min(warnings.length, 10)} more` : "";
  if (info.check_failed || info.truncated) {
    // A refused save says check_failed for both; truncated tells them apart.
    const why = info.truncated ? "stopped early (too many edited rows to check)" : "failed";
    const seen = n ? `\nIt found ${n} warning${n === 1 ? "" : "s"} before that:\n${listed}${more}` : "";
    return `The live edit check ${why}, so these edits may break the network unnoticed. Run validation to see ` +
      `every issue.${seen}\n\nSave the copy anyway?`;
  }
  if (!n) return "";
  return `${n} edit warning${n === 1 ? " is" : "s are"} still open:\n${listed}${more}\n\nSave the copy anyway?`;
}

// What a finished SaveNetwork job says. `notes` is plain text naming the edited tables.
export function savedText(result) {
  const n = result.edits_saved;
  const parts = [`Saved a copy to ${result.output} (${n} edit${n === 1 ? "" : "s"}).`];
  if (result.closed_while_saving) parts.push("The network was closed while saving, so no edits are marked saved.");
  else if (result.edited_while_saving) parts.push(`The network was edited while saving: the copy holds version ${result.version}; later edits are still unsaved.`);
  parts.push(...(result.notes || []));
  return parts.join(" ");
}

// What a finished RunValidation job adds: a run overtaken by a newer one did not change the stored issues.
export const validatedText = result =>
  result && result.superseded ? "A newer validation finished first; the Issues panel keeps its results." : "";
