// The project cards plugin's front end: the "Cards" dock panel (its id is the bare `cards` core reserves for this
// plugin) and the "New change ▸ …" commands. The panel has four sections: the catalog (a folder of cards, each with
// its applicability badge, previewed or applied), the draft card (compose a change, preview it, the draft's changes,
// commit and import), this session's card actions (saved, applied, catalog files), and the session summary (copied
// or downloaded). Everything
// goes through `wb` (see the cookbook page "Write a Workbench plugin"); the flow lives in controller.js and the rules
// in cardmodel.js, both tested under node. Card and user text is only ever set as text (`h`'s `text`, or a string
// child), never parsed as markup.
import {
  ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, applyDefaults, applyEnabled, catalogOf, catalogRows, commitChoices,
  commitFields, commitView, draftBadge, editPrompt, fileLine, metaFields, metaForm, renameName, draftNet, draftOf, hasNode, importPath, inspectHeader, inspectRows, needsSelection,
  openPreview, previewLine, savedChoices, selectionStatus, sessionRows, stepIndicator, summaryFileName, typeChoices,
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

// Every button in `nodes` and their descendants, in document order.
const buttonsIn = nodes => nodes.flatMap(n => (n.tagName === "BUTTON" ? [n] : n.children ? buttonsIn([...n.children]) : []));

// Fills `box` with `nodes`. If focus was in the box (or `force`), it moves to the new button with the same name as the
// focused one, else the first button, else the heading (tabindex -1), so a redraw never drops keyboard focus. Returns
// true when the box has none of them to take it.
function redraw(box, nodes, force = false) {
  const was = document.activeElement;
  const had = force || box.contains(was);
  const name = had && was && was.getAttribute ? was.getAttribute("aria-label") : null;
  box.replaceChildren(...nodes);
  if (!had) return false;
  const buttons = buttonsIn(nodes);
  const target = (name && buttons.find(b => b.getAttribute("aria-label") === name)) || buttons[0]
    || nodes.find(n => n.getAttribute && n.getAttribute("tabindex") === "-1");
  if (target) target.focus();
  return !target;
}

// A labeled control in core's form layout (`.sf-field`, as wb.schemaForm draws).
const field = (label, control) => h("div", { class: "sf-field" }, h("label", { for: control.id, text: label }), control);

const ctxNet = ctx => (ctx.network ? ctx.network.id : null);

// The icons this panel needs that core has no name for, drawn in core's style (24×24 strokes in currentColor).
const LOCAL_ICONS = {
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
    + '<path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  rename: '<path d="M4 7V5h9v2"/><line x1="8.5" y1="5" x2="8.5" y2="19"/><line x1="6.5" y1="19" x2="10.5" y2="19"/>'
    + '<line x1="17" y1="4" x2="17" y2="20"/><line x1="15" y1="4" x2="19" y2="4"/><line x1="15" y1="20" x2="19" y2="20"/>',
  tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/>'
    + '<circle cx="7.5" cy="7.5" r="1.5"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/>'
    + '<line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="10" x2="9" y2="20"/>',
};
const svgOf = body => '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" '
  + `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
// A row of icon buttons, kept compact (a card's actions, a section's controls).
const actionRow = (...buttons) => h("div", { class: "pc-actions",
  style: "display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin:4px 0" }, ...buttons);

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
    view.draftMoved();
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
  // Whether the host draws icons (Host API 1.7's wb.icon): an older host gets every icon button's text instead.
  const hostIcons = (() => {
    try { return typeof wb.icon === "function" && Boolean(wb.icon("x")); } catch (e) { return false; }
  })();
  // `name`'s SVG in `button`: core's icon, or one of this panel's own (LOCAL_ICONS); else the text `fallback`.
  const glyph = (button, name, fallback) => {
    let svg = "";
    if (hostIcons) {
      if (Object.hasOwn(LOCAL_ICONS, name)) svg = svgOf(LOCAL_ICONS[name]);
      else try { svg = wb.icon(name); } catch (e) { svg = ""; } // a name this host does not have: the text
    }
    if (svg) button.innerHTML = svg; else button.textContent = fallback;
    return button;
  };
  const catalogBox = h("section", { id: "pc-catalog" });
  const previewBox = h("section", { id: "pc-preview" });
  const inspectBox = h("section", { id: "pc-inspect", "aria-label": "Inspect a card" });
  const createBox = h("section", { id: "pc-create" });
  const composeBox = h("section", { id: "pc-compose" });
  const draftBox = h("section", { id: "pc-draft" });
  const commitBox = h("section", { id: "pc-commit" });
  const sessionBox = h("section", { id: "pc-session" });
  const summaryBox = h("section", { id: "pc-summary" });
  const status = h("p", { class: "muted", role: "status", id: "pc-status" });
  const dialog = h("dialog", { "aria-labelledby": "pc-dialog-title" });
  // A native <dialog> (so its own closed state hides it), drawn like core's modal cards.
  dialog.style.cssText = "background:var(--panel);color:var(--ink);border:1px solid var(--edge);border-radius:12px;"
    + "padding:16px;width:min(760px, calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto";
  const heading = text => h("h3", { class: "label", style: "font-size:12px;margin:18px 0 6px", text });
  el.style.overflowWrap = "anywhere"; // long file paths wrap instead of scrolling the dock sideways
  el.append(
    heading("Catalog"), catalogBox, previewBox, inspectBox,
    heading("Draft"), createBox, composeBox, draftBox, commitBox,
    heading("This session"), sessionBox,
    heading("Session summary"), summaryBox,
    status, dialog,
  );

  const say = text => { status.textContent = text; };
  // A status line with one button after it (say, "Undo delete"); the next say() replaces both.
  const sayWith = (text, label, fn) => {
    const b = h("button", { class: "mini ghost", id: "pc-status-action" }, label);
    b.onclick = guard(() => busy([b], fn));
    status.replaceChildren(text, " ", b);
  };
  const guard = fn => async () => {
    try { await fn(); } catch (e) { say(e.message); }
  };
  // A button that runs `fn`, disabled while it does.
  const act = (cls, label, fn) => {
    const b = h("button", { class: cls }, label);
    b.onclick = guard(() => busy([b], fn));
    return b;
  };
  // An icon button (core's `iconbtn`) that runs `fn`: `tip` is its tooltip, and its text where the host draws no
  // icons; `label` its accessible name (the tip and the card it acts on, say).
  const iconAct = (name, tip, fn, label = tip) => {
    const b = glyph(h("button", { class: "iconbtn", "data-tip": tip, "aria-label": label }), name, tip);
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
      const begin = iconAct("plus", "New card", async () => { await ctl.beginCreate(); draw(); },
        "New card: a guided change");
      redraw(createBox, [h("div", { style: "display:flex;gap:6px;align-items:center" }, begin,
        h("span", { class: "muted", text: "New card: a guided change (type, facility, details, add)." }))]);
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
    return redraw(previewBox, preview ? [h("div", { style: "display:flex;gap:6px;align-items:center" },
      iconAct("x", "Back to the network", async () => { await ctl.closePreview(); say(""); }),
      h("span", { text: previewLine(preview) }))] : []);
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
    const list = h("ol", { id: "pc-draft-changes" });
    for (const change of draft.changes) {
      const i = change.index ?? draft.changes.indexOf(change);
      const remove = iconAct("x", "Remove", async () => {
        const result = await ctl.removeChange(i, baseNet());
        say(`Removed: ${result.removed}${result.readded ? ` (re-added the ${result.readded} after it)` : ""}`);
      }, `Remove change ${i + 1}`);
      const edit = iconAct("pencil", "Edit…", async () => {
        await ctl.editChange(i, baseNet());
        say(`Removed change ${i + 1}: change the form and Add to card to put it back`);
        draw();
      }, `Edit change ${i + 1}`);
      list.append(h("li", { "data-change": String(i), style: "display:flex;gap:4px;align-items:center" },
        h("span", { style: "flex:1", text: change.describe }), edit, remove));
    }
    const undo = iconAct("undo", "Undo last change", async () => {
      await ctl.undo({ netId: baseNet() });
      say("Undid the last change");
    });
    const heading = h("div", { class: "label", tabindex: -1, text: `Draft card${name} (${draft.count})` });
    const from = draft.source ? [h("p", { class: "muted", id: "pc-draft-source",
      text: `Editing ${draft.source.card_id} (${draft.source.path})` })] : [];
    const inspect = iconAct("search", "Inspect", () => openInspect(null), "Inspect the draft card");
    redraw(draftBox, [heading, ...from, list, actionRow(undo, inspect)], takeFocus);
    fillCommit(draft);
  }

  // Set while the commit dialog is drawn: marks its check outdated (the draft changed elsewhere).
  let markOutdated = () => {};

  async function openDialog(fields) {
    const check = await ctl.check(fields);
    const v = commitView(check);
    const note = h("p", { role: "status" });
    // One Commit button, or (a draft loaded from a card) "Save over <file>" and "Save as new file".
    const draft = draftOf(server(), server().active);
    const choices = commitChoices(fields, draft && draft.source);
    const buttons = choices.map(c => h("button", { class: "mini", "data-choice": c.key,
      ...(c.disabled ? { title: c.disabled } : {}) }, c.label || v.button));
    const cancel = h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel");
    const recheck = h("button", { class: "mini" }, "Check again");
    recheck.hidden = true;
    const confirm = h("input", { type: "checkbox", id: "pc-confirm" });
    let outdated = false;
    const sync = () => {
      const off = outdated || v.blocked || (v.needsConfirm && !confirm.checked);
      buttons.forEach((b, i) => { b.disabled = off || Boolean(choices[i].disabled); });
    };
    sync();
    confirm.onchange = sync;
    buttons.forEach((button, i) => {
      button.onclick = async () => {
        try {
          const result = await busy([...buttons, cancel], () => ctl.commit(choices[i].fields));
          dialog.close();
          say(`Committed ${result.card_id} to ${result.path}`);
        } catch (e) {
          note.textContent = e.message;
        } finally {
          sync();
        }
      };
    });
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
      ...choices.filter(c => c.disabled).map(c => h("p", { class: "muted", text: `${c.label}: ${c.disabled}` })),
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" }, cancel, recheck, ...buttons),
    );
    if (!dialog.open) dialog.showModal(); // "Check again" redraws an open dialog
    const ready = buttons.find(b => !b.disabled);
    (v.needsConfirm && !v.blocked ? confirm : ready || cancel).focus();
  }

  // Drawn once: redrawing on every state change would wipe what the user is typing.
  const input = (id, placeholder) => h("input", { type: "text", id, placeholder });
  const project = input("pc-project", "Main Street road diet");
  const tags = input("pc-tags", "comma separated");
  const path = input("pc-path", "cards/main-street.yml");
  // A draft loaded from a card (Edit) fills the commit fields with the card's, once per card loaded.
  let filledFrom = null;
  function fillCommit(draft) {
    const key = draft && draft.source ? JSON.stringify(draft.source) : null;
    if (!key || key === filledFrom) return;
    filledFrom = key;
    project.value = draft.project || "";
    tags.value = (draft.tags || []).join(", ");
    path.value = draft.source.path;
  }
  function drawCommit() {
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
      actionRow(iconAct("save", "Commit…", open, "Commit the draft card…")),
      h("div", { class: "label", text: "Import a card" }),
      field("Card or edit log", source),
      actionRow(iconAct("download", "Import", importIt, "Import the card or edit log")),
    );
  }

  // ------------------------------------------------------------------------------------------------ the catalog

  // The folder controls are drawn once (a redraw would wipe the path being typed); the listing is redrawn.
  const catalogPath = h("input", { type: "text", id: "pc-catalog-path", placeholder: "folder of cards" });
  const catalogLine = h("p", { class: "muted", id: "pc-catalog-line" });
  const catalogList = h("div", { id: "pc-catalog-list" });
  function drawCatalogControls() {
    const open = iconAct("folder-open", "Open", async () => {
      const path = catalogPath.value.trim();
      if (!path) { say("Give a folder of cards"); return; }
      const result = await ctl.openCatalog(path);
      say(`Opened ${result.path}: ${result.cards} card(s)`);
    }, "Open the folder of cards");
    const refresh = iconAct("refresh", "Rescan the folder", async () => {
      const result = await ctl.refreshCatalog();
      say(`Rescanned ${result.path}: ${result.cards} card(s)`);
    });
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

  // --------------------------------------------------------------------------------------------- inspecting

  // The read-only Inspect view of a catalog card or the draft. Clicking a change highlights its links on the map;
  // the map does not zoom to them (the Workbench gives plugins no camera control), so the status says so.
  let showYaml = false;
  let highlighted = false;
  let inspectedDraft = null; // the draft the view shows, to read it again when it changes
  const highlight = links => {
    if (typeof wb.highlight !== "function") return false;
    wb.highlight(links ? { links } : {});
    highlighted = Boolean(links && links.length);
    return true;
  };
  async function openInspect(cardId) {
    const detail = await ctl.inspect(cardId, baseNet());
    showYaml = false;
    inspectedDraft = cardId ? null : JSON.stringify(draftOf(server(), server().active));
    drawInspect(true);
    say(`Inspecting ${detail.card_id || `the draft on ${detail.net_id}`}`);
  }
  function closeInspect() {
    ctl.closeInspect();
    inspectedDraft = null;
    if (highlighted) highlight(null);
    drawInspect(true);
  }
  // Called on every server change: a draft being inspected is read again when it changed (or closed when empty).
  async function draftMoved() {
    if (inspectedDraft === null) return;
    const now = JSON.stringify(draftOf(server(), server().active));
    if (now === inspectedDraft) return;
    inspectedDraft = now;
    try {
      await ctl.inspect(null, baseNet());
      drawInspect();
    } catch (e) {
      closeInspect(); // the draft has no changes any more (committed, or every change removed)
    }
  }
  function drawInspect(force = false) {
    const detail = ctl.state.inspect;
    if (!detail) { redraw(inspectBox, [], force); return; }
    const what = detail.source === "draft" ? `the draft on ${detail.net_id}` : detail.card_id;
    const yaml = glyph(h("button", { class: `iconbtn${showYaml ? " on" : ""}`, "data-tip": "YAML",
      "aria-label": "Show the card's YAML", "aria-pressed": String(showYaml) }), "code", "YAML");
    yaml.onclick = () => { showYaml = !showYaml; drawInspect(); };
    const close = iconAct("x", "Close", async () => closeInspect(), "Close the Inspect view");
    const head = h("div", { style: "display:flex;gap:6px;align-items:center" },
      h("div", { class: "label", tabindex: -1, text: `Inspect: ${what}` }), yaml, close);
    const lines = inspectHeader(detail).map(([label, text]) => h("div", {},
      h("span", { class: "muted", text: `${label}: ` }), h("span", { text })));
    const list = h("ol", { id: "pc-inspect-changes", style: "padding-left:1.2em" });
    for (const row of inspectRows(detail)) {
      const pick = h("button", { class: "mini ghost", "data-index": String(row.index),
        "aria-label": `Highlight change ${row.index + 1} on the map` }, row.title);
      pick.onclick = () => {
        const n = row.links.length;
        if (!highlight(row.links)) say("This Workbench cannot highlight links for plugins");
        else say(n ? `Highlighted ${n} link${n === 1 ? "" : "s"} of change ${row.index + 1} on the map `
          + "(the map does not zoom to them)" : `Change ${row.index + 1} has no links to highlight`);
      };
      list.append(h("li", { "data-change": String(row.index), style: "margin:4px 0" }, pick,
        h("div", { class: "muted", text: row.facility }),
        ...(row.properties.length ? [h("ul", {}, ...row.properties.map(p => h("li", { text: p })))] : []),
        ...(row.note ? [h("div", { class: "muted", text: `Why: ${row.note}` })] : []),
        h("div", { "data-check": row.check.state, text: row.check.text })));
    }
    const nodes = [head, ...lines, h("p", { class: "muted",
      text: "Click a change to highlight its links on the map. Inspecting changes nothing." }), list];
    if (showYaml) {
      nodes.push(h("pre", { id: "pc-inspect-yaml", text: detail.yaml,
        style: "max-height:45vh;overflow:auto;background:var(--bg)" }));
    }
    redraw(inspectBox, nodes, force);
  }

  const opened = new Set(); // the cards whose Details the user opened
  function cardRow(row) {
    const tags = row.tags.length ? ` · ${row.tags.join(", ")}` : "";
    const badge = h("span", {
      class: "pc-badge", title: row.tip, "aria-label": row.tip, "data-badge": row.badge,
      style: "display:inline-block;width:1.4em;font-weight:700", text: row.badge,
    });
    const of = `${row.project} (${row.card_id})`;
    const preview = iconAct("play", "Preview", async () => {
      const result = await ctl.previewCard(row.card_id, baseNet());
      say(`Previewing ${row.card_id} on ${result.preview}`);
    }, `Preview ${of}`);
    // Applying is off unless the enable_apply setting is on: the server refuses it then, so the row offers none.
    const apply = applyEnabled(server()) ? [iconAct("check-circle", "Apply…", () => openApplyDialog(row), `Apply ${of}…`)]
      : [];
    const inspect = iconAct("search", "Inspect", () => openInspect(row.card_id), `Inspect ${of}`);
    const edit = iconAct("pencil", "Edit", () => openEditDialog(row), `Edit ${of} in the draft`);
    // The Details toggle shows the card's file and its check; a redraw (any server change) keeps it open if it was.
    const isOpen = opened.has(row.card_id);
    const details = h("div", { class: "pc-details", style: "margin:2px 0 0 1.4em" },
      h("p", { class: "muted", text: row.path }), ...detailRows(row));
    details.hidden = !isOpen;
    const toggle = glyph(h("button", { class: `iconbtn${isOpen ? " on" : ""}`, "data-tip": "Details",
      "aria-label": `Details of ${of}`, "aria-expanded": String(isOpen) }), "info", "Details");
    toggle.onclick = () => {
      const now = !opened.has(row.card_id);
      if (now) opened.add(row.card_id); else opened.delete(row.card_id);
      details.hidden = !now;
      toggle.setAttribute("aria-expanded", String(now));
      toggle.setAttribute("class", `iconbtn${now ? " on" : ""}`);
    };
    const actions = actionRow(preview, ...apply, inspect, edit, toggle,
      iconAct("rename", "Rename…", () => openRenameDialog(row), `Rename ${of}…`),
      iconAct("copy", "Duplicate", async () => say(fileLine(await ctl.duplicateCard(row.card_id))), `Duplicate ${of}`),
      iconAct("tag", "Edit metadata…", () => openMetaDialog(row), `Edit the metadata of ${of}…`),
      iconAct("trash", "Delete", () => deleteCard(row), `Delete ${of}`));
    actions.style.marginLeft = "1.4em";
    return h("div", { class: "pc-card", "data-card": row.card_id, style: "margin:6px 0 8px" },
      h("div", {}, badge, " ", h("strong", { text: row.project }),
        h("span", { class: "muted", text: ` · ${row.changes} change(s)${tags} · ${row.card_id}` })),
      actions, details,
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

  // ------------------------------------------------------------------------------------ the catalog's files

  // A dialog with `fields` (labeled controls), a status line and Cancel / `label`: `run()` resolves to the status
  // text, and a refusal stays in the dialog.
  function fileDialog(title, fields, label, run) {
    const note = h("p", { role: "status" });
    const go = h("button", { class: "mini" }, label);
    const cancel = h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel");
    go.onclick = async () => {
      try {
        const text = await busy([go, cancel], run);
        if (text === null) return; // the form itself said what is missing
        dialog.close();
        say(text);
      } catch (e) {
        note.textContent = e.message;
      }
    };
    dialog.replaceChildren(h("h3", { id: "pc-dialog-title", text: title }), ...fields, note,
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" }, cancel, go));
    if (!dialog.open) dialog.showModal();
    return note;
  }

  function openRenameDialog(row) {
    const name = h("input", { type: "text", id: "pc-rename" });
    name.value = row.card_id;
    const overwrite = h("input", { type: "checkbox", id: "pc-rename-overwrite" });
    const note = fileDialog(`Rename "${row.project}"`, [
      field("New file name (in the catalog folder)", name), field("Replace an existing file", overwrite)],
    "Rename", async () => {
      const { name: newName, error } = renameName(name.value, row.card_id);
      if (error) { note.textContent = error; return null; }
      return fileLine(await ctl.renameCard(row.card_id, newName, overwrite.checked));
    });
    name.focus();
  }

  async function openMetaDialog(row) {
    const start = metaForm(await ctl.cardDetail(row.card_id, baseNet()));
    const inputs = Object.fromEntries(["project", "tags", "prerequisites", "corequisites", "conflicts"].map(k => {
      const el = h("input", { type: "text", id: `pc-meta-${k}` });
      el.value = start[k];
      return [k, el];
    }));
    const labels = { project: "Project name", tags: "Tags (comma separated)",
      prerequisites: "Prerequisites (projects, comma separated)", corequisites: "Corequisites", conflicts: "Conflicts" };
    const note = fileDialog(`Edit metadata of "${row.project}"`,
      Object.entries(inputs).map(([k, el]) => field(labels[k], el)), "Save", async () => {
        const { fields, error } = metaFields(Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])));
        if (error) { note.textContent = error; return null; }
        return fileLine(await ctl.updateCardMeta(row.card_id, fields));
      });
    inputs.project.focus();
  }

  // A delete moves the file to the catalog's trash: the status offers it back at once.
  async function deleteCard(row) {
    const result = await ctl.deleteCard(row.card_id);
    sayWith(fileLine(result), "Undo delete", async () => say(fileLine(await ctl.restoreCard(row.card_id))));
  }

  // Edit: load the card into the draft. An empty draft (and a card that applies) needs no question; otherwise the
  // dialog says what happens (the draft's changes undone, or the card refused), and a refusal shows in it.
  async function openEditDialog(row) {
    const prompt = editPrompt(draftOf(server(), server().active), row);
    const run = async () => {
      const result = await ctl.editCard(row.card_id, { replaceDraft: prompt.replace, netId: baseNet() });
      say(`Editing ${result.card_id}: ${result.count} change(s) in the draft. Save over it from Commit…`);
      draw();
    };
    if (!prompt.ask) { await run(); return; }
    const note = h("p", { role: "status" });
    const go = h("button", { class: "mini" }, prompt.replace ? "Replace the draft" : "Edit anyway");
    const cancel = h("button", { class: "mini ghost", onclick: () => dialog.close() }, "Cancel");
    go.onclick = async () => {
      try {
        await busy([go, cancel], run);
        dialog.close();
      } catch (e) {
        note.textContent = e.message;
      }
    };
    dialog.replaceChildren(
      h("h3", { id: "pc-dialog-title", text: `Edit "${row.project}"` }),
      h("p", { text: prompt.ask }),
      note,
      h("div", { style: "display:flex;gap:8px;justify-content:flex-end" }, cancel, go),
    );
    if (!dialog.open) dialog.showModal();
    cancel.focus();
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

  // ---------------------------------------------------------------------------- this session, and the summary

  // What this session did with cards, newest first: each entry an icon, its sentence ("Saved main.yml") and the time.
  function drawSession() {
    const rows = sessionRows(server());
    if (!rows.length) {
      redraw(sessionBox, [h("p", { class: "empty", tabindex: -1,
        text: "Nothing yet: saved cards and catalog file changes are listed here." })]);
      return;
    }
    const list = h("ul", { id: "pc-session-list", style: "list-style:none;padding:0;margin:0" });
    for (const r of rows) {
      const mark = glyph(h("span", { "aria-hidden": "true", style: "display:inline-flex;width:16px;flex:none" }),
        r.icon, "•");
      list.append(h("li", { "data-card": r.card_id, "data-kind": r.kind, title: r.at,
        style: "display:flex;gap:6px;align-items:baseline;margin:3px 0" },
      mark, h("span", { style: "flex:1", text: r.text },
        ...(r.detail ? [h("span", { class: "muted", text: ` · ${r.detail}` })] : [])),
      h("span", { class: "muted", text: r.time })));
    }
    redraw(sessionBox, [list]);
  }

  function drawSummary() {
    const copy = iconAct("copy", "Copy the session summary", async () => {
      const text = await ctl.summary(baseNet());
      const clipboard = globalThis.navigator && globalThis.navigator.clipboard;
      if (!clipboard) throw new Error("The clipboard is not available here: download the summary instead");
      await clipboard.writeText(text);
      say("Copied the session summary");
    });
    const download = iconAct("download", "Download the session summary", async () => {
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
    drawSession();
  }

  // The controller dropped the dialog's check: Commit stays disabled until the user checks again.
  const checkWentStale = () => { if (dialog.open) markOutdated(); };
  // The controller marked the open form outdated: Preview and Add stay disabled until the user reloads it.
  const composeWentStale = () => markComposeOutdated();

  drawCatalogControls();
  drawCommit();
  drawSummary();
  return { draw, drawCreate, draftMoved, loadCatalog, checkWentStale, composeWentStale };
}
