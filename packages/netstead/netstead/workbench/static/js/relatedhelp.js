// The words behind the Related steps control's info popover (import-free: unit-tested under node).
// Examples use the GMNS 0.97 keys: lane.link_id, link_tod.link_id, link.from_node_id / to_node_id, lane_tod.lane_id and
// link_tod.timeday_id -> time_set_definitions. The server never revisits a table, so 2 steps reaches new tables only.

export const GENERIC = [
  "Related shows the records in other tables that your selection points at, or that point at it, through foreign keys.",
  "1 step: for a selected link 17, its two end nodes (link.from_node_id, link.to_node_id), its lanes (lane.link_id = 17) " +
    "and its time-of-day rows (link_tod.link_id = 17).",
  "2 steps: also follows those records' keys one more step, for example each lane's time-of-day rows (lane_tod.lane_id) " +
    "and the time definitions those rows use (time_set_definitions). It never loops back to a table already reached.",
];

// What the focused record or the highlights are called in the text, or "" when nothing is chosen.
export function subjectOf(focus, highlights) {
  if (focus && focus.table != null && focus.id != null) return `${focus.table} ${focus.id}`;
  const n = highlights ? highlights.length : 0;
  return n ? `${n} highlighted link${n === 1 ? "" : "s"}` : "";
}

const rowsOf = t => `${t.count.toLocaleString()} ${t.table} row${t.count === 1 ? "" : "s"}${t.partial ? " or more" : ""}`;

// The popover's paragraphs: the generic example, or, when something is focused or highlighted and the server has
// answered (`related` = POST /related, store.related), that answer's real counts, one line per step.
export function relatedExplanation({ subject = "", related = null } = {}) {
  if (!subject || !related || !related.tables) return GENERIC;
  const lines = [`For ${subject}, Related follows foreign keys like this:`];
  for (let hop = 1; hop <= related.hops; hop++) {
    const found = related.tables.filter(t => t.hop === hop);
    const text = found.length
      ? found.map(t => `${rowsOf(t)} (${t.via.join(", ")})`).join("; ")
      : "nothing more in this network";
    lines.push(`${hop === 1 ? "1 step" : "2 steps also reach"}: ${text}.`);
  }
  if (related.hops === 1) lines.push("Choose 2 steps to follow those records' keys one more step.");
  return lines;
}
