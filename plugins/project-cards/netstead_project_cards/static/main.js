// The project cards plugin's front end: the "Cards" dock panel (compose a change, preview it, the draft card's
// changes, commit and import; its id is the bare `cards` core reserves for this plugin) and "New change ▸ …" commands. Everything goes through `wb` (see the cookbook page "Write a
// Workbench plugin"); the flow lives in controller.js and the rules in cardmodel.js, both tested under node.
// Card and user text is only ever set as text (`h`'s `text`, or a string child), never parsed as markup.
import {
  ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, commitFields, commitView, draftBadge, draftNet, draftOf, hasNode,
  importPath, needsSelection, openPreview, previewLine,
} from "./cardmodel.js";
import { createController } from "./controller.js";

const PANEL = "cards";

// A DOM element. `text` is its text content, `on<event>` a handler, `disabled` / `checked` properties, the rest
// attributes; string children become text nodes.
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el[key] = value;
    else if (key === "disabled" || key === "checked") el[key] = Boolean(value);
    else el.setAttribute(key, value);
  }
  el.append(...children);
  return el;
}

// Runs `fn` with `buttons` disabled, so a second click can't start it again; they are re-enabled after.
async function busy(buttons, fn) {
  const was = buttons.map(b => b.disabled);
  for (const b of buttons) b.disabled = true;
  try { return await fn(); } finally { buttons.forEach((b, i) => { b.disabled = was[i]; }); }
}

// Fills `box` with `nodes`. If focus was in the box (or `force`), it moves to the new button, else to the heading
// (tabindex -1), so a redraw never drops keyboard focus. Returns true when the box has neither to take it.
function redraw(box, nodes, force = false) {
  const had = force || box.contains(document.activeElement);
  box.replaceChildren(...nodes);
  if (!had) return false;
  const target = nodes.find(n => n.tagName === "BUTTON") || nodes.find(n => n.getAttribute("tabindex") === "-1");
  if (target) target.focus();
  return !target;
}

// A labelled control in core's form layout (`.sf-field`, as wb.schemaForm draws).
const field = (label, control) => h("div", { class: "sf-field" }, h("label", { for: control.id, text: label }), control);

const ctxNet = ctx => (ctx.network ? ctx.network.id : null);

export function activate(wb) {
  const ctl = createController(wb);
  let view = null;

  wb.registerPanel({
    workspace: "*",
    id: PANEL,
    title: "Cards",
    order: 25,
    // On a preview, the draft of the base it stands for (draftOf follows the preview).
    badge: ctx => draftBadge(ctx.state, ctxNet(ctx)),
    render(el) {
      view = createView(wb, ctl, el);
      view.draw();
    },
    onShow: () => view && view.draw(),
  });

  async function compose(type) {
    if (!(await ctl.start(type))) return; // a transit type: the controller said "not yet supported"; any form stays
    wb.showPanel(PANEL); // the first show renders the panel, which draws the form
    if (view) view.draw();
  }

  for (const type of ROADWAY_TYPES) {
    wb.registerCommand({
      id: `project_cards.new_${type}`,
      title: TYPE_TITLES[type],
      group: "New change",
      // On the right-click and "Selection actions" menus only while there is a selection to change.
      contexts: needsSelection(type) ? ["selection", "palette"] : ["palette"],
      when: ctx => !needsSelection(type) || ctx.selectionCount > 0,
      run: () => compose(type),
    });
  }
  for (const type of TRANSIT_TYPES) {
    wb.registerCommand({
      id: `project_cards.new_${type}`,
      title: `${TYPE_TITLES[type]} (not yet supported)`,
      group: "New change",
      contexts: ["palette"],
      run: () => compose(type),
    });
  }
  wb.registerCommand({
    id: "project_cards.undo",
    title: "Undo the draft card's last change",
    group: "Card",
    contexts: ["palette"],
    when: ctx => Boolean(draftOf(ctx.state, ctxNet(ctx))),
    run: ctx => ctl.undo({ netId: draftNet(ctx.state, ctxNet(ctx)) }),
  });

  // Core redraws the badge on every server change; the panel follows the draft and the preview here.
  wb.store.subscribe(["plugins.project_cards", "active"], () => {
    const stale = ctl.serverChanged();
    if (!view) return;
    view.draw();
    if (stale) view.checkWentStale();
  });
  // A form read from the shared selection is outdated once it changes. Watched as the server's `selection`, not
  // wb.selection: that follows the active network, which opening (or closing) a preview changes.
  wb.store.subscribe(["selection"], () => {
    if (ctl.selectionChanged() && view) view.composeWentStale();
  });
}

