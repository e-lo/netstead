// Layers → Filter links rules: the value a condition sends, and what the panel shows for the recorded filter.
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).

// The operators the panel's controls can send (index.html #flt-op), with how a read-only filter names them.
const OPS = { eq: "=", ne: "≠", lt: "<", lte: "≤", gt: ">", gte: "≥", contains: "contains", isnull: "is empty", notnull: "is not empty" };
export const DEFAULT_OP = "eq";
export const takesNoValue = op => op === "isnull" || op === "notnull";

// A filter value as typed: the trimmed text, unchanged (the server reads it as the column's type: "2" is 2 on a
// numeric column, "007" stays "007" on a text one); nothing for the empty / not-empty tests.
export function filterValue(op, raw) {
  return takesNoValue(op) ? null : String(raw).trim();
}

const valueText = v => (v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v));
const conditionText = c => [c.col, OPS[c.op] || c.op, takesNoValue(c.op) ? "" : valueText(c.val)].filter(Boolean).join(" ");

// What the panel shows for the active network's recorded filter (a filter set from Python or replayed reads the
// same as one applied here):
//   mode "none":     no filter; the controls go back to their defaults.
//   mode "controls": one condition the controls can send back; `controls` holds its column, operator and value.
//   mode "readonly": several conditions, or an operator the controls lack (`in`): `note` names it, the controls stay.
export function filterView(server) {
  const f = server && server.filters && server.filters[server.active];
  if (!f) return { mode: "none", count: "", canClear: false, note: "", controls: { op: DEFAULT_OP, val: "", valDisabled: false } };
  const count = f.count == null ? "filter no longer applies" : `${f.count.toLocaleString()} links match`;
  const [c] = f.where;
  if (f.where.length !== 1 || !(c.op in OPS)) {
    return { mode: "readonly", count, canClear: true, note: `Set from Python: ${f.where.map(conditionText).join(" and ")}.` };
  }
  return { mode: "controls", count, canClear: true, note: "",
    controls: { col: c.col, op: c.op, val: valueText(c.val), valDisabled: takesNoValue(c.op) } };
}

// A key that changes when the active network's recorded filter does (the panel reloads its controls only then, so
// a state push never overwrites what is being typed).
export function filterKey(server) {
  const f = server && server.filters && server.filters[server.active];
  return f ? `${server.active}|${JSON.stringify(f.where)}` : `${server ? server.active : ""}|`;
}
