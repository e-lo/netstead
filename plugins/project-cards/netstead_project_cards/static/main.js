// The project cards plugin's front end: the "Cards" dock panel (its id is the bare `cards` core reserves for this
// plugin) and the "New change ▸ …" commands. The panel has four sections: the catalog (a folder of cards, each row
// its fit icon, its name, Inspect details and Preview; the details view edits a card), the draft card (compose a change, preview it, the draft's changes,
// commit and import), this session's card actions (saved, applied, catalog files), and the session summary (copied
// or downloaded). Everything
// goes through `wb` (see the cookbook page "Write a Workbench plugin"); the flow lives in controller.js and the rules
// in cardmodel.js, both tested under node. Card and user text is only ever set as text (`h`'s `text`, or a string
// child), never parsed as markup.
import {
  ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, applyDefaults, applyEnabled, catalogOf, checkedAgainst, sampleCatalogs, catalogRows, commitChoices,
  FACILITY_FORMS, ID_SYSTEMS, facilityReason,
  commitFields, commitView, draftBadge, editPrompt, fileLine, draftNet, draftOf, hasNode, importPath, inspectHeader, inspectRows, needsSelection,
  openPreview, previewLine, savedChoices, selectionStatus, sessionRows, stepIndicator, summaryFileName, typeChoices,
  READ_ONLY_TIP, catalogReadOnly, detailFields, editKey, fieldEdit, fitDetail, fitIcon, rowSubline,
} from "./cardmodel.js";
import { createController } from "./controller.js";

