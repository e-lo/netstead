// JSON Schema -> form fields, and form input -> values. The Settings dialog (settingsform.js, settings.js) and
// wb.schemaForm (formview.js) share it, so every form looks and parses alike (plugins design, UX principle 5).
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
// dom.js's `esc`, repeated because a pure module can't import dom.js (pluginlist.js uses this one).
export const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

// A `$ref` node -> its definition, with the node's own keys (a field's default, description, title) on top.
export function resolveRef(schema, node) {
  if (!node || !node.$ref) return node;
  const { $ref, ...own } = node;
  return { ...((schema.$defs || {})[$ref.split("/").pop()] || {}), ...own };
}

// resolveRef, and also inside `anyOf` (pydantic writes Optional[SomeEnum] as anyOf [{$ref}, {type: null}]).
const resolveField = (schema, node) => {
  const p = resolveRef(schema, node);
  return p && Array.isArray(p.anyOf) ? { ...p, anyOf: p.anyOf.map(o => resolveRef(schema, o)) } : p;
};

// A schema bound, kept only when it is a finite number: plugin schemas pass `json_schema_extra` through verbatim.
const bound = v => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function fieldKind(prop) {
  const options = prop.anyOf ? prop.anyOf.filter(o => o.type !== "null") : [prop];
  const nullable = Boolean(prop.anyOf && prop.anyOf.some(o => o.type === "null"));
  const p = options.length === 1 ? options[0] : null;
  if (!p) return { kind: "json", nullable };
  if (p.enum) return { kind: "choice", options: p.enum, nullable };
  if (p.type === "boolean") return { kind: "bool", nullable };
  if (p.type === "integer" || p.type === "number") {
    return { kind: p.type === "integer" ? "int" : "float", nullable,
      min: bound(p.minimum) ?? bound(p.exclusiveMinimum), max: bound(p.maximum) ?? bound(p.exclusiveMaximum) };
  }
  if (p.type === "string") return { kind: "text", nullable };
  if (p.type === "array" && p.items && p.items.type === "string") return { kind: "list", nullable };
  return { kind: "json", nullable };
}

// A JSON Schema object (an Action's, a plugin's) -> one field per property:
// {key, label, kind, options, nullable, min, max, value, default, required, description, group}.
// A `const` property (an Action's `type`) is fixed, so it gets no field; `omit` drops others by name. One level of
// nested object becomes a group of dotted keys ("where.city"); anything deeper is a JSON field.
export function fieldsFrom(schema, value = {}, { omit = [] } = {}) {
  const required = new Set(schema.required || []);
  const fields = [];
  for (const [name, raw] of Object.entries(schema.properties || {})) {
    const prop = resolveField(schema, raw);
    if (omit.includes(name) || prop.const !== undefined) continue;
    if (prop.type === "object" && prop.properties) {
      const inner = new Set(prop.required || []);
      for (const [sub, subRaw] of Object.entries(prop.properties)) {
        const key = `${name}.${sub}`;
        fields.push(describe(key, resolveField(schema, subRaw), getPath(value, key),
          required.has(name) && inner.has(sub), prop.title || name));
      }
    } else {
      fields.push(describe(name, prop, getPath(value, name), required.has(name), null));
    }
  }
  return fields;
}

// Whether a number field's bounds are exclusive (pydantic's gt / lt), so formErrors refuses the bound itself.
function exclusive(prop) {
  const options = prop.anyOf ? prop.anyOf.filter(o => o.type !== "null") : [prop];
  const p = options.length === 1 ? options[0] : {};
  const flag = (inclusive, excl) => bound(p[inclusive]) === null && bound(p[excl]) !== null;
  return { ...(flag("minimum", "exclusiveMinimum") ? { exclusiveMin: true } : {}),
    ...(flag("maximum", "exclusiveMaximum") ? { exclusiveMax: true } : {}) };
}

function describe(key, prop, value, required, group) {
  return { key, label: prop.title || key.split(".").pop(), ...fieldKind(prop), ...exclusive(prop), value: value ?? null,
    default: prop.default ?? null, required, description: prop.description || "", group };
}

