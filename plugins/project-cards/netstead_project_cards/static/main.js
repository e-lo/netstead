// The project cards plugin's front end: the "Cards" dock panel (its id is the bare `cards` core reserves for this
// plugin) and the "New change ▸ …" commands. The panel has four sections: the catalog (a folder of cards, each with
// its applicability badge, previewed or applied), the draft card (compose a change, preview it, the draft's changes,
// commit and import), the cards applied this session, and the session summary (copied or downloaded). Everything
// goes through `wb` (see the cookbook page "Write a Workbench plugin"); the flow lives in controller.js and the rules
// in cardmodel.js, both tested under node. Card and user text is only ever set as text (`h`'s `text`, or a string
// child), never parsed as markup.
import {
  ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, appliedRows, applyDefaults, catalogOf, catalogRows, commitFields,
  commitView, draftBadge, draftNet, draftOf, hasNode, importPath, needsSelection, openPreview, previewLine,
  savedChoices, selectionStatus, stepIndicator, summaryFileName, typeChoices,
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

// A labeled control in core's form layout (`.sf-field`, as wb.schemaForm draws).
const field = (label, control) => h("div", { class: "sf-field" }, h("label", { for: control.id, text: label }), control);

const ctxNet = ctx => (ctx.network ? ctx.network.id : null);

export function activate(wb) {
  const ctl = createController(wb);
  let view = null;

  wb.registerPanel({
    workspace: "*",
    id: PANEL,
    title: "Cards",
    icon: "cards",
    order: 25,
    // On a preview, the draft of the base it stands for (draftOf follows the preview).
    badge: ctx => draftBadge(ctx.state, ctxNet(ctx)),
    render(el) {
      view = createView(wb, ctl, el);
      view.draw();
      view.loadCatalog(); // the badges of a catalog opened before the panel first showed (the catalog_dir setting)
    },
    onShow: () => view && view.draw(),
  });

  async function compose(type) {
    ctl.endCreate(); // the right-click form replaces the New card mode's steps
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

  // The links a previewed card aims at are highlighted on the map (the server publishes them with the preview) and
  // cleared once the preview closes, whoever closed it (the panel, the palette, the assistant).
  let cardShown = null;
  const highlight = ids => { if (typeof wb.highlight === "function") wb.highlight(ids || {}); };
  wb.on("card_selection", payload => {
    cardShown = payload.card_id;
    highlight({ links: payload.links || [], nodes: payload.nodes || [] });
  });

  // Core redraws the badge on every server change; the panel follows the draft, the preview and the catalog here.
  wb.store.subscribe(["plugins.project_cards", "active"], state => {
    const stale = ctl.serverChanged();
    const catalogMoved = ctl.catalogChanged();
    if (cardShown && !openPreview(state)) {
      cardShown = null;
      highlight();
    }
    if (!view) return;
    view.draw();
    if (stale) view.checkWentStale();
    if (catalogMoved) view.loadCatalog(); // opened, refreshed, or a card's check dropped: fill the badges again
  });
  // The server says when the catalog was (re)scanned or a card applied: the listing is read again either way.
  for (const event of ["catalog", "applied"]) wb.on(event, () => view && view.loadCatalog());
  // A form read from the shared selection is outdated once it changes. Watched as the server's `selection`, not
  // wb.selection: that follows the active network, which opening (or closing) a preview changes.
  wb.store.subscribe(["selection", "saved_selections"], () => {
    if (ctl.selectionChanged() && view) view.composeWentStale();
    if (view) view.drawCreate(); // the New card mode's live count
  });
}

function createView(wb, ctl, el) {
  // Core's SVG icon in `button` (Host API 1.7's wb.icon), else the text glyph an older host still shows.
  const glyph = (button, name, fallback) => {
    const svg = typeof wb.icon === "function" ? wb.icon(name) : "";
    if (svg) button.innerHTML = svg; else button.textContent = fallback;
    return button;
  };
  const catalogBox = h("section", { id: "pc-catalog" });
  const previewBox = h("section", { id: "pc-preview" });
  const createBox = h("section", { id: "pc-create" });
  const composeBox = h("section", { id: "pc-compose" });
  const draftBox = h("section", { id: "pc-draft" });
  const commitBox = h("section", { id: "pc-commit" });
  const appliedBox = h("section", { id: "pc-applied" });
  const summaryBox = h("section", { id: "pc-summary" });
  const status = h("p", { class: "muted", role: "status", id: "pc-status" });
  const dialog = h("dialog", { "aria-labelledby": "pc-dialog-title" });
  // A native <dialog> (so its own closed state hides it), drawn like core's modal cards.
  dialog.style.cssText = "background:var(--panel);color:var(--ink);border:1px solid var(--edge);border-radius:12px;"
    + "padding:16px;width:min(760px, calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto";
  const heading = text => h("h3", { class: "label", style: "font-size:12px;margin:18px 0 6px", text });
  el.append(
    heading("Catalog"), catalogBox, previewBox,
    heading("Draft"), createBox, composeBox, draftBox, commitBox,
    heading("Applied this session"), appliedBox,
    heading("Session summary"), summaryBox,
    status, dialog,
  );

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

  // The New card mode: the step indicator, then the step's own controls (the form itself is the compose box's).
  // The description input and the saved-selection list are made once, so a redraw keeps what the user typed.
  const utterance = h("input", { type: "text", id: "pc-utterance", placeholder: "Main Street from 1st to 5th" });
  const savedPick = h("select", { id: "pc-saved" });
  const step = (cls, label, fn) => act(cls, label, async () => { await fn(); draw(); });
  function selectStep() {
    const s = server();
    const status = selectionStatus(s, s.active);
    const use = step("mini", `Use these ${status.count} link${status.count === 1 ? "" : "s"}`, () => ctl.useFacility());
    use.disabled = !status.ready;
    utterance.onkeydown = e => { if (e.key === "Enter") describe.onclick(); };
    const describe = act("mini", "Select", async () => {
      await ctl.selectByText(utterance.value);
      say(`Selected: ${utterance.value.trim()}`);
    });
    const choices = savedChoices(s, s.active);
    const picked = savedPick.value;
    savedPick.replaceChildren(...choices.map(c => h("option", { value: c.name, text: c.label })));
    if (choices.some(c => c.name === picked)) savedPick.value = picked;
    const useSaved = step("mini", "Use saved", () => ctl.useFacility({ saved: savedPick.value }));
    const nodes = [
      h("p", { id: "pc-select-count", role: "status", text: status.label }), use,
      h("p", { class: "muted", text: "Select the facility: turn on Highlight links, click links or shift-drag a box "
        + "over them and press Set as selection (Find tab), or describe them:" }),
      h("div", { style: "display:flex;gap:6px;align-items:center" }, utterance, describe),
    ];
    if (choices.length) nodes.push(field("Or a saved selection", savedPick), useSaved);
    return nodes;
  }
  function drawCreate() {
    const mode = ctl.state.create;
    if (!mode) {
      redraw(createBox, [step("mini", "New card", () => ctl.beginCreate()),
        h("span", { class: "muted", text: " a guided change: type, facility, details, add." })]);
      return;
    }
    const steps = h("ol", { class: "pc-steps", "aria-label": "New card steps",
      style: "display:flex;gap:10px;list-style:none;padding:0;margin:4px 0" },
    ...stepIndicator(mode).map(st => h("li", {
      "data-step": st.key, "data-state": st.state, ...(st.state === "current" ? { "aria-current": "step" } : {}),
      style: st.state === "current" ? "font-weight:700" : st.state === "todo" ? "" : "opacity:.6",
      text: `${st.state === "done" ? "✓ " : ""}${st.label}${st.state === "skipped" ? " (not needed)" : ""}`,
    })));
    const nodes = [h("div", { class: "label", text: "New card" }), steps];
    if (mode.step === "type") {
      for (const c of typeChoices()) {
        const b = step("mini", c.enabled ? c.title : `${c.title} (${c.note})`, () => ctl.chooseCreateType(c.type));
        b.disabled = !c.enabled;
        nodes.push(b, " ");
      }
    } else if (mode.step === "select") {
      nodes.push(...selectStep());
    } else if (mode.step === "added") {
      nodes.push(h("p", { text: "Added to the draft card. Commit it below, or add another change." }),
        step("mini", "Add another change", () => ctl.anotherChange()), " ",
        step("mini ghost", "Done", () => ctl.endCreate()));
    } else {
      nodes.push(h("p", { class: "muted", text: mode.step === "add"
        ? "The preview is on the map: Add to card when it looks right."
        : "Fill in the change below, Preview it, then Add to card." }));
    }
    if (mode.step !== "added") {
      nodes.push(h("div", { style: "margin-top:6px" },
        ...(mode.step === "type" ? [] : [step("mini ghost", "Back", () => ctl.backCreate()), " "]),
        step("mini ghost", "Cancel", async () => { await ctl.cancel(); say(""); })));
    }
    const typing = [utterance, savedPick].find(el => el === document.activeElement);
    if (!typing) { redraw(createBox, nodes); return; }
    createBox.replaceChildren(...nodes); // a redraw while typing (the live count moved) keeps the caret there
    if (createBox.contains(typing)) typing.focus();
  }

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
        text: "No draft card for this network yet: start one with New card, or select links and right-click: "
          + "New change ▸ …" })], takeFocus);
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

  // ------------------------------------------------------------------------------------------------ the catalog

  // The folder controls are drawn once (a redraw would wipe the path being typed); the listing is redrawn.
  const catalogPath = h("input", { type: "text", id: "pc-catalog-path", placeholder: "folder of cards" });
  const catalogLine = h("p", { class: "muted", id: "pc-catalog-line" });
  const catalogList = h("div", { id: "pc-catalog-list" });
  function drawCatalogControls() {
    const open = act("mini", "Open", async () => {
      const path = catalogPath.value.trim();
      if (!path) { say("Give a folder of cards"); return; }
      const result = await ctl.openCatalog(path);
      say(`Opened ${result.path}: ${result.cards} card(s)`);
    });
    const refresh = glyph(h("button", { class: "iconbtn", "data-tip": "Rescan the folder",
      "aria-label": "Rescan the folder" }), "refresh", "↻");
    refresh.onclick = guard(() => busy([refresh], async () => {
      const result = await ctl.refreshCatalog();
      say(`Rescanned ${result.path}: ${result.cards} card(s)`);
    }));
    catalogPath.onkeydown = e => { if (e.key === "Enter") open.onclick(); };
    catalogBox.append(
      h("div", { style: "display:flex;gap:6px;align-items:center" }, catalogPath, open, refresh),
      catalogLine, catalogList,
    );
  }

  // A listing is read when one is open (the route checks unchecked cards, so the `?` badges fill in).
  let loading = null;
  function loadCatalog() {
    if (loading) return loading;
    loading = (async () => {
      if (catalogOf(server())) {
        try { await ctl.loadCatalog(baseNet()); } catch (e) { say(e.message); }
      }
      drawCatalog();
    })().finally(() => { loading = null; });
    return loading;
  }

  // The applicability rows a card's Details expand to.
  function detailRows(row) {
    const a = row.applicability;
    if (!a) return [h("p", { class: "muted", text: "Not checked yet." })];
    const list = h("ul");
    for (const c of a.changes || []) {
      const what = c.reason ? c.reason : c.missing && c.missing.length ? `missing ${c.missing.join(", ")}` : "";
      list.append(h("li", { text: `${c.describe}: ${c.resolved} resolved${what ? `; ${what}` : ""}` }));
    }
    return [h("p", { class: "muted", text: `Checked on ${a.net_id}` }), list];
  }

  function cardRow(row) {
    const tags = row.tags.length ? ` · ${row.tags.join(", ")}` : "";
    const badge = h("span", {
      class: "pc-badge", title: row.tip, "aria-label": row.tip, "data-badge": row.badge,
      style: "display:inline-block;width:1.4em;font-weight:700", text: row.badge,
    });
    const preview = act("mini", "Preview", async () => {
      const result = await ctl.previewCard(row.card_id, baseNet());
      say(`Previewing ${row.card_id} on ${result.preview}`);
    });
    const apply = act("mini", "Apply…", () => openApplyDialog(row));
    const details = h("details", {}, h("summary", { class: "muted", text: "Details" }), ...detailRows(row));
    return h("div", { class: "pc-card", "data-card": row.card_id, style: "margin:6px 0 8px" },
      h("div", {}, badge, " ", h("strong", { text: row.project }),
        h("span", { class: "muted", text: ` · ${row.changes} change(s)${tags}` })),
      h("div", { style: "margin:4px 0 0 1.4em" }, preview, " ", apply),
      details,
    );
  }

  function drawCatalog() {
    const listing = ctl.catalog();
    if (!listing) {
      catalogLine.textContent = "";
      redraw(catalogList, [h("p", { class: "empty", tabindex: -1, text: "No catalog open: open a folder of cards." })]);
      return;
    }
    catalogLine.textContent = listing.path;
    const rows = catalogRows(server(), listing);
    const nodes = rows.length ? rows.map(cardRow) : [h("p", { class: "empty", tabindex: -1, text: "No cards in this folder." })];
    const rejected = listing.rejected || [];
    if (rejected.length) {
      nodes.push(h("details", {}, h("summary", { class: "muted", text: `Not cards (${rejected.length})` }),
        h("ul", {}, ...rejected.map(r => h("li", { class: "muted", text: `${r.card_id}: ${r.error}` })))));
    }
    redraw(catalogList, nodes);
  }

  // The options apply_card takes, then the card goes to the network (all or nothing); a refusal shows in the dialog.
  function openApplyDialog(row) {
    const defaults = applyDefaults(row);
    const conflict = h("select", { id: "pc-conflict" },
      ...["error", "warn", "skip"].map(v => h("option", { value: v, text: v })));
    conflict.value = defaults.existing_value_conflict;
    const ignore = h("input", { type: "checkbox", id: "pc-ignore", checked: defaults.ignore_missing });
    const note = h("p", { role: "status" });
    const apply = h("button", { class: "mini" }, "Apply");
    const cancel = h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel");
    apply.onclick = async () => {
      try {
        const options = { existing_value_conflict: conflict.value, ignore_missing: ignore.checked };
        const result = await busy([apply, cancel], () => ctl.applyCard(row.card_id, options, baseNet()));
        dialog.close();
        say(`Applied ${result.card_id}: ${result.changes} change(s) on ${result.net_id}`);
        draw();
      } catch (e) {
        note.textContent = e.message;
      }
    };
    dialog.replaceChildren(
      h("h3", { id: "pc-dialog-title", text: `Apply "${row.project}"` }),
      h("p", { class: "muted", text: `${row.badge} ${row.tip}` }),
      field("When a property's current value differs from the card's", conflict),
      field("Skip ids the network does not have", ignore),
      note,
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" }, cancel, apply),
    );
    if (!dialog.open) dialog.showModal();
    apply.focus();
  }

  // ------------------------------------------------------------------------------- applied, and the summary

  function drawApplied() {
    const rows = appliedRows(server());
    if (!rows.length) {
      redraw(appliedBox, [h("p", { class: "empty", tabindex: -1, text: "No cards applied this session yet." })]);
      return;
    }
    const list = h("ul", { id: "pc-applied-list" });
    for (const r of rows) {
      list.append(h("li", { "data-card": r.card_id },
        h("strong", { text: r.project }), " ",
        h("span", { class: "muted", "data-source": r.source, text: `[${r.source}]` }), " ",
        h("span", { class: "muted", text: `${r.changes} change(s) on ${r.net_id} at ${r.time}` })));
    }
    redraw(appliedBox, [list]);
  }

  function drawSummary() {
    const iconButton = (tip, name, fallback, fn) => {
      const b = glyph(h("button", { class: "iconbtn", "data-tip": tip, "aria-label": tip }), name, fallback);
      b.onclick = guard(() => busy([b], fn));
      return b;
    };
    const copy = iconButton("Copy the session summary", "copy", "⎘", async () => {
      const text = await ctl.summary(baseNet());
      const clipboard = globalThis.navigator && globalThis.navigator.clipboard;
      if (!clipboard) throw new Error("The clipboard is not available here: download the summary instead");
      await clipboard.writeText(text);
      say("Copied the session summary");
    });
    const download = iconButton("Download the session summary", "download", "⤓", async () => {
      const text = await ctl.summary(baseNet());
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      const a = h("a", { href: url, download: summaryFileName(baseNet()) });
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      say(`Downloaded ${summaryFileName(baseNet())}`);
    });
    summaryBox.append(h("div", { style: "display:flex;gap:6px;align-items:center" },
      h("span", { class: "muted", text: "The cards applied, the draft and this session's card actions, as text." },
      ), copy, download));
  }

  function draw() {
    drawCatalog();
    drawCreate();
    drawCompose();
    drawDraft(drawPreview());
    drawApplied();
  }

  // The controller dropped the dialog's check: Commit stays disabled until the user checks again.
  const checkWentStale = () => { if (dialog.open) markOutdated(); };
  // The controller marked the open form outdated: Preview and Add stay disabled until the user reloads it.
  const composeWentStale = () => markComposeOutdated();

  drawCatalogControls();
  drawCommit();
  drawSummary();
  return { draw, drawCreate, loadCatalog, checkWentStale, composeWentStale };
}
