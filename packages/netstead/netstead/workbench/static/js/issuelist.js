// Issues-panel rules. Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py).
//
// An issue's `anchor` places it on the map: {link: id} (its middle vertex), {node: id}, or {lonlat: [x, y]}.
// The server sends anchors, not coordinates; positions come from the network buffer the browser already has.

import { countOf } from "./tabs.js";

export const SEVERITIES = ["error", "warning", "info"];
export const SEVERITY_RGB = { error: [224, 82, 77], warning: [240, 170, 60], info: [90, 150, 220] };

// The /issues query string for a filter ({severity: [...], table, code, located, record: {table, id}}) and a page.
export function issuesQuery({ severity = [], table = "", code = "", located = "", record = null }, offset = 0, limit = 100) {
  const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (severity.length) q.set("severity", severity.join(","));
  if (table) q.set("table", table);
  if (code) q.set("code", code);
  if (located) q.set("located", located);
  if (record) q.set("record", `${record.table}:${record.id}`);
  return q.toString();
}

// [lon, lat] for an anchor, or null when this network buffer cannot place it.
export function anchorPosition(anchor, net) {
  if (!anchor || !net) return null;
  if (anchor.lonlat) return anchor.lonlat;
  if (anchor.node != null) {
    const i = net.nodeId2idx.get(anchor.node);
    return i == null ? null : [net.nodePositions[2 * i], net.nodePositions[2 * i + 1]];
  }
  if (anchor.link != null) {
    const i = net.id2idx.get(anchor.link);
    if (i == null) return null;
    const a = net.linkStart[i], b = net.linkStart[i + 1], k = a + Math.floor((b - a - 1) / 2);
    return [net.linkPositions[2 * k], net.linkPositions[2 * k + 1]];
  }
  return null;
}

// Map markers: [{i, severity, table, key, position}] for the issues this network can place.
export function markerPoints(markers, net) {
  const out = [];
  for (const m of markers || []) {
    const position = anchorPosition(m.anchor, net);
    if (position) out.push({ i: m.i, severity: m.severity, table: m.table, key: m.key, position });
  }
  return out;
}

// The issue numbers recorded against one record ({table: {"<key>": [i, ...]}} from /issues/markers).
export const recordIssues = (records, table, id) => (records && records[table] && records[table][String(id)]) || [];

export const rangeLabel = (offset, shown, total) => (total ? `${offset + 1}–${offset + shown} of ${total}` : "No issues");

// The last validation of the active network (session state `issues[active]`), or null.
export const issueMeta = state => (state && state.issues && state.issues[state.active]) || null;

// One line on the state of the last validation.
export function statusLine(meta) {
  if (!meta) return "Not validated yet.";
  const c = meta.counts || {};
  const parts = SEVERITIES.filter(s => c[s]).map(s => `${c[s].toLocaleString("en-US")} ${s}${c[s] === 1 || s === "info" ? "" : "s"}`);
  return `Validated v${meta.version}: ${parts.join(", ") || "no issues"}` + (meta.stale ? " · the network changed since: re-run" : "");
}

// The Issues panel's badge (Part 2 `badge(ctx)`, from `ctx.state`): validation issues plus live edit warnings on the
// active network, or null for none.
export function issuesBadge(state) {
  const meta = issueMeta(state), edits = state && state.edits && state.edits[state.active];
  const counts = { ...((meta && meta.counts) || {}) };
  counts.warning = (counts.warning || 0) + ((edits && edits.warnings) || 0);
  const n = SEVERITIES.reduce((t, k) => t + (counts[k] || 0), 0);
  if (!n) return null;
  const title = SEVERITIES.filter(k => counts[k]).map(k => (k === "info" ? `${counts[k]} info` : countOf(counts[k], k))).join(", ");
  return { text: String(n), dirty: false, title };
}