export const getPath = (obj, key) => key.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

// A copy of `obj` with dotted `key` set to `v`; `null` removes the key (an empty input means "not given"), unless
// `keepNull` (a required field that may be None sends an explicit null).
export function setPath(obj, key, v, keepNull = false) {
  const out = JSON.parse(JSON.stringify(obj || {}));
  const parts = key.split(".");
  let o = out;
  for (const p of parts.slice(0, -1)) o = o[p] && typeof o[p] === "object" ? o[p] : (o[p] = {});
  if (v === null && !keepNull) delete o[parts[parts.length - 1]];
  else o[parts[parts.length - 1]] = v;
  return out;
}

// A required field that may be None: blank means an explicit null, not "left out" (there is no default to fall to).
export const explicitNull = f => Boolean(f.required && f.nullable && f.default === null);

// What a generated form shows, made its value too, so what is sent is what is seen: a checkbox or a non-nullable
// menu always shows a value (its default, else unchecked / the first option), and a blank required-but-nullable
// field is an explicit null. Returns {fields, value} (copies).
export function fillShown(fields, value) {
  let out = JSON.parse(JSON.stringify(value || {}));
  const filled = fields.map(f => {
    if (f.value !== null) return f;
    if (explicitNull(f)) { out = setPath(out, f.key, null, true); return f; }
    if (f.nullable || !(f.kind === "bool" || (f.kind === "choice" && f.options.length))) return f;
    const shown = f.default ?? (f.kind === "bool" ? false : f.options[0]);
    out = setPath(out, f.key, shown);
    return { ...f, value: shown };
  });
  return { fields: filled, value: out };
}

// What a submit would trip on: entries that didn't parse (`bad`: their keys; the value still holds the last good
// one, so it must not be sent), required fields left empty, numbers out of bounds ([] when the form can be sent).
// The server validates again; this only saves a round trip.
export function formErrors(fields, value, bad = new Set()) {
  const errors = [];
  for (const f of fields) {
    if (bad.has(f.key)) { errors.push(`${f.label}: fix this entry`); continue; }
    const v = getPath(value, f.key);
    if (v === null && explicitNull(f)) continue; // None, chosen on purpose
    if (v === undefined || v === null || v === "") {
      if (f.required && f.default === null) errors.push(`${f.label} is required`);
      continue;
    }
    if (typeof v !== "number") continue;
    if (f.min != null && (f.exclusiveMin ? v <= f.min : v < f.min)) {
      errors.push(`${f.label} must be ${f.exclusiveMin ? "more than" : "at least"} ${f.min}`);
    }
    if (f.max != null && (f.exclusiveMax ? v >= f.max : v > f.max)) {
      errors.push(`${f.label} must be ${f.exclusiveMax ? "less than" : "at most"} ${f.max}`);
    }
  }
  return errors;
}

// An input's raw value -> {ok, value} or {ok: false, error}. Empty means "reset": null removes the key from that layer.
export function parseInput(field, raw) {
  if (field.kind === "bool") {
    // A nullable bool is a menu in a generated form ("", "true", "false"); a checkbox passes `checked`.
    if (typeof raw === "string" && field.nullable) return { ok: true, value: raw === "" ? null : raw === "true" };
    return { ok: true, value: Boolean(raw) };
  }
  if (raw === "" || raw === null || raw === undefined) return { ok: true, value: null };
  if (field.kind === "choice") {  // back to the option itself: Literal[1, 2] and IntEnum choices stay numbers
    const option = (field.options || []).find(o => String(o) === String(raw));
    return { ok: true, value: option === undefined ? String(raw) : option };
  }
  if (field.kind === "text") return { ok: true, value: String(raw) };
  if (field.kind === "int" || field.kind === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n) || (field.kind === "int" && !Number.isInteger(n))) {
      return { ok: false, error: `${field.label}: enter a ${field.kind === "int" ? "whole " : ""}number` };
    }
    return { ok: true, value: n };
  }
  if (field.kind === "list") return { ok: true, value: String(raw).split(",").map(s => s.trim()).filter(Boolean) };
  try { return { ok: true, value: JSON.parse(raw) }; } catch (e) { return { ok: false, error: `${field.label}: not valid JSON` }; }
}

