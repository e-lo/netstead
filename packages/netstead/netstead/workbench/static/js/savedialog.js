// The Save dialog (#save-dialog, a native <dialog>): name, format, a folder picked in the file browser, and, when the
// active network has open edit warnings or its live check failed, the warning text with a "Save anyway" checkbox
// (= accept_warnings, an explicit, recorded choice). It dispatches the save_network job. The server checks again:
// a refused save (check_failed) reopens the dialog with the server's reason instead of a window.confirm.
import { $, toast } from "./dom.js";
import { savePrompt, savedText } from "./editmodel.js";
import { dispatchJob } from "./edits.js";
import { createFileBrowser } from "./filebrowser.js";
import { store } from "./store.js";

let browser = null, folder = null;
const activeId = () => { const s = store.get().server; return s && s.active; };
const dialog = () => $("save-dialog");

function setError(text) {
  $("save-error").textContent = text || "";
  $("save-error").hidden = !text;
}

// The warnings section: `text` is a savePrompt() message (its trailing question dropped); "" hides it.
function setWarning(text) {
  const body = (text || "").replace(/\n*Save the copy anyway\?$/, "");
  $("save-warn-text").textContent = body;
  $("save-warn").hidden = !body;
  $("save-accept").checked = false;
}

function setFolder(path) {
  folder = path;
  $("save-folder").textContent = path ? `Saving into ${path}` : "No folder chosen yet: pick one below.";
}

function activeLabel() {
  const s = store.get().server, h = s && s.networks.find(n => n.id === s.active);
  return h ? h.label : "";
}

export function openSaveDialog() {
  if (!activeId()) { toast("Open a network first."); return; }
  const name = $("ed-save-name");
  if (!name.value.trim()) name.value = `${activeLabel().replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "network"}-copy`;
  const e = store.get().edits;
  setWarning(savePrompt(e && e.net_id === activeId() ? e : null));
  setError("");
  setFolder(folder);
  browser ||= createFileBrowser($("ed-save-fb"), { kinds: [], pickFolder: true, onPick: entry => setFolder(entry.path) });
  browser.show();
  if (!dialog().open) dialog().showModal();
  name.focus();
  name.select();
}

export function closeSaveDialog() { if (dialog().open) dialog().close(); }

// A save ended: report where it went, or reopen the dialog with the server's reason for refusing.
function onSaveEnded(job, action) {
  if (job.status === "done") { toast(savedText(job.result), "info"); return; }
  if (job.status !== "failed" || !job.payload || !("check_failed" in job.payload)) return;
  if (action.accept_warnings) return; // refused even so: the Jobs panel has the failure
  openSaveDialog();
  setWarning(savePrompt(job.payload));
  setError("The server refused the save: tick “Save anyway” to save over these warnings.");
}

function save() {
  const name = $("ed-save-name").value.trim();
  if (!name) { setError("Name the copy first."); $("ed-save-name").focus(); return; }
  if (!folder) { setError("Choose a folder below."); return; }
  const action = { type: "save_network", output_dir: folder, name, output_format: $("ed-save-format").value };
  if (!$("save-warn").hidden) {
    if (!$("save-accept").checked) { setError("Tick “Save anyway” to save over the open warnings, or fix them first."); return; }
    action.accept_warnings = true;
  }
  closeSaveDialog();
  toast(`Saving a copy as ${name}… (see Jobs)`, "info");
  dispatchJob(action, onSaveEnded).catch(e => toast(e.message));
}

export function wireSaveDialog() {
  $("save-btn").onclick = openSaveDialog;
  $("ed-save").onclick = save;
  $("save-cancel").onclick = closeSaveDialog;
  $("save-form").onsubmit = e => { e.preventDefault(); save(); }; // Enter in the name box saves
  $("save-accept").onchange = () => setError("");
  dialog().addEventListener("cancel", e => { e.preventDefault(); closeSaveDialog(); }); // Escape
  dialog().addEventListener("click", e => { if (e.target === dialog()) closeSaveDialog(); }); // the backdrop
}
