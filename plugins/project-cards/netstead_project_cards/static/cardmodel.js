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

// ---------------------------------------------------------------------------------------------------- the catalog

// The open catalog as state() reports it ({path, cards, rejected}), or null.
export const catalogOf = state => cards(state).catalog || null;

// Whether applying catalog cards is on (`[plugins.project_cards] enable_apply`; off by default).
export const applyEnabled = state => Boolean(cards(state).enable_apply);

// The first few ids, and how many more (as the server's `few`).
export function fewIds(ids, n = 5) {
  const items = [...(ids || [])];
  const shown = items.slice(0, n).join(", ");
  return items.length > n ? `${shown} (+${items.length - n} more)` : shown;
}

// Every id a card's check found missing, across its changes.
export const missingIds = applicability => ((applicability && applicability.changes) || []).flatMap(c => c.missing || []);

// A catalog card's status pill, from the server's `fit` (catalog.fit: {state, reasons, warnings, net_id}): `fit` is
// "fits" (green "Fits this network"), "no" (red "Doesn't fit", `reason` its first reason, shown inline) or
// "unchecked" (gray "Not checked"); `tip` lists every reason, then the warnings. An entry without `fit` (an older
// server) gets one from its applicability.
export function fitPill(entry) {
  const a = (entry && entry.applicability) || null;
  const f = (entry && entry.fit) || { state: a ? (a.ok ? "fits" : "no") : "unchecked", reasons: [], warnings: [],
    net_id: a ? a.net_id : null };
  const warnings = f.warnings || [];
  if (f.state === "fits") {
    return { fit: "fits", label: "Fits this network", reason: "", tip: [`Fits ${f.net_id}`, ...warnings].join("\n") };
  }
  if (f.state === "no") {
    const reasons = f.reasons && f.reasons.length ? f.reasons : ["does not apply"];
    return { fit: "no", label: "Doesn't fit", reason: reasons[0], tip: [...reasons, ...warnings].join("\n") };
  }
  return { fit: "unchecked", label: "Not checked", reason: "", tip: "Not checked against this network yet" };
}

// The network a listing's cards were checked against (the first checked card's), or null.
export function checkedAgainst(catalog) {
  const checked = ((catalog && catalog.cards) || []).find(c => (c.fit && c.fit.net_id) || c.applicability);
  if (!checked) return null;
  return (checked.fit && checked.fit.net_id) || checked.applicability.net_id || null;
}

// The catalog's cards as the panel lists them: one row per card, with its status pill, in the listing's order.
// `catalog` is the listing to show (by default the one in `state`): the /catalog route's answer carries fresher checks.
export function catalogRows(state, catalog = catalogOf(state)) {
  return ((catalog && catalog.cards) || []).map(entry => ({
    card_id: entry.card_id,
    path: entry.path,
    project: entry.project || entry.card_id,
    changes: entry.change_count || 0,
    tags: entry.tags || [],
    applicability: entry.applicability || null,
    ...fitPill(entry),
  }));
}

// What the Apply… dialog starts with: conflicts are errors (as a card says), and missing ids are ignored only when
// the check found some (a card refused for a reason is not helped by ignoring ids).
export function applyDefaults(entry) {
  const applicability = entry ? entry.applicability : null;
  const ignore = Boolean(applicability && !applicability.ok && missingIds(applicability).length > 0);
  return { existing_value_conflict: "error", ignore_missing: ignore };
}

// "2026-10-21T14:03:00+00:00" -> "14:03" (the applied list shows the time of day; the summary has the full stamp).
export const clockTime = iso => {
  const m = /T(\d{2}:\d{2})/.exec(String(iso || ""));
  return m ? m[1] : String(iso || "");
};

// The icon each kind of session entry shows (core's names, or the panel's own: rename, trash, tag).
export const SESSION_ICONS = { saved: "save", applied: "check-circle", renamed: "rename", duplicated: "copy",
  deleted: "trash", restored: "undo", updated: "tag" };

