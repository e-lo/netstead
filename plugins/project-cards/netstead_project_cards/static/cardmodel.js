// Pure rules for the cards panel: change types, the change a form describes, the draft badge. No DOM, no wb, so
// they are unit-tested under node (tests/test_frontend_model.py).

export const ROADWAY_TYPES = ["roadway_property_change", "roadway_deletion", "roadway_addition"];
export const TRANSIT_TYPES = ["transit_property_change", "transit_routing_change", "transit_route_addition",
  "transit_service_deletion"];
export const TYPE_TITLES = {
  roadway_property_change: "Property change",
  roadway_deletion: "Deletion",
  roadway_addition: "Addition",
  transit_property_change: "Transit property change",
  transit_routing_change: "Transit routing change",
  transit_route_addition: "Transit route addition",
  transit_service_deletion: "Transit service deletion",
};

// Property changes and deletions act on the selection; an addition does not.
export const needsSelection = type => type === "roadway_property_change" || type === "roadway_deletion";

// What a change takes from the shared selection (server state's `selection`): its status, link ids and query. Not
// its network: the same links picked on the preview target the same links of the base. Not its anchors either.
export const selectionKey = sel => JSON.stringify(sel ? [sel.status, sel.link_ids, sel.projectcard] : null);

const filled = v => v !== undefined && v !== null && v !== "";
const withNote = (change, options) => (filled(options && options.note) ? { ...change, note: options.note } : change);

// The roadway_property_change a form describes. `columns` maps a column to its new value (or delta); `options` is
// {mode: "set" | "change", conflict, note}; `existing` is each column's current value when every selected record
// agrees (the /form route's `existing`). It is written as the change's `existing`, so the card fails loudly if the
// network has moved on.
export function propertyChange(columns, options = {}, existing = {}) {
  const mode = options.mode === "change" ? "change" : "set";
  const property_changes = {};
  for (const [column, value] of Object.entries(columns || {})) {
    if (!filled(value)) continue;
    let pc;
    if (mode === "change") {
      const delta = Number(value);
      if (Number.isNaN(delta)) throw new Error(`${column}: change is a number, the delta to add`);
      pc = { change: delta };
    } else {
      pc = { set: value };
    }
    if (filled(existing[column])) {
      pc.existing = existing[column];
      pc.existing_value_conflict = options.conflict || "error";
    }
    property_changes[column] = pc;
  }
  if (!Object.keys(property_changes).length) throw new Error("give a new value for at least one property");
  return withNote({ type: "roadway_property_change", property_changes }, options);
}

export function deletion(options = {}) {
  return withNote({ type: "roadway_deletion", clean_nodes: Boolean(options.clean_nodes) }, options);
}

// Whether an addition's (optional) node form describes a node: it has an id. Without one the form is ignored.
export const hasNode = node => Boolean(node && filled(node.node_id));

// A new link, and a new end node when the node form has an id. Blank fields are left out.
export function addition(link, node, options = {}) {
  const keep = record => Object.fromEntries(Object.entries(record || {}).filter(([, v]) => filled(v)));
  const nodes = hasNode(node) ? [keep(node)] : [];
  return withNote({ type: "roadway_addition", links: [keep(link)], nodes }, options);
}

// The Action for a change (`kind`: "add_change" or "preview"). `netId` is the network the form was read for (so
// the change lands where the form looked); a change that needs a selection takes the shared one, or `saved`.
export function changeAction(kind, change, { saved = null, netId = null } = {}) {
  const action = { type: `project_cards.${kind}`, change };
  if (netId) action.net_id = netId;
  if (needsSelection(change.type)) {
    if (saved) action.saved_selection = saved;
    else action.use_selection = true;
  }
  return action;
}

const cards = state => (state && state.plugins && state.plugins.project_cards) || {};

// The open preview as state() reports it ({net_id, base, describe, stale}), or null.
export const openPreview = state => cards(state).preview || null;

// The network a draft belongs to: an open preview stands for the network it previews.
export function draftNet(state, netId) {
  const preview = openPreview(state);
  return preview && preview.net_id === netId ? preview.base : netId;
}

// The draft of a network ({project, count, changes}), or null; on a preview, its base's draft.
export const draftOf = (state, netId) => (cards(state).drafts || {})[draftNet(state, netId)] || null;

// The panel's badge: the number of uncommitted changes, marked dirty (a dot on the tab and its workspace).
export function draftBadge(state, netId) {
  const draft = draftOf(state, netId);
  if (!draft || !draft.count) return null;
  return { text: String(draft.count), dirty: true, title: `${draft.count} uncommitted card change(s)` };
}

// Live edit warnings as lines, each naming its record once (core's messages usually start with it already).
export const warningLines = warnings => (warnings || []).map(w => {
  const record = `${w.table} ${w.key}: `;
  return String(w.message).startsWith(record) ? w.message : `${record}${w.message}`;
});

// The open preview as one line for the panel (state() says when its base has moved on since), or "".
export function previewLine(preview) {
  if (!preview) return "";
  const line = `Previewing ${preview.describe} on ${preview.net_id}.`;
  return preview.stale ? `${line} ${preview.base} has changed since: preview again to see it.` : line;
}

// The commit form's fields ({project, path, tags: "a, b", overwrite}) as the commit Action's, or {error}: a card needs
// a project name and a file. `netId` is the network whose draft the dialog shows, so the commit takes the same one.
export function commitFields({ project = "", path = "", tags = "", overwrite = false } = {}, netId = null) {
  const fields = {
    project: String(project).trim(),
    path: String(path).trim(),
    overwrite: Boolean(overwrite),
    tags: String(tags).split(",").map(t => t.trim()).filter(Boolean),
  };
  if (netId) fields.net_id = netId;
  const missing = [!fields.project && "a project name", !fields.path && "a file"].filter(Boolean);
  return missing.length ? { error: `Give the card ${missing.join(" and ")}` } : { fields };
}

// The import form's path as the import Action's, or {error}.
export function importPath(path = "") {
  const trimmed = String(path).trim();
  return trimmed ? { path: trimmed } : { error: "Give a card or edit log path" };
}

// What the commit dialog shows for a /commit-check answer. Any live edit warning needs the user's confirmation
// (`needsConfirm`); a schema problem means the card can't be committed yet (`blocked`).
export function commitView(check) {
  const lines = warningLines(check.warnings);
  const problems = check.problems || [];
  const n = lines.length;
  return {
    lines,
    needsConfirm: n > 0,
    blocked: problems.length > 0,
    summary: n ? `${n} live edit warning${n === 1 ? "" : "s"}. The card records the changes as they are.`
      : "No live edit warnings.",
    problems: problems.length ? `Not a valid ProjectCard yet: ${problems.join("; ")}` : "",
    button: n ? "Commit anyway" : "Commit",
  };
}
