// The record editor (Details → Edit…, or "Edit record…" in the palette) and the fix editor (an issue's Fix…): a
// generated form (wb.schemaForm, formview.js) over the record's editable columns, or just the issue's column,
// that sends one EditCells action with only what changed. Ported from the offline report's "Fix locally"
// mini-editor (map_component.js). Delete asks the server what the cascade would remove (a dry run) first.
// Column names, values and notes are data: every one is escaped (schemaForm escapes its own labels).
import { dispatch, getJSON, netPath, postJSON } from "./api.js";
import { esc, toast } from "./dom.js";
import { changedValues, deleteAction, deletePlanText, recordSchema } from "./editmodel.js";
import { schemaForm } from "./formview.js";
import { activeSelection, store } from "./store.js";

// Open the editor in `host` for {table, id, column?, note?}; it replaces whatever editor `host` held.
export async function openFixEditor(host, { table, id, column = null, note = "" }) {
  const s = store.get(), net = s.server && s.server.active;
  if (!host || !net) return;
  const [rec, schema] = await Promise.all([
    getJSON(netPath(net, `feature/${encodeURIComponent(table)}/${encodeURIComponent(id)}`)),
    getJSON(netPath(net, `table/${encodeURIComponent(table)}/schema`)),
  ]);
  if (store.get().server.active !== net) return; // the network changed while the record loaded
  const formSchema = recordSchema(schema.columns, schema.primary_key, column ? [column] : null);
  const keys = Object.keys(formSchema.properties);
  const before = Object.fromEntries(keys.map(k => [k, rec.attributes[k] ?? null]));
  const sel = activeSelection(s);
  const many = table === "link" && sel && sel.link_ids.includes(id) && sel.link_ids.length > 1;
  host.innerHTML = `<div class="fix-editor" role="group" aria-label="${esc(`Edit ${table} ${id}`)}">` +
    `<div class="row"><b>${esc(table)} ${esc(id)}</b>${column ? ` <span class="diag">${esc(column)}</span>` : ""}</div>` +
    `<div class="fx-form"></div>` +
    `<div class="row"><input class="fx-note grow" aria-label="Note" placeholder="Why (kept with the edit)" value="${esc(note || "")}"></div>` +
    (many ? `<label class="row"><input type="checkbox" class="fx-sel"> all ${sel.link_ids.length} selected links</label>` : "") +
    `<div class="row"><button class="mini fx-apply">Apply</button><button class="mini ghost fx-cancel">Cancel</button>` +
    `<button class="mini ghost fx-delete">Delete…</button></div></div>`;
  const el = host.querySelector(".fix-editor");
  const form = schemaForm(el.querySelector(".fx-form"), formSchema, before);
  const close = () => el.remove();
  el.onkeydown = e => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  el.querySelector(".fx-cancel").onclick = close;
  el.querySelector(".fx-delete").onclick = () => deleteRecord(table, id).then(done => { if (done) close(); });
  el.querySelector(".fx-apply").onclick = async () => {
    // A field that did not parse is an error here, never its last good value sent in its place.
    const errors = form.errors();
    if (errors.length) { toast(errors.join("; ")); return; }
    const values = changedValues(before, form.value(), keys);
    if (!Object.keys(values).length) { toast("Nothing changed."); return; }
    const box = el.querySelector(".fx-sel"), why = el.querySelector(".fx-note").value.trim();
    const action = { type: "edit_cells", table, ids: box && box.checked ? [...sel.link_ids] : [id], values };
    if (why) action.note = why;
    try { await dispatch(action); close(); } catch (e) { toast(e.message); }
  };
  form.focus();
}

// Delete one record after showing what goes with it (the server's dry run); resolves to whether it was deleted.
export async function deleteRecord(table, id) {
  try {
    const net = store.get().server.active;
    const plan = await postJSON(netPath(net, `table/${encodeURIComponent(table)}/delete-plan`), { ids: [id] });
    if (plan.refused) { toast(plan.refused); return false; }
    if (!window.confirm(deletePlanText(plan))) return false;
    await dispatch(deleteAction(table, [id], { version: plan.version }));
    return true;
  } catch (e) { toast(e.message); return false; }
}
