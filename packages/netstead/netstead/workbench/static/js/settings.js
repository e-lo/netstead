// Settings dialog: a form generated from the Settings JSON schema; every change is a set_setting action.
// Other modules add their own sections with registerSection (the Language models panel is one).
import { dispatch, getJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { trapTab } from "./modal.js";
import { clearHint, resetScope, scopeNote, sectionsFrom } from "./settingsform.js";
import { inputHTML, parseControl } from "./schemaform.js";

const extra = new Map(); // id -> {label, element, onShow}
let payload = null, sections = [], current = null, opener = null, refused = null;

export function registerSection(id, label, element, onShow) { extra.set(id, { label, element, onShow }); }

export const settingsOpen = () => !$("settings").hidden;

// A modal dialog: focus moves in on open, Tab cycles inside it, and focus returns to the opener on close.
export function closeSettings() {
  $("settings").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

export async function openSettings(section) {
  if (!settingsOpen()) opener = document.activeElement;
  $("settings").hidden = false;
  await refreshSettings();
  showSection(section || current || (sections[0] && sections[0].name));
  const first = $("set-nav").querySelector("button.on") || $("set-close");
  if (!$("settings").contains(document.activeElement)) first.focus();
}

export async function refreshSettings() {
  payload = await getJSON("/api/settings");
  sections = sectionsFrom(payload);
  $("set-paths").textContent = `User file: ${payload.paths.user} · Project file: ${payload.paths.project}`;
  renderNav();
  if (current && !extra.has(current)) renderForm();
}

function renderNav() {
  const items = sections.map(s => [s.name, s.title]).concat([...extra].map(([id, e]) => [id, e.label]));
  const focused = $("set-nav").contains(document.activeElement) ? document.activeElement.dataset.sec : null;
  $("set-nav").innerHTML = items
    .map(([id, label]) => `<button data-sec="${esc(id)}"${id === current ? ' class="on" aria-current="true"' : ""}>` +
      `${esc(label)}</button>`)
    .join("");
  if (focused) { const b = $("set-nav").querySelector(`[data-sec="${CSS.escape(focused)}"]`); if (b) b.focus(); }
}

function showSection(id) {
  current = id;
  for (const b of $("set-nav").querySelectorAll("button")) {
    b.classList.toggle("on", b.dataset.sec === id);
    if (b.dataset.sec === id) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
  }
  const ext = extra.get(id);
  $("set-form").hidden = Boolean(ext);
  // A registered section saves on its own terms (Language models: user), so the Save to menu goes, label and all.
  for (const el of [$("set-scope"), document.querySelector(".set-scope-lbl")]) el.hidden = Boolean(ext);
  for (const [key, e] of extra) $(e.element).hidden = key !== id;
  if (ext) Promise.resolve(ext.onShow()).catch(e => toast(e.message));
  else renderForm();
}

function fieldHTML(f, scope) {
  const reset = !f.readonly && resetScope(f);
  const note = !f.readonly && scopeNote(f, scope);
  return `<div class="set-field"><label for="set-${esc(f.key.replace(/\./g, "-"))}">${esc(f.label)}` +
    `${f.restart ? ' <span class="tag">applies on next launch</span>' : ""}</label>${inputHTML(f)}` +
    `<span class="src src-${esc(f.source)}" title="Where the current value comes from">${esc(f.source)}</span>` +
    (reset ? `<button class="mini ghost" data-reset="${esc(f.key)}" data-scope="${reset}" ` +
      `title="Remove it from the ${reset} layer">Reset</button>` : "<span></span>") +
    (f.readonly ? `<div class="set-why">${esc(f.readonly)}</div>` : "") +
    (note ? `<div class="set-why">${esc(note)}</div>` : "") + "</div>";
}

// Re-rendering (after any save) keeps the field being edited: its focus, its caret and any unsaved typing.
function renderForm() {
  const sec = sections.find(s => s.name === current);
  if (!sec) return;
  const scope = $("set-scope").value;
  const active = $("set-form").contains(document.activeElement) ? document.activeElement : null;
  const kept = active && active.dataset.key ? { key: active.dataset.key, value: active.value,
    edited: "defaultValue" in active && active.value !== active.defaultValue && active.dataset.key !== refused,
    caret: caretOf(active) } : null;
  refused = null;
  $("set-form").innerHTML = (sec.description ? `<p class="llm-note">${esc(sec.description)}</p>` : "") +
    (sec.note ? `<p class="set-note">${esc(sec.note)}</p>` : "") + sec.fields.map(f => fieldHTML(f, scope)).join("");
  const el = kept && $("set-form").querySelector(`[data-key="${CSS.escape(kept.key)}"]`);
  if (!el) return;
  if (kept.edited) el.value = kept.value;
  el.focus();
  if (kept.caret) try { el.setSelectionRange(...kept.caret); } catch (e) { /* number inputs have no caret */ }
}

function caretOf(el) {
  try { return el.selectionStart == null ? null : [el.selectionStart, el.selectionEnd]; } catch (e) { return null; }
}

async function save(key, value, scope) {
  try {
    await dispatch({ type: "set_setting", key, value, scope });
  } catch (e) {
    toast(e.message);
    refused = key; // put the control back to the real value (a 422 is not in history), even while it has focus
    await refreshSettings().catch(() => {});
  }
}

// Any set_setting, from this dialog, Python, another tab or the assistant, refreshes an open dialog.
export function onSettingsHistory(entry) {
  if (entry.action.type === "set_setting" && settingsOpen()) refreshSettings().catch(e => toast(e.message));
}

export function wireSettings() {
  $("settings-btn").onclick = () => openSettings().catch(e => toast(e.message));
  $("set-close").onclick = closeSettings;
  $("set-nav").onclick = e => { const b = e.target.closest("button[data-sec]"); if (b) showSection(b.dataset.sec); };
  $("set-scope").onchange = () => renderForm();
  $("set-form").onchange = e => {
    const el = e.target.closest("[data-key]");
    if (!el) return;
    const field = sections.flatMap(s => s.fields).find(f => f.key === el.dataset.key);
    const scope = $("set-scope").value;
    const parsed = parseControl(field, { type: el.type, value: el.value, checked: el.checked,
      badInput: Boolean(el.validity && el.validity.badInput) });
    if (!parsed.ok) { toast(parsed.error); return; }
    const hint = parsed.value === null && clearHint(field, scope);
    if (hint) { toast(hint); if ("defaultValue" in el) el.value = el.defaultValue; else renderForm(); return; }
    save(field.key, parsed.value, scope);
  };
  $("set-form").onclick = e => {
    const b = e.target.closest("button[data-reset]");
    if (b) save(b.dataset.reset, null, b.dataset.scope);
  };
  $("settings").addEventListener("keydown", e => trapTab($("settings"), e));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && settingsOpen()) closeSettings(); });
}
