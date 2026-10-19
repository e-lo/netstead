// The project cards plugin's front end: a "Card" dock panel (compose a change, preview it, the draft card's changes,
// commit and import) and "New change ▸ …" commands. Everything goes through `wb` (see the cookbook page "Write a
// Workbench plugin"); the flow lives in controller.js and the rules in cardmodel.js, both tested under node.
// Card and user text is only ever set as text (`h`'s `text`, or a string child), never parsed as markup.
import {
  ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, commitFields, commitView, draftBadge, draftNet, draftOf, needsSelection,
  openPreview, previewLine,
} from "./cardmodel.js";
import { createController } from "./controller.js";

const PANEL = "project_cards.draft";

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

// A labelled control in core's form layout (`.sf-field`, as wb.schemaForm draws).
const field = (label, control) => h("div", { class: "sf-field" }, h("label", { for: control.id, text: label }), control);

const ctxNet = ctx => (ctx.network ? ctx.network.id : null);

export function activate(wb) {
  const ctl = createController(wb);
  let view = null;

  const panel = wb.registerPanel({
    workspace: "*",
    id: PANEL,
    title: "Card",
    order: 40,
    // On a preview, the draft of the base it stands for (draftOf follows the preview).
    badge: ctx => draftBadge(ctx.state, ctxNet(ctx)),
    render(el) {
      view = createView(wb, ctl, el);
      view.draw();
    },
    onShow: () => view && view.draw(),
  });

  async function compose(type) {
    if (!(await ctl.start(type))) return; // a transit type: the controller said "not yet supported"
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

  wb.store.subscribe(["plugins.project_cards", "active"], () => {
    panel.refreshBadge();
    if (view) view.draw();
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
  const server = () => wb.store.get();
  const baseNet = () => draftNet(server(), server().active); // the network whose draft the panel shows

  // The form is redrawn only for a new compose: redrawing on every state change would wipe what the user typed.
  let drawn = null;
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
    const run = step => guard(async () => {
      const errors = Object.values(forms).flatMap(f => f.errors());
      if (errors.length) { say(errors.join("; ")); return; }
      say(await step(values()));
      draw();
    });
    composeBox.append(
      h("button", { class: "mini", onclick: run(async v => `Previewing on ${(await ctl.preview(v)).preview}`) },
        "Preview"),
      " ",
      h("button", { class: "mini", onclick: run(async v => `Added: ${(await ctl.add(v)).describe}`) }, "Add to card"),
      " ",
      h("button", { class: "mini ghost", onclick: guard(async () => { await ctl.cancel(); say(""); draw(); }) },
        "Cancel"),
    );
    const first = Object.values(forms)[0];
    if (first) first.focus();
  }

  function drawPreview() {
    const preview = openPreview(server());
    previewBox.replaceChildren();
    if (!preview) return;
    previewBox.append(h("p", { text: previewLine(preview) }),
      h("button", { class: "mini ghost", onclick: guard(async () => { await ctl.closePreview(); say(""); }) },
        "Back to the network"));
  }

  function drawDraft() {
    draftBox.replaceChildren();
    const draft = draftOf(server(), server().active);
    if (!draft) {
      draftBox.append(h("p", { class: "empty",
        text: "No draft card for this network yet. Select links, then right-click: New change ▸ …" }));
      return;
    }
    const name = draft.project ? `: ${draft.project}` : "";
    draftBox.append(h("div", { class: "label", text: `Draft card${name} (${draft.count})` }));
    const list = h("ol");
    for (const change of draft.changes) list.append(h("li", { text: change.describe }));
    draftBox.append(list,
      h("button", { class: "mini", onclick: guard(async () => { await ctl.undo({ netId: baseNet() }); say("Undid the last change"); }) },
        "Undo last change"));
  }

  async function openDialog(fields) {
    const check = await ctl.check(fields);
    const v = commitView(check);
    const note = h("p", { role: "status" });
    const commit = h("button", { class: "mini", disabled: v.blocked || v.needsConfirm }, v.button);
    const confirm = h("input", { type: "checkbox", id: "pc-confirm" });
    confirm.onchange = () => { commit.disabled = v.blocked || !confirm.checked; };
    commit.onclick = async () => {
      try {
        const result = await ctl.commit(fields);
        dialog.close();
        say(`Committed ${result.card_id} to ${result.path}`);
      } catch (e) {
        note.textContent = e.message;
      }
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
      h("pre", { text: check.yaml, style: "max-height:45vh;overflow:auto;background:#0c0e12;padding:8px;"
        + "border:1px solid var(--edge);border-radius:6px;font-size:12px" }),
      note,
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" },
        h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel"), commit),
    );
    dialog.showModal();
    (v.needsConfirm && !v.blocked ? confirm : commit.disabled ? dialog.querySelector("button") : commit).focus();
  }

  // Drawn once: redrawing on every state change would wipe what the user is typing.
  function drawCommit() {
    const input = (id, placeholder) => h("input", { type: "text", id, placeholder });
    const project = input("pc-project", "Main Street road diet");
    const tags = input("pc-tags", "comma separated");
    const path = input("pc-path", "cards/main-street.yml");
    const overwrite = h("input", { type: "checkbox", id: "pc-overwrite" });
    const source = input("pc-import", "path/to/card.yml");
    const open = guard(async () => {
      const raw = { project: project.value, path: path.value, tags: tags.value, overwrite: overwrite.checked };
      const { fields, error } = commitFields(raw, baseNet());
      if (error) say(error);
      else await openDialog(fields);
    });
    commitBox.append(
      h("div", { class: "label", text: "Commit" }),
      field("Project name", project), field("Tags", tags), field("Card file", path),
      field("Replace an existing file", overwrite),
      h("button", { class: "mini", onclick: open }, "Commit…"),
      h("div", { class: "label", text: "Import a card" }),
      field("Card or edit log", source),
      h("button", { class: "mini", onclick: guard(async () => {
        say(`Imported ${(await ctl.importCard(source.value.trim())).imported} change(s)`);
      }) }, "Import"),
    );
  }

  function draw() {
    drawCompose();
    drawPreview();
    drawDraft();
  }

  drawCommit();
  return { draw };
}