// A form control's state ({type, value, checked, badInput}) -> parseInput's answer. A number input the browser
// cannot parse ("-", "1e") reports an empty value; that must not read as "reset" and delete the saved value.
export function parseControl(field, { type, value, checked, badInput }) {
  if (badInput) return { ok: false, error: `${field.label}: enter a ${field.kind === "int" ? "whole " : ""}number` };
  return parseInput(field, type === "checkbox" ? checked : value);
}

// A generated form's control id for dotted `key`: every character but a letter, digit or "_" is written as its code,
// so "a-b" and "a.b" get different ids (the Settings dialog keeps its own "set-io-allowed_roots" ids).
export const fieldId = (prefix, key) =>
  prefix + String(key).replace(/[^A-Za-z0-9_]/g, c => `-${c.charCodeAt(0).toString(16).padStart(4, "0")}`);

// The blank choice: "(default)" when leaving it blank falls back to something, "None" when it sends null.
const blankLabel = f => (f.default !== null || !f.required ? "(default)" : "None");

// One form control for a field. The Settings dialog uses idPrefix "set-"; `data-key` names the field for parseControl.
// `form: true` (wb.schemaForm) uses fieldId, a menu for a nullable bool, and a JSON box with no value left empty (its
// default as the placeholder); the Settings dialog's markup is unchanged.
export function inputHTML(f, idPrefix = "set-", { form = false } = {}) {
  const id = form ? fieldId(idPrefix, f.key) : `${idPrefix}${f.key.replace(/\./g, "-")}`;
  const attrs = `id="${esc(id)}" data-key="${esc(f.key)}"${f.readonly ? " disabled" : ""}` +
    (f.required ? ' aria-required="true"' : "");
  if (f.kind === "bool" && form && f.nullable) {
    const opts = [["", blankLabel(f)], ["true", "true"], ["false", "false"]];
    const now = f.value === null ? "" : String(f.value);
    return `<select ${attrs}>${opts.map(([v, label]) => `<option value="${v}"${v === now ? " selected" : ""}>` +
      `${esc(label)}</option>`).join("")}</select>`;
  }
  if (f.kind === "bool") return `<input type="checkbox" ${attrs}${f.value ? " checked" : ""}>`;
  if (f.kind === "choice") {
    const opts = (f.nullable ? [""] : []).concat(f.options);
    return `<select ${attrs}>${opts.map(o => `<option value="${esc(o)}"${o === (f.value ?? "") ? " selected" : ""}>` +
      `${esc(o === "" ? blankLabel(f) : o)}</option>`).join("")}</select>`;
  }
  if (f.kind === "int" || f.kind === "float") {
    const bounds = (f.min != null ? ` min="${esc(f.min)}"` : "") + (f.max != null ? ` max="${esc(f.max)}"` : "");
    return `<input type="number" ${attrs} step="${f.kind === "int" ? 1 : "any"}"${bounds} value="${esc(f.value ?? "")}" ` +
      `placeholder="${esc(f.default ?? "default")}">`;
  }
  if (f.kind === "json" && form && f.value === null) {
    const hint = f.default === null ? "JSON" : JSON.stringify(f.default);
    return `<textarea ${attrs} rows="3" spellcheck="false" placeholder="${esc(hint)}"></textarea>`;
  }
  if (f.kind === "json") return `<textarea ${attrs} rows="3" spellcheck="false">${esc(JSON.stringify(f.value ?? null, null, 1))}</textarea>`;
  const text = f.kind === "list" ? (f.value || []).join(", ") : f.value ?? "";
  const hint = f.kind === "list" ? "comma-separated" : f.default ?? "default";
  return `<input ${attrs} value="${esc(text)}" placeholder="${esc(hint)}" spellcheck="false">`;
}