// The "This session" list: what this session did with cards (saved, applied when apply is on, a catalog file
// renamed, duplicated, deleted, restored or its metadata edited), newest first as state() lists them, with the time
// of day and the icon for its kind.
export function sessionRows(state) {
  return (cards(state).session || []).map(r => ({
    kind: r.kind,
    icon: SESSION_ICONS[r.kind] || "info",
    text: r.text,
    detail: r.detail || "",
    card_id: r.card_id,
    at: r.at,
    time: clockTime(r.at),
  }));
}

// The file name the session summary downloads as.
export const summaryFileName = netId => `${netId || "network"}-cards-session.txt`;

// ------------------------------------------------------------------------------------------------- creation mode

// The guided "New card" mode: a change type, then the facility (the shared selection, or a saved one), then the
// change form, then Add to card. It is the panel's state, never the server's: `{step, type, saved}`, where `step` is
// "type", "select", "details" (the form is open), "add" (a preview of the form's change is on the map) or "added".
export const CREATE_STEPS = [["type", "Type"], ["select", "Select"], ["details", "Details"], ["add", "Add"]];

export const startCreate = () => ({ step: "type", type: null, saved: null });

// The change types the mode offers, in order: the roadway ones, then the transit ones (shown, not offered yet).
export const typeChoices = () => [
  ...ROADWAY_TYPES.map(type => ({ type, title: TYPE_TITLES[type], enabled: true, note: "" })),
  ...TRANSIT_TYPES.map(type => ({ type, title: TYPE_TITLES[type], enabled: false, note: "not yet supported" })),
];

// After picking `type`: a change that acts on a selection goes to "select", an addition straight to "details".
export function chooseType(mode, type) {
  if (TRANSIT_TYPES.includes(type)) throw new Error(`${TYPE_TITLES[type]} is not yet supported`);
  if (!ROADWAY_TYPES.includes(type)) throw new Error(`no change type ${type}`);
  return { ...mode, type, saved: null, step: needsSelection(type) ? "select" : "details" };
}

// The mode after `event`: "form" (the form opened, for the shared selection or the saved one `saved`), "previewed",
// "added", or "back" (one step back: from the form to Select, or to Type for an addition; from Select to Type).
export function createAfter(mode, event, saved = null) {
  if (event === "form") return { ...mode, step: "details", saved };
  if (event === "previewed") return { ...mode, step: "add" };
  if (event === "added") return { ...mode, step: "added" };
  if (event === "back") {
    if ((mode.step === "details" || mode.step === "add") && needsSelection(mode.type)) {
      return { ...mode, step: "select", saved: null };
    }
    return startCreate();
  }
  throw new Error(`no creation step event ${event}`);
}

// The step indicator: each step's label and state ("done", "current", "todo", or "skipped": an addition selects
// nothing). "added" marks every step done.
export function stepIndicator(mode) {
  const order = CREATE_STEPS.map(([key]) => key);
  const at = mode.step === "added" ? order.length : order.indexOf(mode.step);
  return CREATE_STEPS.map(([key, label], i) => {
    if (key === "select" && mode.type && !needsSelection(mode.type)) return { key, label, state: "skipped" };
    return { key, label, state: i < at ? "done" : i === at ? "current" : "todo" };
  });
}

// What the Select step says about the shared selection (server state's `selection`) for the network `netId` (or the
// base a preview stands for): `{count, ready, label}`. Only a resolved selection on that network can be used.
export function selectionStatus(state, netId) {
  const sel = state && state.selection;
  const nets = [netId, draftNet(state, netId)];
  if (!sel || !nets.includes(sel.net_id) || !(sel.link_ids || []).length) {
    return { count: 0, ready: false, label: "Nothing selected on this network yet." };
  }
  const count = sel.link_ids.length;
  const links = `${count} link${count === 1 ? "" : "s"}`;
  if (sel.status !== "resolved") {
    return { count, ready: false, label: `${links} selected, not resolved yet: pin its anchors in the Selection panel.` };
  }
  return { count, ready: true, label: `${links} selected.` };
}

// The saved selections a Select step offers: those of the network (or of the base a preview stands for).
export const savedChoices = (state, netId) => ((state && state.saved_selections) || [])
  .filter(s => s.net_id === netId || s.net_id === draftNet(state, netId))
  .map(s => ({ name: s.name, label: `${s.name} (${s.links} link${s.links === 1 ? "" : "s"})` }));

