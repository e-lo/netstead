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

// Which export forms apply, and why not (matches GET /api/selection/export's 404/409 rules).
export function exportChoices(sel) {
  if (!sel) return { resolved: false, query: false, reason: "Nothing is selected." };
  if (sel.status === "ambiguous") return { resolved: false, query: false, reason: "Pin the ambiguous anchors first." };
  if (sel.status !== "resolved") return { resolved: false, query: false, reason: "Nothing was found to export." };
  return { resolved: true, query: sel.intent != null, reason: "" };
}

// The Selection tab's badge: "?" while an anchor is ambiguous, else the link count; none without a selection.
export function selectionBadge(sel) {
  if (!sel) return null;
  return sel.status === "ambiguous" ? "?" : sel.link_ids.length;
}
