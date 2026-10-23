// The cards panel's flow without the DOM: which route each step reads and which Action it dispatches. main.js draws
// it; tests/test_frontend_model.py runs it under node with a stub `wb`. A refused Action rejects, and the step stops
// there: the form and the preview stay open, so the user can fix the form and try again. Whether a preview is open is
// read from the server's state (`openPreview(wb.store.get())`), never remembered here: the palette, the assistant or
// another tab can close one. One step runs at a time: a second click while an Action is in flight is refused, so a
// change is never added (or a card committed) twice.
import {
  addition, catalogOf, changeAction, chooseType, createAfter, deletion, draftOf, needsSelection, openPreview,
  propertyChange, selectionKey, startCreate,
} from "./cardmodel.js";

const query = params => Object.entries(params)
  .filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  .join("&");

export function createController(wb) {
  const state = { compose: null, check: null, catalog: null, create: null, inspect: null };
  const previewOpen = () => openPreview(wb.store.get()) !== null;
  let running = false;
  const oneAtATime = fn => async (...args) => {
    if (running) throw new Error("Wait for the last step to finish");
    running = true;
    try { return await fn(...args); } finally { running = false; }
  };

  // Open the form for a change type: the plugin's /form route says what the selection holds now. An unsupported type
  // only says so: a form already open stays open (and working). A form read from the shared selection remembers
  // which selection (read before the form, so a change while it loads still outdates it): see selectionChanged.
  async function start(type, { saved = null } = {}) {
    const params = { type, saved };
    const onSelection = needsSelection(type) && !saved;
    if (onSelection) params.use_selection = "true";
    const key = onSelection ? selectionKey(wb.store.get().selection) : null;
    const form = await wb.api.get(`/form?${query(params)}`);
    if (!form.supported) {
      wb.toast(form.message);
      return null;
    }
    state.compose = { type, form, saved, selection: key, outdated: false };
    return form;
  }

  const openForm = () => {
    if (!state.compose) throw new Error("no change form is open");
    return state.compose;
  };

  // Call on every server change: true when the shared selection a form was read from changed (once per form). Its
  // targets and current values describe the old selection, while Preview and Add would act on the new one, so the
  // form is outdated: they are refused until it is read again (reload).
  function selectionChanged() {
    const c = state.compose;
    if (!c || c.selection === null || c.outdated || selectionKey(wb.store.get().selection) === c.selection) return false;
    c.outdated = true;
    return true;
  }

  // The open form read again, for the selection as it is now.
  const reload = () => {
    const { type, saved } = openForm();
    return start(type, { saved });
  };

  // The change the open form describes. `values` is {columns, options, link, node}: each form's value().
  function changeFrom(values) {
    const { type, form } = openForm();
    if (type === "roadway_property_change") return propertyChange(values.columns, values.options, form.existing);
    if (type === "roadway_deletion") return deletion(values.options);
    return addition(values.link, values.node, values.options);
  }

  const dispatchChange = (kind, values) => {
    const { saved, form, outdated } = openForm();
    if (outdated) throw new Error("The selection changed since this form opened: reload the form");
    return wb.api.dispatch(changeAction(kind, changeFrom(values), { saved, netId: form.net_id }));
  };

  // A preview goes to a copy of the network; a second one replaces the first (the server closes the older copy).
  async function preview(values) {
    const result = await dispatchChange("preview", values);
    if (state.create) state.create = createAfter(state.create, "previewed");
    return result;
  }

  const closePreview = () => wb.api.dispatch({ type: "project_cards.close_preview" });

  // The change goes to the network itself (never the preview), so the preview has done its job: close it. Once the
  // change is added, add() resolves: a failure to close the preview is only reported (retrying would add it twice).
  async function add(values) {
    const result = await dispatchChange("add_change", values);
    state.compose = null;
    state.check = null;
    if (state.create) state.create = createAfter(state.create, "added");
    if (previewOpen()) {
      try { await closePreview(); } catch (e) { wb.toast(`The change was added; the preview stayed open: ${e.message}`); }
    }
    return result;
  }

  async function cancel() {
    state.compose = null;
    state.check = null;
    state.create = null;
    if (previewOpen()) await closePreview();
  }

  // ---------------------------------------------------------------------------------------------- creation mode

  // "New card": the guided mode (cardmodel's startCreate). A form already open (from the right-click menu) is
  // closed first, with its preview: the mode draws its own.
  async function beginCreate() {
    if (state.compose) await cancel();
    state.create = startCreate();
    return state.create;
  }

  const openMode = () => {
    if (!state.create) throw new Error("the New card mode is not open");
    return state.create;
  };

  // Read the form for the mode's type: on the shared selection, or the saved one `saved`. A form that could not be
  // read (say, the selection is not resolved) leaves the mode where it was.
  async function openModeForm(saved = null) {
    const mode = openMode();
    const form = await start(mode.type, { saved });
    if (form && state.create === mode) state.create = createAfter(mode, "form", saved);
    return form;
  }

  // Pick the change type: an addition needs no selection, so its form opens at once.
  async function chooseCreateType(type) {
    const mode = chooseType(openMode(), type);
    state.create = mode;
    if (mode.step === "details") await openModeForm();
    return state.create;
  }

  // "Use these N links" (the shared selection), or a saved selection by name.
  const useFacility = ({ saved = null } = {}) => openModeForm(saved);

  // One step back; leaving the form closes it (and its preview).
  async function backCreate() {
    const mode = openMode();
    if (state.compose) {
      state.compose = null;
      if (previewOpen()) await closePreview();
    }
    state.create = createAfter(mode, "back");
    return state.create;
  }

  // The Select step's "describe them": core's natural-language selection (the `select` Action), on `netId`.
  const selectByText = (utterance, netId = null) => {
    const text = String(utterance || "").trim();
    if (!text) throw new Error("Describe the links to select, say “Main Street from 1st to 5th”");
    return wb.api.dispatch({ type: "select", utterance: text, ...(netId ? { net_id: netId } : {}) });
  };

  // After "Add to card": start the next change of the same card, or leave the mode.
  const anotherChange = () => { openMode(); state.create = startCreate(); return state.create; };
  const endCreate = () => { state.create = null; };

  // `netId`: the network whose draft to undo (by default the server's choice: the active one, or a preview's base).
  function undo({ netId = null } = {}) {
    state.check = null;
    const action = { type: "project_cards.undo_change" };
    return wb.api.dispatch(netId ? { ...action, net_id: netId } : action);
  }

  // Before committing: the live edit warnings, the schema problems and the card as YAML, for the commit dialog. The
  // check stays until a commit succeeds or the draft changes (add, undo, import, cancel, or from elsewhere).
  const draftNow = netId => JSON.stringify(draftOf(wb.store.get(), netId));
  let checkedDraft = null;
  async function check(fields) {
    state.check = await wb.api.get(`/commit-check?${query({ project: fields.project, net_id: fields.net_id })}`);
    checkedDraft = draftNow(state.check.net_id);
    return state.check;
  }

  // Call on every server change: true when it changed the checked draft (another tab, the assistant, the palette),
  // which drops the check, so its warnings are never confirmed for a draft they no longer describe.
  function serverChanged() {
    if (!state.check || draftNow(state.check.net_id) === checkedDraft) return false;
    state.check = null;
    return true;
  }

  // Committing after the dialog showed warnings for this network is the confirmation the server asks for. A refusal
  // (say, the file exists) keeps the check, so the retry confirms the same warnings.
  async function commit(fields) {
    const shown = state.check;
    const confirm = Boolean(shown && shown.warnings.length && fields.net_id && shown.net_id === fields.net_id);
    const result = await wb.api.dispatch({ type: "project_cards.commit", ...fields, confirm_warnings: confirm });
    if (state.check === shown) state.check = null;
    return result;
  }

  function importCard(path) {
    state.check = null;
    return wb.api.dispatch({ type: "project_cards.import", path });
  }

  // ------------------------------------------------------------------------------------------------ the catalog

  // The /catalog listing, which checks unchecked cards against `netId` (so the badges fill in): kept in
  // `state.catalog` until the open catalog changes. Reads only; the server's state is not touched.
  async function loadCatalog(netId = null) {
    state.catalog = await wb.api.get(`/catalog?${query({ net_id: netId })}`);
    return state.catalog;
  }

  // The catalog to list: the loaded listing while it is the open catalog's (its checks are fresher than the
  // snapshot's), else the server's snapshot (null when none is open).
  function catalog() {
    const open = catalogOf(wb.store.get());
    if (!open) {
      state.catalog = null;
      return null;
    }
    const loaded = state.catalog;
    return loaded && loaded.path === open.path ? loaded : open;
  }

  // Call on every server change: true when the open catalog's snapshot changed (opened, refreshed, a card applied
  // or its check dropped), so the listing is loaded again.
  let seenCatalog = null;
  function catalogChanged() {
    const now = JSON.stringify(catalogOf(wb.store.get()));
    if (now === seenCatalog) return false;
    seenCatalog = now;
    return now !== "null";
  }

  const openCatalog = path => wb.api.dispatch({ type: "project_cards.open_catalog", path });
  const refreshCatalog = () => wb.api.dispatch({ type: "project_cards.refresh_catalog" });

  // A card shown on a copy of `netId` (the server publishes `card_selection` with the links it aims at).
  const previewCard = (cardId, netId = null) =>
    wb.api.dispatch({ type: "project_cards.preview_card", card_id: cardId, ...(netId ? { net_id: netId } : {}) });

  // `options`: {existing_value_conflict, ignore_missing} (the Apply… dialog's). The card goes to the network itself,
  // so an open preview has done its job: close it (a failure to close is only reported; the card was applied).
  async function applyCard(cardId, options, netId = null) {
    state.check = null;
    const result = await wb.api.dispatch({
      type: "project_cards.apply_card", card_id: cardId, ...options, ...(netId ? { net_id: netId } : {}),
    });
    if (previewOpen()) {
      try { await closePreview(); } catch (e) { wb.toast(`The card was applied; the preview stayed open: ${e.message}`); }
    }
    return result;
  }

  // The Inspect view (GET /card, read only) of a catalog card, or of the draft (`cardId` null), kept in
  // `state.inspect` until closed or inspected again. Reads only; nothing on the server changes.
  async function inspect(cardId = null, netId = null) {
    const params = cardId ? { card_id: cardId, net_id: netId } : { draft: "true", net_id: netId };
    state.inspect = await wb.api.get(`/card?${query(params)}`);
    return state.inspect;
  }
  const closeInspect = () => { state.inspect = null; };

  // The session summary as text (GET /summary.txt).
  async function summary(netId = null) {
    const response = await wb.api.fetch(`/summary.txt?${query({ net_id: netId })}`);
    if (response.ok === false) throw new Error(`summary: ${response.status}`);
    return response.text();
  }

  return {
    state, start, changeFrom, check, serverChanged, selectionChanged, loadCatalog, catalog, catalogChanged, summary,
    anotherChange, endCreate, inspect, closeInspect,
    ...Object.fromEntries(Object.entries({
      preview, closePreview, add, cancel, undo, commit, importCard, reload, openCatalog, refreshCatalog, previewCard,
      applyCard, beginCreate, chooseCreateType, useFacility, backCreate, selectByText,
    }).map(([name, step]) => [name, oneAtATime(step)])),
  };
}