// --------------------------------------------------------------------------------------------- editing a card

// The last part of a path ("/cards/main.yml" -> "main.yml").
export const baseName = path => String(path || "").split(/[\\/]/).filter(Boolean).pop() || "";

// The commit dialog's buttons for `fields` (commitFields'): one Commit, or, for a draft loaded from a card file
// (`source`, {card_id, path}), "Save over <file>" (that file, replaced) and "Save as new file" (the Card file field,
// never replacing the source: `disabled` says why when the field still names it).
export function commitChoices(fields, source = null) {
  if (!source || !source.path) return [{ key: "commit", label: null, fields, disabled: "" }];
  const over = { key: "over", label: `Save over ${baseName(source.path)}`, fields: { ...fields, path: source.path,
    overwrite: true }, disabled: "" };
  // The field names the source itself, absolutely or as a path relative to a folder above it (the server refuses
  // any other existing file without overwrite anyway).
  const same = fields.path === source.path || String(source.path).endsWith(`/${fields.path}`);
  const asNew = { key: "new", label: "Save as new file", fields: { ...fields, overwrite: false },
    disabled: same ? "Give a different card file to save as a new card" : "" };
  return [over, asNew];
}

// What Edit on a catalog card asks first: nothing for an empty draft, else whether to replace the draft's changes.
export function editPrompt(draft, row) {
  const n = draft ? draft.count || 0 : 0;
  const warn = row && row.fit === "no" ? ` ${row.project} does not fit this network as it is: ${row.tip}` : "";
  if (!n) return { replace: false, ask: warn ? warn.trim() : "" };
  const changes = `${n} uncommitted change${n === 1 ? "" : "s"}`;
  return { replace: true, ask: `The draft card has ${changes}. Edit undoes ${n === 1 ? "it" : "them"} (network edits `
    + `included) and loads ${row ? row.project : "the card"} instead.${warn}` };
}

// ------------------------------------------------------------------------------------------ editing the catalog

// "a, b,, c" -> ["a", "b", "c"].
export const splitList = text => String(text || "").split(",").map(t => t.trim()).filter(Boolean);

// The Edit metadata form ({project, tags, prerequisites, corequisites, conflicts}, comma-separated lists) as the
// update_card_meta Action's fields, or {error}: a card needs a project name. Every field is sent, so a cleared one
// is cleared in the file.
export function metaFields(form = {}) {
  const project = String(form.project || "").trim();
  if (!project) return { error: "Give the card a project name" };
  const dependencies = {};
  for (const key of ["prerequisites", "corequisites", "conflicts"]) dependencies[key] = splitList(form[key]);
  return { fields: { project, tags: splitList(form.tags), dependencies } };
}

// The Edit metadata form's start, from the /card route's answer.
export const metaForm = detail => ({
  project: detail.project || "",
  tags: (detail.tags || []).join(", "),
  ...Object.fromEntries(["prerequisites", "corequisites", "conflicts"]
    .map(k => [k, ((detail.dependencies || {})[k] || []).join(", ")])),
});

// The rename form's new name, or {error}.
export function renameName(name, cardId) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return { error: "Give the card a new file name" };
  if (trimmed === cardId) return { error: `${cardId} already has that name` };
  return { name: trimmed };
}

// What the panel says after a catalog file Action (its result {action, card_id, to}).
export function fileLine(result) {
  if (result.action === "renamed") return `Renamed ${result.card_id} to ${result.to}`;
  if (result.action === "duplicated") return `Duplicated ${result.card_id} as ${result.to}`;
  if (result.action === "deleted") return `Moved ${result.card_id} to the catalog's ${result.to} folder`;
  if (result.action === "restored") return `Restored ${result.card_id}`;
  return `Saved ${result.card_id}'s ${result.to}`;
}

