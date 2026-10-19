// The cards panel's flow without the DOM: which route each step reads and which Action it dispatches. main.js draws
// it; tests/test_frontend_model.py runs it under node with a stub `wb`. A refused Action rejects, and the step stops
// there: the form and the preview stay open, so the user can fix the form and try again. Whether a preview is open is
// read from the server's state (`openPreview(wb.store.get())`), never remembered here: the palette, the assistant or
// another tab can close one.
import { addition, changeAction, deletion, needsSelection, openPreview, propertyChange } from "./cardmodel.js";

const query = params => Object.entries(params)
  .filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  .join("&");

export function createController(wb) {
  const state = { compose: null, check: null };
  const previewOpen = () => openPreview(wb.store.get()) !== null;

  // Open the form for a change type: the plugin's /form route says what the selection holds now.
  async function start(type, { saved = null } = {}) {
    const params = { type, saved };
    if (needsSelection(type) && !saved) params.use_selection = "true";
    const form = await wb.api.get(`/form?${query(params)}`);
    if (!form.supported) {
      state.compose = null;
      wb.toast(form.message);
      return null;
    }
    state.compose = { type, form, saved };
    return form;
  }

  const openForm = () => {
    if (!state.compose) throw new Error("no change form is open");
    return state.compose;
  };

  // The change the open form describes. `values` is {columns, options, link, node}: each form's value().
  function changeFrom(values) {
    const { type, form } = openForm();
    if (type === "roadway_property_change") return propertyChange(values.columns, values.options, form.existing);
    if (type === "roadway_deletion") return deletion(values.options);
    return addition(values.link, values.node, values.options);
  }

  const dispatchChange = (kind, values) => {
    const { saved, form } = openForm();
    return wb.api.dispatch(changeAction(kind, changeFrom(values), { saved, netId: form.net_id }));
  };

  // A preview goes to a copy of the network; a second one replaces the first (the server closes the older copy).
  const preview = values => dispatchChange("preview", values);

  const closePreview = () => wb.api.dispatch({ type: "project_cards.close_preview" });

  // The change goes to the network itself (never the preview), so the preview has done its job: close it. Once the
  // change is added, add() resolves: a failure to close the preview is only reported (retrying would add it twice).
  async function add(values) {
    const result = await dispatchChange("add_change", values);
    state.compose = null;
    state.check = null;
    if (previewOpen()) {
      try { await closePreview(); } catch (e) { wb.toast(`The change was added; the preview stayed open: ${e.message}`); }
    }
    return result;
  }

  async function cancel() {
    state.compose = null;
    state.check = null;
    if (previewOpen()) await closePreview();
  }

  // `netId`: the network whose draft to undo (by default the server's choice: the active one, or a preview's base).
  function undo({ netId = null } = {}) {
    state.check = null;
    const action = { type: "project_cards.undo_change" };
    return wb.api.dispatch(netId ? { ...action, net_id: netId } : action);
  }

  // Before committing: the live edit warnings, the schema problems and the card as YAML, for the commit dialog. The
  // check stays until a commit succeeds or the draft changes (add, undo, import, cancel).
  async function check(fields) {
    state.check = await wb.api.get(`/commit-check?${query({ project: fields.project, net_id: fields.net_id })}`);
    return state.check;
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

  return { state, start, changeFrom, preview, closePreview, add, cancel, undo, check, commit, importCard };
}
