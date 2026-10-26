// The data table's per-column filters: one typed spec per column, the `[{col, op, val}]` conditions the rows
// request sends for them, and the chip text that shows each one. Import-free and DOM-free: unit-tested under node
// (tests/test_workbench_js.py); table.js owns the popover that builds specs and the chip strip that shows them.
//
// A spec is one of
//   {kind: "num",   op: "eq|ne|lt|lte|gt|gte", val}        a numeric comparison
//   {kind: "num",   op: "between", lo, hi}                 an inclusive range
//   {kind: "empty", op: "isnull|notnull"}                  null test
//   {kind: "text",  val}                                   case-insensitive substring (the server's `contains`)
//   {kind: "set",   mode: "any_of|all_of|none_of", values: [...], listLike, bool}
//     a value list: `values` are the strings the /distinct route returned; `listLike` says the column holds
//     comma-separated tokens (`allowed_uses = "auto,truck"`), so the server's token operators apply; `bool` says
//     the column is boolean, so "True"/"False" are sent as booleans.

const NUM_OPS = { eq: "=", ne: "≠", lt: "<", lte: "≤", gt: ">", gte: "≥" };
const MODES = { any_of: "any of", all_of: "all of", none_of: "none of" };
const CHIP_VALUES = 4; // values shown on a chip before "+n"

const isNum = v => typeof v === "number" && Number.isFinite(v);

// `spec`, normalized, or an Error naming what is wrong with it (the popover shows the message as a toast).
function check(spec) {
  if (!spec || typeof spec !== "object") throw new Error("A filter needs a kind");
  const { kind } = spec;
  if (kind === "num") {
    if (spec.op === "between") {
      if (!isNum(spec.lo) || !isNum(spec.hi)) throw new Error("Between needs two numbers");
      if (spec.lo > spec.hi) throw new Error("Between: the low end is above the high end");
      return { kind, op: "between", lo: spec.lo, hi: spec.hi };
    }
    if (!Object.hasOwn(NUM_OPS, spec.op)) throw new Error(`Unknown numeric operator "${spec.op}"`);
    if (!isNum(spec.val)) throw new Error("Enter a number");
    return { kind, op: spec.op, val: spec.val };
  }
  if (kind === "empty") {
    if (spec.op !== "isnull" && spec.op !== "notnull") throw new Error(`Unknown empty-test operator "${spec.op}"`);
    return { kind, op: spec.op };
  }
  if (kind === "text") {
    const val = spec.val == null ? "" : String(spec.val).trim();
    if (!val) throw new Error("Enter the text to look for");
    return { kind, val };
  }
  if (kind === "set") {
    if (!Object.hasOwn(MODES, spec.mode)) throw new Error(`Unknown mode "${spec.mode}"`);
    const values = Array.isArray(spec.values) ? spec.values.map(String) : [];
    if (!values.length) throw new Error("Pick at least one value");
    const listLike = Boolean(spec.listLike);
    if (spec.mode === "all_of" && !listLike) throw new Error('"All of" only applies to a column of comma-separated values');
    const out = { kind, mode: spec.mode, values, listLike };
    if (spec.bool) out.bool = true;
    return out;
  }
  throw new Error(`Unknown filter kind "${kind}"`);
}

const asBool = v => (v === "True" || v === "true" ? true : v === "False" || v === "false" ? false : v);

// The server condition for one column's spec.
export function condition(col, spec) {
  switch (spec.kind) {
    case "num": return spec.op === "between" ? { col, op: "between", val: [spec.lo, spec.hi] } : { col, op: spec.op, val: spec.val };
    case "empty": return { col, op: spec.op, val: null };
    case "text": return { col, op: "contains", val: spec.val };
    default: { // set
      const val = spec.bool ? spec.values.map(asBool) : spec.values;
      // A plain column's "any of" is membership; its "none of" still goes through the token operator (a
      // single-token cell is its own token list), which is what the server's none_of does for plain cells.
      const op = spec.mode === "any_of" && !spec.listLike ? "in" : spec.mode;
      return { col, op, val };
    }
  }
}

// What a chip says for one column's spec: `free_speed > 40`, `allowed_uses any of auto, truck`, `name contains "Pine"`.
export function chipText(col, spec) {
  switch (spec.kind) {
    case "num": return spec.op === "between" ? `${col} ${spec.lo}–${spec.hi}` : `${col} ${NUM_OPS[spec.op]} ${spec.val}`;
    case "empty": return `${col} ${spec.op === "isnull" ? "is empty" : "is not empty"}`;
    case "text": return `${col} contains "${spec.val}"`;
    default: {
      const shown = spec.values.slice(0, CHIP_VALUES).join(", ");
      const more = spec.values.length - CHIP_VALUES;
      return `${col} ${MODES[spec.mode]} ${shown}${more > 0 ? ` +${more}` : ""}`;
    }
  }
}

// The filter model for one table: insertion-ordered, one spec per column.
export function createGridFilter() {
  const specs = new Map();
  return {
    // Set (or, with a null spec, drop) a column's filter; a malformed spec throws and changes nothing.
    setFilter(col, spec) {
      if (spec == null) { specs.delete(col); return; }
      specs.set(col, check(spec));
    },
    remove(col) { specs.delete(col); },
    clear() { specs.clear(); },
    get(col) { return specs.has(col) ? { ...specs.get(col) } : null; },
    active() { return [...specs.keys()]; },
    conditions() { return [...specs].map(([col, spec]) => condition(col, spec)); },
    chipText(col) { return specs.has(col) ? chipText(col, specs.get(col)) : ""; },
  };
}
