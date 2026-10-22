// Core's validate and edit commands, registered like a plugin's. All are palette commands except Fix…, the one
// feature / row menu command, which applies only to a record with an issue (R8: right-click stays silent
// otherwise). Core commands may read the page's store; plugin commands only get the frozen ctx.
import { dispatch } from "./api.js";
import { $, toast } from "./dom.js";
import { addCommandTo, commandContext, runCommand } from "./commands.js";
import { editSummary, undoBlocked } from "./editmodel.js";
import { dispatchJob } from "./edits.js";
import { deleteRecord, openFixEditor } from "./fixeditor.js";
import { recordIssues } from "./issuelist.js";
import { openSaveDialog } from "./savedialog.js";
import { detailsSettled } from "./side.js";
import { CORE, slots } from "./slots.js";
import { store } from "./store.js";
import { showPanel } from "./workspaces.js";

const fail = e => toast(e.message);
const add = spec => addCommandTo(slots, store, CORE, { contexts: ["palette"], ...spec });

// Run one of core's registered commands with the current context (a button that does what a command does).
export function runCore(id) {
  const command = slots.commands.get(id), s = store.get();
  if (!command) return;
  const ctx = commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace });
  runCommand(command, ctx, (c, e) => fail(e));
}

// The fix target for a right-clicked (or focused) record: its first live edit warning, narrowed to that column; else
// the whole record when the last validation located an issue on it. Null when it has none, so no Fix… menu.
function recordFixTarget(t) {
  const s = store.get(), net = s.server && s.server.active;
  if (!t || !net) return null;
  const same = x => x.table === t.table && String(x.key) === String(t.id);
  const w = s.edits && s.edits.net_id === net ? s.edits.warnings.find(same) : null;
  if (w) return { table: t.table, id: t.id, column: w.column, note: w.message };
  const records = s.issues && s.issues.net_id === net ? s.issues.records : null;
  return recordIssues(records, t.table, t.id).length ? { table: t.table, id: t.id } : null;
}

// Focus the record (Details shows it), then open the record editor in Details.
async function editRecord(table, id) {
  showPanel("inspect");
  const f = store.get().focus;
  if (!f || f.table !== table || f.id !== id) store.set({ focus: { table, id, from: "table" } });
  await detailsSettled();
  const host = document.querySelector("#details .det-edit");
  if (host) await openFixEditor(host, { table, id });
}

export function registerEditCommands() {
  add({ id: "run_validation", group: "Issues", title: "Run validation", when: c => Boolean(c.network),
    run: () => { showPanel("validate"); return dispatchJob({ type: "run_validation" }); } });
  add({ id: "fix_issue", group: "Issues", title: "Fix the picked issue…", when: () => Boolean(store.get().fixTarget),
    run: () => { showPanel("validate"); return openFixEditor($("iss-fixer"), store.get().fixTarget); } });
  // The only core context-menu command (R8): on a record with an issue, focus it, filter Issues to it, and fix.
  // The record is already in view (it was right-clicked, or is focused), so the focus does not move the map.
  add({ id: "fix_record", title: "Fix…", contexts: ["feature", "row"], when: c => Boolean(recordFixTarget(c.target)),
    run: c => {
      const { table, id } = c.target;
      store.set({ fixTarget: recordFixTarget(c.target), focus: { table, id, from: "panel" },
        issueFilter: { ...store.get().issueFilter, thisRecord: true } });
      runCore("fix_issue");
    } });
  // The focused record, from the palette only (R8: not in the feature / row menus; Details has the same buttons).
  add({ id: "edit_record", group: "Edits", title: "Edit record…", when: c => Boolean(c.focus),
    run: c => editRecord(c.focus.table, c.focus.id) });
  add({ id: "delete_record", group: "Edits", title: "Delete record…", when: c => Boolean(c.focus),
    run: c => deleteRecord(c.focus.table, c.focus.id) });
  add({ id: "undo_edit", group: "Edits", title: "Undo last edit", when: c => !undoBlocked(editSummary(c.state)),
    run: () => dispatch({ type: "undo_edit" }) });
  add({ id: "save_copy", group: "Edits", title: "Save a copy…", when: c => Boolean(c.network), run: () => openSaveDialog() });
}
