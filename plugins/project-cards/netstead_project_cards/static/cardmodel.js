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

// A new link, and a new end node when the node form has an id. Blank fields are left out.
export function addition(link, node, options = {}) {
  const keep = record => Object.fromEntries(Object.entries(record || {}).filter(([, v]) => filled(v)));
  const nodes = node && filled(node.node_id) ? [keep(node)] : [];
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

export const warningLines = warnings => (warnings || []).map(w => `${w.table} ${w.key}: ${w.message}`);
