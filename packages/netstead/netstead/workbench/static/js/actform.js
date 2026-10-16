// One Action's form, generated from its JSON Schema: how a plugin Action runs with no plugin UI (plugins design,
// "declarative first"). Each plugin Action's palette command opens it (plugins.js). Run dispatches the Action, so it
// is recorded in history like any other. A modal dialog: focus moves in, Tab is trapped, Escape closes, and focus
// returns to the opener. The title and description come from the plugin: they are set as text, never as markup.
import { dispatch } from "./api.js";
import { $ } from "./dom.js";
import { schemaForm } from "./formview.js";
import { trapTab } from "./modal.js";

let opener = null, form = null, current = null;
let opened = 0; // bumped on each open: a run still in flight doesn't write into a form opened after it

export const actionFormOpen = () => !$("actform").hidden;

export function openActionForm(entry, prefill = {}) {
  if (!actionFormOpen()) opener = document.activeElement;
  current = entry;
  opened++;
  $("actform-run").disabled = false;
  $("actform-title").textContent = entry.label;
  $("actform-desc").textContent = entry.description || "";
  $("actform-result").textContent = "";
  form = schemaForm($("actform-body"), entry.schema, prefill, { omit: ["type"], onChange: () => { $("actform-result").textContent = ""; } });
  $("actform").hidden = false;
  if (!form.focus()) $("actform-run").focus();
}

export function closeActionForm() {
  if (!actionFormOpen()) return;
  $("actform").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

async function run() {
  if ($("actform-run").disabled) return; // already running (Enter pressed twice)
  const errors = form.errors();
  if (errors.length) { $("actform-result").textContent = errors.join("; "); return; }
  const mine = opened, show = text => { if (mine === opened) $("actform-result").textContent = text; };
  $("actform-run").disabled = true;
  try {
    const result = await dispatch({ ...form.value(), type: current.type });
    show(result == null ? "Done." : `Done: ${typeof result === "string" ? result : JSON.stringify(result)}`);
  } catch (e) {
    show(e.message);
  } finally {
    if (mine === opened) $("actform-run").disabled = false;
  }
}

// Enter in a one-line field runs the form, after reading that field (its change event may not have fired yet).
function onEnter(e) {
  if (e.key !== "Enter" || e.isComposing || !e.target.matches("#actform-body input")) return;
  e.preventDefault();
  e.target.dispatchEvent(new Event("change", { bubbles: true }));
  run();
}

export function wireActionForm() {
  $("actform-close").onclick = closeActionForm;
  $("actform-run").onclick = () => run();
  $("actform").addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeActionForm(); return; }
    onEnter(e);
    trapTab($("actform"), e);
  });
}