// ------------------------------------------------------------------------------------------------- inspecting

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// The Inspect view's heading lines (the /card route's answer): [label, text] pairs, the empty ones left out.
export function inspectHeader(detail) {
  const deps = detail.dependencies || {};
  const depText = ["prerequisites", "corequisites", "conflicts"]
    .filter(k => (deps[k] || []).length).map(k => `${k}: ${deps[k].join(", ")}`).join("; ");
  return [
    ["Project", detail.project || "(unnamed)"],
    ["Tags", (detail.tags || []).join(", ")],
    ["Dependencies", depText],
    ["Notes", detail.notes || ""],
    ["File", detail.path || ""],
  ].filter(([, text]) => text);
}

// A query selection as one line: its fields (without ignore_missing), and what it resolved to when made.
function queryText(facility) {
  const links = { ...((facility.query || {}).links || {}) };
  delete links.ignore_missing;
  const resolved = facility.resolved ? `resolved to ${plural(facility.resolved.length, "link")}`
    : "no provenance: cannot be applied yet";
  return `links matching ${JSON.stringify(links)} (${resolved})`;
}

// What a change aims at, as one line.
export function facilityText(change) {
  if (change.type === "roadway_property_change") {
    const f = change.facility;
    if (!f) return "no facility";
    return f.query ? queryText(f) : `${f.table === "node" ? "nodes" : "links"} ${fewIds(f.ids)}`;
  }
  const parts = [];
  if ((change.links || []).length) {
    const ids = change.type === "roadway_addition" ? change.links.map(r => r.link_id) : change.links;
    parts.push(`links ${fewIds(ids)}`);
  }
  if ((change.nodes || []).length) {
    const ids = change.type === "roadway_addition" ? change.nodes.map(r => r.node_id) : change.nodes;
    parts.push(`nodes ${fewIds(ids)}`);
  }
  const verb = change.type === "roadway_addition" ? "adds" : change.type === "roadway_deletion" ? "deletes" : "";
  const extra = change.clean_nodes ? " (and end nodes no other link uses)" : "";
  return `${verb} ${parts.join("; ") || "nothing"}${extra}`.trim();
}

// A property change's properties, one line each, existing → set or change: "lanes: 2 → 3", "lanes: 2 → change by
// +1"; without an existing value, "lanes: change by +1" or "name: set Main St".
export function propertyLines(change) {
  return Object.entries(change.property_changes || {}).map(([name, pc]) => {
    const has = v => v !== undefined && v !== null;
    const delta = has(pc.change) ? `${pc.change >= 0 ? "+" : ""}${pc.change}` : null;
    const conflict = has(pc.existing) && pc.existing_value_conflict && pc.existing_value_conflict !== "error"
      ? ` (if it differs: ${pc.existing_value_conflict})` : "";
    if (!has(pc.existing)) return delta !== null ? `${name}: change by ${delta}` : `${name}: set ${pc.set}`;
    const after = delta !== null ? `change by ${delta}` : pc.set;
    return `${name}: ${pc.existing} → ${after}${conflict}`;
  });
}

// One change's check on the open network, as a line and its state: "ok", "no" or "unknown". A draft's changes are
// on its network already (`draft`).
export function checkText(check, { draft = false, netId = null } = {}) {
  if (draft) return { state: "ok", text: `In the draft: applied to ${netId}` };
  if (!check) return { state: "unknown", text: "Not checked: no network is open" };
  if (check.reason) return { state: "no", text: `Does not apply: ${check.reason}` };
  if ((check.missing || []).length) {
    return { state: "no", text: `${check.resolved} found; missing ${fewIds(check.missing)}` };
  }
  return { state: "ok", text: `Applies: ${plural(check.resolved, "record")} found` };
}

// The Inspect view's rows: one per change, with what to highlight on the map when it is clicked.
export function inspectRows(detail) {
  const draft = detail.source === "draft";
  return (detail.changes || []).map(change => ({
    index: change.index,
    title: `${change.index + 1}. ${TYPE_TITLES[change.type] || change.type}`,
    facility: facilityText(change),
    properties: propertyLines(change),
    note: change.note || "",
    check: checkText(change.check, { draft, netId: detail.net_id }),
    links: [...((change.targets && change.targets.links) || [])],
  }));
}