function createView(wb, ctl, el) {
  const composeBox = h("section");
  const previewBox = h("section");
  const draftBox = h("section");
  const commitBox = h("section");
  const status = h("p", { class: "muted", role: "status" });
  const dialog = h("dialog", { "aria-labelledby": "pc-dialog-title" });
  // A native <dialog> (so its own closed state hides it), drawn like core's modal cards.
  dialog.style.cssText = "background:var(--panel);color:var(--ink);border:1px solid var(--edge);border-radius:12px;"
    + "padding:16px;width:min(760px, calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto";
  el.append(composeBox, previewBox, draftBox, commitBox, status, dialog);

  const say = text => { status.textContent = text; };
  const guard = fn => async () => {
    try { await fn(); } catch (e) { say(e.message); }
  };
  // A button that runs `fn`, disabled while it does.
  const act = (cls, label, fn) => {
    const b = h("button", { class: cls }, label);
    b.onclick = guard(() => busy([b], fn));
    return b;
  };
  const server = () => wb.store.get();
  const baseNet = () => draftNet(server(), server().active); // the network whose draft the panel shows

  // The form is redrawn only for a new compose: redrawing on every state change would wipe what the user typed.
  let drawn = null;
  // Set while a compose form is drawn: shows that the selection it was read from changed.
  let markComposeOutdated = () => {};
  function drawCompose() {
    const c = ctl.state.compose;
    if (c === drawn) return;
    drawn = c;
    composeBox.replaceChildren();
    if (!c) return;
    composeBox.append(h("div", { class: "label", text: `New change: ${TYPE_TITLES[c.type]}` }));
    if (c.form.targets) {
      composeBox.append(h("p", { class: "muted", text: c.form.targets.label }));
      for (const w of c.form.targets.warnings || []) composeBox.append(h("p", { class: "muted", text: w }));
    }
    const forms = {};
    for (const key of ["columns", "link", "node", "options"]) {
      if (!c.form[key]) continue;
      if (key === "node") {
        const next = c.form.next_ids && c.form.next_ids.node_id;
        composeBox.append(h("p", { class: "muted", text: `New end node (optional; next free id: ${next})` }));
      }
      const slot = h("div");
      composeBox.append(slot);
      const start = key === "link" && c.form.next_ids ? { link_id: c.form.next_ids.link_id } : {};
      forms[key] = wb.schemaForm(slot, c.form[key], start);
    }
    const values = () => Object.fromEntries(Object.entries(forms).map(([k, f]) => [k, f.value()]));
    // The node form is optional: left without an id, it adds no node (cardmodel's addition), so its required
    // fields are not asked for.
    const checked = () => Object.entries(forms).filter(([k, f]) => k !== "node" || hasNode(f.value()));
    const row = [h("button", { class: "mini" }, "Preview"), h("button", { class: "mini" }, "Add to card"),
      h("button", { class: "mini ghost" }, "Cancel")];
    const [previewButton, addButton, cancelButton] = row;
    const reload = h("button", { class: "mini" }, "Reload form");
    reload.hidden = true;
    const outdatedNote = h("p", { class: "muted", role: "status" });
    // An outdated form can't be previewed or added (busy() re-enables the buttons after a step: this runs after it).
    const sync = () => { if (c.outdated) previewButton.disabled = addButton.disabled = true; };
    const run = step => guard(async () => {
      try {
        await busy(row, async () => {
          const errors = checked().flatMap(([, f]) => f.errors());
          if (errors.length) { say(errors.join("; ")); return; }
          say(await step(values()));
          draw();
        });
      } finally { sync(); }
    });
    previewButton.onclick = run(async v => `Previewing on ${(await ctl.preview(v)).preview}`);
    addButton.onclick = run(async v => `Added: ${(await ctl.add(v)).describe}`);
    cancelButton.onclick = guard(() => busy(row, async () => { await ctl.cancel(); say(""); draw(); }));
    reload.onclick = guard(() => busy([reload], async () => { await ctl.reload(); say(""); draw(); }));
    composeBox.append(outdatedNote, previewButton, " ", addButton, " ", cancelButton, " ", reload);
    markComposeOutdated = () => {
      const had = composeBox.contains(document.activeElement);
      sync();
      outdatedNote.textContent = "The selection changed since this form opened: reload the form to change it.";
      reload.hidden = false;
      if (had) reload.focus(); // Preview or Add (perhaps focused) is now disabled
    };
    if (c.outdated) markComposeOutdated();
    const first = Object.values(forms)[0];
    if (first) first.focus();
  }

  // Returns true when focus was in the box and the preview has gone: the draft box takes it.
  function drawPreview() {
    const preview = openPreview(server());
    return redraw(previewBox, preview ? [h("p", { text: previewLine(preview) }),
      act("mini ghost", "Back to the network", async () => { await ctl.closePreview(); say(""); })] : []);
  }

  function drawDraft(takeFocus) {
    const draft = draftOf(server(), server().active);
    if (!draft) {
      redraw(draftBox, [h("p", { class: "empty", tabindex: -1,
        text: "No draft card for this network yet. Select links, then right-click: New change ▸ …" })], takeFocus);
      return;
    }
    const name = draft.project ? `: ${draft.project}` : "";
    const list = h("ol");
    for (const change of draft.changes) list.append(h("li", { text: change.describe }));
    const undo = act("mini", "Undo last change", async () => {
      await ctl.undo({ netId: baseNet() });
      say("Undid the last change");
    });
    const heading = h("div", { class: "label", tabindex: -1, text: `Draft card${name} (${draft.count})` });
    redraw(draftBox, [heading, list, undo], takeFocus);
  }

  // Set while the commit dialog is drawn: marks its check outdated (the draft changed elsewhere).
  let markOutdated = () => {};

  async function openDialog(fields) {
    const check = await ctl.check(fields);
    const v = commitView(check);
    const note = h("p", { role: "status" });
    const commit = h("button", { class: "mini" }, v.button);
    const cancel = h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel");
    const recheck = h("button", { class: "mini" }, "Check again");
    recheck.hidden = true;
    const confirm = h("input", { type: "checkbox", id: "pc-confirm" });
    let outdated = false;
    const sync = () => { commit.disabled = outdated || v.blocked || (v.needsConfirm && !confirm.checked); };
    sync();
    confirm.onchange = sync;
    commit.onclick = async () => {
      try {
        const result = await busy([commit, cancel], () => ctl.commit(fields));
        dialog.close();
        say(`Committed ${result.card_id} to ${result.path}`);
      } catch (e) {
        note.textContent = e.message;
      } finally {
        sync();
      }
    };
    recheck.onclick = guard(() => busy([recheck], () => openDialog(fields)));
    markOutdated = () => {
      outdated = true;
      sync();
      note.textContent = "The draft changed since this check: check again before committing.";
      recheck.hidden = false;
      recheck.focus(); // the dialog is modal, and Commit (perhaps focused) is now disabled
    };
    dialog.replaceChildren(
      h("h3", { id: "pc-dialog-title", text: `Commit "${fields.project}"` }),
      h("p", { class: v.lines.length ? "" : "muted", text: v.summary }),
      h("ul", {}, ...v.lines.map(line => h("li", { text: line }))),
      ...(v.needsConfirm && !v.blocked
        ? [h("p", {}, confirm, " ", h("label", { for: "pc-confirm", text: "I have read the warnings: commit anyway" }))]
        : []),
      h("p", { text: v.problems }),
      h("div", { class: "label", text: `Card YAML (${check.net_id})` }),
      h("pre", { text: check.yaml, style: "max-height:45vh;overflow:auto;background:var(--bg)" }), // core's <pre> look
      note,
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" }, cancel, recheck, commit),
    );
    if (!dialog.open) dialog.showModal(); // "Check again" redraws an open dialog
    (v.needsConfirm && !v.blocked ? confirm : commit.disabled ? cancel : commit).focus();
  }

  // Drawn once: redrawing on every state change would wipe what the user is typing.
  function drawCommit() {
    const input = (id, placeholder) => h("input", { type: "text", id, placeholder });
    const project = input("pc-project", "Main Street road diet");
    const tags = input("pc-tags", "comma separated");
    const path = input("pc-path", "cards/main-street.yml");
    const overwrite = h("input", { type: "checkbox", id: "pc-overwrite" });
    const source = input("pc-import", "path/to/card.yml");
    const open = async () => {
      const raw = { project: project.value, path: path.value, tags: tags.value, overwrite: overwrite.checked };
      const { fields, error } = commitFields(raw, baseNet());
      if (error) say(error);
      else await openDialog(fields);
    };
    const importIt = async () => {
      const { path: from, error } = importPath(source.value);
      if (error) say(error);
      else say(`Imported ${(await ctl.importCard(from)).imported} change(s)`);
    };
    commitBox.append(
      h("div", { class: "label", text: "Commit" }),
      field("Project name", project), field("Tags", tags), field("Card file", path),
      field("Replace an existing file", overwrite),
      act("mini", "Commit…", open),
      h("div", { class: "label", text: "Import a card" }),
      field("Card or edit log", source),
      act("mini", "Import", importIt),
    );
  }

  function draw() {
    drawCompose();
    drawDraft(drawPreview());
  }

  // The controller dropped the dialog's check: Commit stays disabled until the user checks again.
  const checkWentStale = () => { if (dialog.open) markOutdated(); };
  // The controller marked the open form outdated: Preview and Add stay disabled until the user reloads it.
  const composeWentStale = () => markComposeOutdated();

  drawCommit();
  return { draw, checkWentStale, composeWentStale };
}