const PANEL = "cards";
// The catalog's table in the table pane (the server registers it with host.register_table).
const CATALOG_TABLE = "cards_catalog";

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
  check: '<polyline points="20 6 9 17 4 12"/>',
  dash: '<line x1="6" y1="12" x2="18" y2="12"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/>'
    + '<line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="10" x2="9" y2="20"/>',
};
const svgOf = body => '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" '
  + `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
// The fit icons' colors: green fits, red doesn't, gray not checked (core's dark theme).
const FIT_COLORS = { fits: "#5fd38a", no: "#ff7a75", unchecked: "var(--muted)" };
// The text an icon stands in for where the host draws no icons.
const FIT_TEXT = { fits: "✓", no: "✗", unchecked: "–" };
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
    order: 35,
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
  // A row clicked in the catalog's table (the table pane, Host API 1.8): the panel comes forward with that card.
  wb.on("core.table_row", payload => {
    if (!payload || payload.table !== CATALOG_TABLE) return;
    wb.showPanel(PANEL); // the first show renders the panel
    if (view) view.selectCard(payload.id);
  });
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
  const catalogHeading = heading("Catalog"); // names the network the cards were checked against (drawCatalog)
  el.style.overflowWrap = "anywhere"; // long file paths wrap instead of scrolling the dock sideways
  // The catalog's rows and the details' editable fields (core's tooltip layer draws every data-tip).
  el.setAttribute("data-pc-panel", "");
  el.append(h("style", { text: "[data-pc-panel] .pc-card .iconbtn{width:28px;height:28px}"
    + "[data-pc-panel] .pc-field-value[tabindex]{cursor:text;border-radius:4px;padding:0 3px}"
    + "[data-pc-panel] .pc-field-value[tabindex]:hover,[data-pc-panel] .pc-field-value[tabindex]:focus{"
    + "background:#20242e;outline:none}" }));
  el.append(
    catalogHeading, catalogBox, previewBox, inspectBox,
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

  // What the change aims at: the selection in words and its warnings, and (for a property change) the choice of how
  // the facility is written: Segment or Link list, in GMNS or OpenStreetMap ids. A choice reads the form again (the
  // targets and their current values depend on it) and keeps what was typed (`carry()`).
  function targetNodes(c, carry) {
    const targets = c.form.targets;
    const words = h("p", { class: "muted", id: "pc-facility-label", role: "status", text: targets.label });
    const notes = h("div", { id: "pc-facility-notes" },
      ...(targets.warnings || []).map(w => h("p", { class: "muted", text: w })));
    if (!c.facility) return [words, notes];
    const choose = part => guard(async () => { await ctl.setFacility(part, carry()); draw(); })();
    const radio = f => {
      const why = facilityReason(targets, "forms", f.key);
      const input = h("input", { type: "radio", name: "pc-facility-form", id: `pc-form-${f.key}`, value: f.key,
        checked: c.facility.form === f.key, disabled: Boolean(why), title: why || f.hint });
      input.onchange = () => choose({ form: f.key });
      return h("label", { title: why || f.hint, style: why ? "opacity:.6" : "" }, input, ` ${f.label} `);
    };
    const system = h("select", { id: "pc-id-system", "aria-label": "ID system" });
    for (const s of ID_SYSTEMS) {
      const why = facilityReason(targets, "id_systems", s.key);
      system.append(h("option", { value: s.key, text: why ? `${s.label} (unavailable)` : s.label,
        disabled: Boolean(why), title: why }));
    }
    system.value = c.facility.id_system;
    system.onchange = () => choose({ id_system: system.value });
    const reasons = [...FACILITY_FORMS.map(f => [f.label, facilityReason(targets, "forms", f.key)]),
      ...ID_SYSTEMS.map(s => [s.label, facilityReason(targets, "id_systems", s.key)])].filter(([, why]) => why);
    const group = h("div", { role: "group", "aria-label": "Facility form", id: "pc-facility",
      style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0" },
    ...FACILITY_FORMS.map(radio), h("label", {}, "ID system ", system));
    return [words, group, ...reasons.map(([what, why]) => h("p", { class: "muted", text: `${what}: ${why}` })), notes];
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
    if (c.form.targets) composeBox.append(...targetNodes(c, () => carried()));
    const forms = {};
    const carried = () => Object.fromEntries(["columns", "options"].filter(k => forms[k]).map(k => [k, forms[k].value()]));
    for (const key of ["columns", "link", "node", "options"]) {
      if (!c.form[key]) continue;
      if (key === "node") {
        const next = c.form.next_ids && c.form.next_ids.node_id;
        composeBox.append(h("p", { class: "muted", text: `New end node (optional; next free id: ${next})` }));
      }
      const slot = h("div");
      composeBox.append(slot);
      const start = key === "link" && c.form.next_ids ? { link_id: c.form.next_ids.link_id } : (c.carry || {})[key] || {};
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
  const sampleLine = h("p", { class: "muted", id: "pc-samples" }); // the bundled sample catalogs, one click each
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
    // The catalog as a sortable, filterable table in the table pane (a host with wb.showTable, Host API 1.8).
    const table = typeof wb.showTable === "function" ? [iconAct("table", "Show as table", async () => {
      await wb.showTable(CATALOG_TABLE);
      say("The catalog is in the table pane: sort and filter it there, click a row to find its card here");
    }, "Show the catalog as a table")] : [];
    catalogBox.append(
      h("div", { style: "display:flex;gap:6px;align-items:center" }, catalogPath, open, refresh, ...table),
      sampleLine, catalogLine, catalogList,
    );
  }

  // One button per bundled sample catalog (Host API 1.9); the one written for the open network is the primary button.
  // Its files sit inside netstead's own install, so the server reads them but refuses every change to them.
  function drawSamples() {
    const samples = sampleCatalogs(server(), baseNet());
    const buttons = samples.map(sample => {
      const b = h("button", { class: sample.suggested ? "mini" : "mini ghost", "data-sample": sample.id,
        title: `Open the ${sample.cards} sample cards bundled with netstead, written for the ${sample.network} example network (read-only)` },
      sample.name);
      b.disabled = sample.isOpen;
      b.onclick = guard(() => busy([b], async () => {
        const result = await ctl.openCatalog(sample.path);
        say(`Opened ${sample.name}: ${result.cards} card(s). It is read-only; copy its folder to change the cards.`);
      }));
      return b;
    });
    sampleLine.replaceChildren(...(buttons.length ? ["Try: ", ...buttons] : []));
    sampleLine.hidden = buttons.length === 0;
  }

  // A listing is read when one is open (the route checks unchecked cards, so the "Not checked" pills fill in).
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

  // --------------------------------------------------------------------------------------------- inspecting

  // The details view (#pc-inspect) of a catalog card or the draft. A catalog card's shows its fit in full, its
  // fields (double-click one to edit it, unless the catalog is read-only) and its actions; clicking a change
  // highlights its links on the map. The map does not zoom to them (the Workbench gives plugins no camera control).
  let showYaml = false;
  let highlighted = false;
  let inspectedDraft = null; // the draft the view shows, to read it again when it changes
  let editing = null; // the key of the details field being edited, or null
  const highlight = links => {
    if (typeof wb.highlight !== "function") return false;
    wb.highlight(links ? { links } : {});
    highlighted = Boolean(links && links.length);
    return true;
  };
  // The element `id` under `root`, or null.
  const findIn = (root, id) => {
    if (root.id === id) return root;
    for (const child of root.children || []) {
      const found = findIn(child, id);
      if (found) return found;
    }
    return null;
  };
  function focusIn(id) {
    const target = findIn(inspectBox, id);
    if (!target) return;
    target.focus();
    if (typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "nearest" });
  }
  // `focusFit`: the view opens at the card's fit reasons (the red X).
  async function openInspect(cardId, { focusFit = false } = {}) {
    const detail = await ctl.inspect(cardId, baseNet());
    showYaml = false;
    editing = null;
    inspectedDraft = cardId ? null : JSON.stringify(draftOf(server(), server().active));
    drawInspect(true);
    drawCatalog(); // the row being inspected is marked
    if (focusFit) focusIn("pc-inspect-fit");
    say(`Inspecting ${detail.card_id || `the draft on ${detail.net_id}`}`);
  }
  // Read a catalog card's details again (after one of its fields was saved: under its new name after a rename).
  async function reinspect(cardId, focusField) {
    await ctl.inspect(cardId, baseNet());
    editing = null;
    drawInspect(true);
    focusIn(`pc-field-${focusField}`);
  }
  function closeInspect() {
    ctl.closeInspect();
    inspectedDraft = null;
    editing = null;
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
  // The catalog row of the card being inspected (its fit is the listing's), or null.
  const inspectedRow = detail => (detail && detail.source === "catalog"
    ? catalogRows(server(), ctl.catalog()).find(r => r.card_id === detail.card_id) || null : null);

  // The fit in full: the icon and its title, every reason, every warning; a card not checked yet has a check button.
  function fitSection(row) {
    const fit = fitDetail(row);
    const nodes = [h("div", { style: "display:flex;gap:6px;align-items:center" }, fitMark(fit.fit, "span"),
      h("span", { text: fit.title }))];
    if (fit.reasons.length) {
      nodes.push(h("ul", { class: "pc-fit-reasons" }, ...fit.reasons.map(r => h("li", { text: r }))));
    }
    if (fit.warnings.length) {
      nodes.push(h("div", { class: "muted", text: "Warnings:" }),
        h("ul", { class: "pc-fit-warnings" }, ...fit.warnings.map(w => h("li", { text: w }))));
    }
    if (row && fit.fit === "unchecked") {
      nodes.push(actionRow(iconAct("check-circle", "Check against this network", () => checkCard(row),
        `Check ${row.project} against this network`)));
    }
    return h("div", { id: "pc-inspect-fit", tabindex: -1, "aria-label": "Fit with this network",
      style: "margin:4px 0" }, ...nodes);
  }

  // One field of a catalog card's details: its value (double-click it, or press Enter on it, to edit), or the input
  // editing it (Enter or Cmd/Ctrl+Enter saves, Escape cancels, leaving the field saves).
  function fieldRow(detail, f) {
    const label = h("span", { class: "muted", text: `${f.label}: ` });
    if (editing !== f.key) {
      const value = h("span", { class: "pc-field-value", id: `pc-field-${f.key}`, "data-field": f.key,
        "data-tip": f.tip, "aria-label": `${f.label}: ${f.value || "none"}`,
        ...(f.editable ? { tabindex: 0, role: "button" } : { "aria-readonly": "true" }), text: f.value || "—" });
      if (f.editable) {
        const begin = () => {
          editing = f.key;
          drawInspect();
          focusIn(`pc-edit-${f.key}`);
          const input = findIn(inspectBox, `pc-edit-${f.key}`);
          if (input && typeof input.select === "function") input.select(); // typing replaces the value
        };
        value.ondblclick = begin;
        value.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); begin(); } };
      }
      return h("div", { "data-row": f.key }, label, value);
    }
    const input = h("input", { type: "text", id: `pc-edit-${f.key}`, "aria-label": f.label,
      ...(f.hint ? { placeholder: f.hint } : {}), style: "width:100%" });
    input.value = f.value;
    let saving = false;
    const stop = () => { editing = null; drawInspect(); focusIn(`pc-field-${f.key}`); };
    const finish = async apply => {
      if (saving || editing !== f.key) return;
      const edit = apply ? fieldEdit(detail, f.key, input.value) : { unchanged: true };
      if (edit.unchanged) { stop(); return; }
      if (edit.error) { say(edit.error); return; } // the input stays open, to fix
      saving = true;
      input.disabled = true;
      try {
        const result = edit.rename ? await ctl.renameCard(detail.card_id, edit.rename)
          : await ctl.updateCardMeta(detail.card_id, edit.meta);
        say(fileLine(result));
        await reinspect(edit.rename ? result.to : detail.card_id, f.key);
      } catch (e) {
        say(e.message); // refused (say, a file by that name exists): the input stays open
        saving = false;
        input.disabled = false;
        input.focus();
      }
    };
    input.onkeydown = e => {
      const what = editKey(e);
      if (!what) return;
      e.preventDefault();
      e.stopPropagation(); // Escape cancels the edit, nothing else
      return finish(what === "apply");
    };
    input.onblur = () => finish(true);
    return h("div", { "data-row": f.key }, h("label", { class: "muted", for: input.id, text: `${f.label}: ` }), input);
  }

  // The details' actions for a catalog card: Edit changes, Apply… (with enable_apply), then Duplicate and Delete (not
  // in a read-only catalog).
  function cardActions(row, readOnly) {
    if (!row) return [];
    const of = `${row.project} (${row.card_id})`;
    const buttons = [iconAct("pencil", "Edit changes", () => openEditDialog(row), `Edit the changes of ${of} in the draft`)];
    if (applyEnabled(server())) buttons.push(iconAct("check-circle", "Apply…", () => openApplyDialog(row), `Apply ${of}…`));
    if (!readOnly) {
      buttons.push(
        iconAct("copy", "Duplicate", async () => say(fileLine(await ctl.duplicateCard(row.card_id))), `Duplicate ${of}`),
        iconAct("trash", "Delete", () => deleteCard(row), `Delete ${of}`));
    }
    return buttons;
  }

  function drawInspect(force = false) {
    const detail = ctl.state.inspect;
    if (!detail) { redraw(inspectBox, [], force); return; }
    const catalogCard = detail.source !== "draft";
    const row = inspectedRow(detail);
    const readOnly = catalogReadOnly(server());
    const yaml = glyph(h("button", { class: `iconbtn${showYaml ? " on" : ""}`, "data-tip": "YAML",
      "aria-label": "Show the card's YAML", "aria-pressed": String(showYaml) }), "code", "YAML");
    yaml.onclick = () => { showYaml = !showYaml; drawInspect(); };
    const close = iconAct("x", "Close", async () => closeInspect(), "Close the details");
    const title = catalogCard ? `Details: ${detail.project || detail.card_id}` : `Inspect: the draft on ${detail.net_id}`;
    const head = h("div", { style: "display:flex;gap:4px;align-items:center;flex-wrap:wrap" },
      h("div", { class: "label", tabindex: -1, style: "flex:1", text: title }),
      ...(catalogCard ? cardActions(row, readOnly) : []), yaml, close);
    const nodes = [head];
    if (catalogCard) {
      nodes.push(fitSection(row), ...detailFields(detail, { readOnly }).map(f => fieldRow(detail, f)));
      if (detail.notes) {
        nodes.push(h("div", {}, h("span", { class: "muted", text: "Notes: " }), h("span", { text: detail.notes })));
      }
      nodes.push(h("p", { class: "muted", text: readOnly ? `${READ_ONLY_TIP}.`
        : "Double-click a field (or focus it and press Enter) to edit it: Enter saves, Escape cancels." }));
    } else {
      nodes.push(...inspectHeader(detail).map(([label, text]) => h("div", {},
        h("span", { class: "muted", text: `${label}: ` }), h("span", { text }))));
    }
    const list = h("ol", { id: "pc-inspect-changes", style: "padding-left:1.2em" });
    for (const change of inspectRows(detail)) {
      const pick = h("button", { class: "mini ghost", "data-index": String(change.index),
        "aria-label": `Highlight change ${change.index + 1} on the map` }, change.title);
      pick.onclick = () => {
        const n = change.links.length;
        if (!highlight(change.links)) say("This Workbench cannot highlight links for plugins");
        else say(n ? `Highlighted ${n} link${n === 1 ? "" : "s"} of change ${change.index + 1} on the map `
          + "(the map does not zoom to them)" : `Change ${change.index + 1} has no links to highlight`);
      };
      list.append(h("li", { "data-change": String(change.index), style: "margin:4px 0" }, pick,
        h("div", { class: "muted", text: change.facility }),
        ...(change.properties.length ? [h("ul", {}, ...change.properties.map(p => h("li", { text: p })))] : []),
        ...(change.note ? [h("div", { class: "muted", text: `Why: ${change.note}` })] : []),
        h("div", { "data-check": change.check.state, text: change.check.text })));
    }
    nodes.push(h("p", { class: "muted", text: "Click a change to highlight its links on the map. "
      + "Inspecting changes nothing." }), list);
    if (showYaml) {
      nodes.push(h("pre", { id: "pc-inspect-yaml", text: detail.yaml,
        style: "max-height:45vh;overflow:auto;background:var(--bg)" }));
    }
    redraw(inspectBox, nodes, force);
  }

  // A fit icon (a green check, a red X or a gray dash) drawn in a `tag`: a span, or a button (the row's X and dash).
  function fitMark(fit, tag, props = {}) {
    return glyph(h(tag, { class: tag === "button" ? "iconbtn pc-fit" : "pc-fit", "data-fit": fit,
      style: `color:${FIT_COLORS[fit]};display:inline-flex;align-items:center;justify-content:center;flex:none`,
      ...props }), fitIcon({ fit }).icon, FIT_TEXT[fit]);
  }

  // Check one card against this network (read only); the listing (and the card's details, if open) follow.
  async function checkCard(row) {
    const result = await ctl.checkCard(row.card_id, baseNet());
    say(`Checked ${row.card_id}: ${result.ok ? "fits" : "doesn't fit"} ${result.net_id}`);
    drawCatalog();
  }

  let selectedCard = null; // the card last picked in the catalog's table (selectCard)
  // A catalog row: its fit icon, its project name (its tags beneath, muted), then Inspect details and Preview.
  function cardRow(row) {
    const of = `${row.project} (${row.card_id})`;
    const icon = fitIcon(row);
    let mark;
    if (icon.action === "details") {
      mark = fitMark(row.fit, "button", { "data-tip": icon.tip, "aria-label": `${of}: incompatible; see details` });
      mark.onclick = guard(() => busy([mark], () => openInspect(row.card_id, { focusFit: true })));
    } else if (icon.action === "check") {
      mark = fitMark(row.fit, "button", { "data-tip": icon.tip, "aria-label": `Check ${of} against this network` });
      mark.onclick = guard(() => busy([mark], () => checkCard(row)));
    } else {
      mark = fitMark(row.fit, "span", { "data-tip": icon.tip, role: "img", "aria-label": icon.tip,
        style: `color:${FIT_COLORS.fits};display:inline-flex;align-items:center;justify-content:center;width:28px;flex:none` });
    }
    const subline = rowSubline(row);
    const name = h("div", { style: "flex:1;min-width:0" }, h("div", { class: "pc-name", text: row.project }),
      ...(subline ? [h("div", { class: "muted pc-tags", style: "font-size:11px", text: subline })] : []));
    const info = iconAct("info", "Inspect details", () => openInspect(row.card_id), `Inspect details of ${of}`);
    const preview = iconAct("play", "Preview on the network", async () => {
      const result = await ctl.previewCard(row.card_id, baseNet());
      say(`Previewing ${row.card_id} on ${result.preview}`);
    }, `Preview ${of} on the network`);
    const picked = row.card_id === selectedCard;
    const inspected = (ctl.state.inspect || {}).card_id === row.card_id;
    return h("div", { class: "pc-card", "data-card": row.card_id,
      ...(picked ? { "aria-current": "true" } : {}),
      style: `display:flex;gap:4px;align-items:center;margin:2px 0;padding:2px 0;border-radius:4px;`
        + `${inspected ? "background:#20242e;" : ""}${picked ? "outline:1px solid var(--accent);outline-offset:1px" : ""}` },
    mark, name, info, preview);
  }

  function drawCatalog() {
    drawSamples();
    const listing = ctl.catalog();
    const against = listing ? checkedAgainst(listing) : null;
    catalogHeading.textContent = against ? `Catalog · checked against ${against}` : "Catalog";
    if (!listing) {
      catalogLine.textContent = "";
      redraw(catalogList, [h("p", { class: "empty", tabindex: -1, text: "No catalog open: open a folder of cards, or try the sample cards." })]);
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
    // A catalog card's details follow its fit (a field being edited keeps what is typed).
    if (ctl.state.inspect && ctl.state.inspect.source === "catalog" && !editing) drawInspect();
  }

  // ------------------------------------------------------------------------------------ the catalog's files

  // A delete moves the file to the catalog's trash: the status offers it back at once.
  async function deleteCard(row) {
    const result = await ctl.deleteCard(row.card_id);
    if ((ctl.state.inspect || {}).card_id === row.card_id) closeInspect();
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
      h("p", { class: "muted", text: `${row.label}: ${row.tip}` }),
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
  // A card picked in the catalog's table: marked in the listing, scrolled into view, its first button focused.
  function selectCard(cardId) {
    const row = catalogRows(server(), ctl.catalog()).find(r => r.card_id === cardId);
    if (!row) { say(`${cardId} is not in the open catalog`); return; }
    selectedCard = cardId;
    drawCatalog();
    const card = [...catalogList.children].find(c => c.getAttribute && c.getAttribute("data-card") === cardId);
    if (card && typeof card.scrollIntoView === "function") card.scrollIntoView({ block: "nearest" });
    const first = card ? buttonsIn([card])[0] : null;
    if (first) first.focus();
    say(`Selected ${row.project} (${cardId}) from the table`);
  }

  return { draw, drawCreate, draftMoved, loadCatalog, checkWentStale, composeWentStale, selectCard };
}
