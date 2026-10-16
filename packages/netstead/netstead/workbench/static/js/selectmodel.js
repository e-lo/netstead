// Selection verification rules: anchor rows with candidates, the candidate markers, what can be exported, and the
// panel badge. Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).

// Anchors placed on the map, then those whose node has no coordinates (marked `unplaced`).
const anchorsOf = sel => (sel ? [...(sel.anchors || []), ...(sel.anchors_unplaced || []).map(a => ({ ...a, unplaced: true }))] : []);
const isPlaced = c => c.lon != null && c.lat != null;

// One row per resolved anchor: its node, how it matched, whether it is pinned, and the other nodes it could be.
export function anchorRows(sel) {
  return anchorsOf(sel).map(a => ({
    role: a.role, nodeId: a.node_id, kind: a.kind, detail: a.detail, anchor: a.anchor || "",
    pinned: Boolean(a.pinned), canUnpin: Boolean(a.pinned), unplaced: Boolean(a.unplaced),
    candidates: (a.candidates || []).map(c => ({ nodeId: c.node_id, placed: isPlaced(c) })),
  }));
}

// Candidate nodes with coordinates, for the map's "Anchor candidates" layer.
export function candidatePoints(sel) {
  return anchorsOf(sel).flatMap(a => (a.candidates || []).filter(isPlaced)
    .map(c => ({ role: a.role, node_id: c.node_id, lon: c.lon, lat: c.lat })));
}

// The anchors whose pinned node an edit took off the facility (the payload's `stale_pins`): the selection kept its
// previous links and its `resolved` status, but its query no longer gives them.
export function staleAnchors(sel) {
  const stale = (sel && sel.stale_pins) || [], intent = (sel && sel.intent) || {};
  return ["from", "to"].filter(role => intent[`${role}_node`] != null && stale.includes(intent[`${role}_node`]))
    .map(role => ({ role, nodeId: intent[`${role}_node`] }));
}

// The Selection panel's warning for a stale pin, one line per anchor ("" when none is stale).
export function staleNotes(sel) {
  return staleAnchors(sel).map(a => `The ${a.role} anchor's pinned node ${a.nodeId} is no longer on the facility. ` +
    `These are the links selected before the edit, not what the query gives now: re-pin the ${a.role} anchor, or Unpin it.`);
}

// Which export forms apply, and why not (matches GET /api/selection/export's 404/409 rules).
export function exportChoices(sel) {
  if (!sel) return { resolved: false, query: false, reason: "Nothing is selected." };
  if (sel.status === "ambiguous") return { resolved: false, query: false, reason: "Pin the ambiguous anchors first." };
  if (sel.status !== "resolved") return { resolved: false, query: false, reason: "Nothing was found to export." };
  const stale = staleAnchors(sel).map(a => a.role);
  if (stale.length) {
    return { resolved: true, query: false, reason: `A pin has gone stale: the query form needs the ${stale.join(" and ")} anchor re-pinned.` };
  }
  return { resolved: true, query: sel.intent != null, reason: "" };
}

// The Selection tab's badge: "?" while an anchor is ambiguous, else the link count; none without a selection.
export function selectionBadge(sel) {
  if (!sel) return null;
  return sel.status === "ambiguous" ? "?" : sel.link_ids.length;
}
