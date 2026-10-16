// wb.schemaForm: a form generated from a JSON Schema, with the Settings dialog's field kinds, controls and parsing
// (schemaform.js). Labels are real <label for>; required fields carry aria-required; a bad entry is reported on its
// control (setCustomValidity), and the form's value is never changed by it. Schema text (titles, descriptions, enum
// values) comes from plugins, so every piece of it is escaped.
import { esc } from "./dom.js";
import { explicitNull, fieldId, fieldsFrom, fillShown, formErrors, inputHTML, parseControl, setPath } from "./schemaform.js";

let forms = 0;
const copy = v => JSON.parse(JSON.stringify(v || {}));

// Render a form for `schema` into `el`, prefilled from `value`. `onChange(value, {errors})` runs after each edit.
// Returns {value(), errors(), set(value), focus()}.
export function schemaForm(el, schema, value = {}, { onChange = () => {}, omit = [] } = {}) {
  const prefix = `sf${++forms}-`;
  let current = copy(value);
  let fields = [];
  const bad = new Set(); // keys whose entry didn't parse: `current` still holds their last good value
  const draw = () => {
    // A checkbox or a menu always shows a value: make it the form's value too, so what is sent is what is seen.
    ({ fields, value: current } = fillShown(fieldsFrom(schema || {}, current, { omit }), current));
    bad.clear();
    let group = null;
    el.innerHTML = fields.map(f => {
      const head = f.group && f.group !== group ? `<div class="sf-group">${esc(f.group)}</div>` : "";
      group = f.group;
      return `${head}<div class="sf-field"><label for="${esc(fieldId(prefix, f.key))}">${esc(f.label)}` +
        `${f.required ? ' <span class="sf-req" aria-hidden="true">*</span>' : ""}</label>${inputHTML(f, prefix, { form: true })}` +
        (f.description ? `<div class="sf-help">${esc(f.description)}</div>` : "") + "</div>";
    }).join("") || '<p class="empty">Nothing to fill in.</p>';
  };
  const api = {
    value: () => copy(current),
    errors: () => formErrors(fields, current, bad),
    set(next) { current = copy(next); draw(); },
    focus() { const c = el.querySelector("[data-key]:not([disabled])"); if (c) c.focus(); return Boolean(c); },
  };
  el.classList.add("sf");
  el.onchange = e => {
    const control = e.target.closest("[data-key]");
    if (!control) return;
    const field = fields.find(f => f.key === control.dataset.key);
    if (!field) return;
    const parsed = parseControl(field, { type: control.type, value: control.value, checked: control.checked,
      badInput: Boolean(control.validity && control.validity.badInput) });
    control.setCustomValidity(parsed.ok ? "" : parsed.error);
    if (!parsed.ok) { bad.add(field.key); control.reportValidity(); return; }
    bad.delete(field.key);
    current = setPath(current, field.key, parsed.value, explicitNull(field));
    onChange(api.value(), { errors: api.errors() });
  };
  draw();
  return api;
}
