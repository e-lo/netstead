// Grouping of a page of validation findings for the Validate panel, mirroring corral.reports.grouping.group_issues:
// two or more findings sharing a code and severity, in the structural category and without a row, fold into one
// collapsible row; every other finding stands alone. Import-free and DOM-free: unit-tested under node
// (tests/test_workbench_js.py).

export const SUMMARY_LIMIT = 8;

// The lead-in per structural code when a group is summarized on one line (corral's _GROUP_LEAD_IN).
const LEAD_IN = {
  "structural.missing_optional_resource": "optional resources absent: ",
  "structural.missing_required_resource": "required resources missing: ",
  "structural.unexpected_resource": "undeclared resources found: ",
};

// Whether a finding can join a group: the structural category (the server sends `category`; the code's prefix says
// the same for older payloads) and no row, so its message differs from its siblings' only by the resource it names.
export const groupable = issue => Boolean(issue) && issue.row == null &&
  (issue.category === "structural" || (!issue.category && typeof issue.code === "string" && issue.code.startsWith("structural.")));

// `issues` (as GET /issues returns them, in order) -> [{group: true, code, severity, count, tables, issues} |
// {group: false, issue}], in the order each entry's first finding appeared.
export function groupIssues(issues) {
  const byKey = new Map(), order = [];
  for (const issue of issues || []) {
    if (!groupable(issue)) { order.push({ issue }); continue; }
    const key = `${issue.code}\u0000${issue.severity}`;
    if (!byKey.has(key)) { byKey.set(key, []); order.push({ key }); }
    byKey.get(key).push(issue);
  }
  return order.map(entry => {
    if (entry.issue) return { group: false, issue: entry.issue };
    const members = byKey.get(entry.key);
    if (members.length < 2) return { group: false, issue: members[0] };
    return { group: true, code: members[0].code, severity: members[0].severity, count: members.length,
      tables: members.map(i => i.table).filter(t => t != null), issues: members };
  });
}

// One line for a group: its lead-in plus at most `limit` resource names, then "(+N more)".
export function groupSummary(group, limit = SUMMARY_LIMIT) {
  const names = group.tables || [];
  let shown = names.slice(0, limit).join(", ");
  if (names.length > limit) shown += ` (+${names.length - limit} more)`;
  return (LEAD_IN[group.code] || `${group.count} findings on: `) + shown;
}

// Whether a finding has somewhere on the map to zoom to: an anchor the issue payload carries.
export const located = issue => Boolean(issue && issue.anchor &&
  (issue.anchor.link != null || issue.anchor.node != null || Array.isArray(issue.anchor.lonlat)));

// ---- the Validate list's default grouping: one collapsible row per finding type (code + severity). Built from
// GET /issues/summary (the whole set, never a page of it); a group's members load on expand.

export const MEMBER_PAGE = 50;
const RANK = { error: 0, warning: 1, info: 2 };

// Summary groups ordered by severity (error, warning, info), then count (largest first), then code. The server
// sends them this way already; sorting again keeps the order a property of this module, not of the wire.
export const orderGroups = groups =>
  [...(groups || [])].sort((a, b) => (RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3) || b.count - a.count || a.code.localeCompare(b.code));

// A group's key: its code and severity (the same code can appear at two severities).
export const typeKey = g => `${g.code}\u0000${g.severity}`;

// "quality.high_speed_residential · warning · 271"
export const groupTitle = g => `${g.code} · ${g.severity} · ${g.count.toLocaleString("en-US")}`;

// The line under a group's title: structural groups name their resources (as before); every other type shows the
// rule's fix hint once, which would otherwise repeat on each of its members.
export function groupBlurb(g) {
  if (typeof g.code === "string" && g.code.startsWith("structural.")) return groupSummary({ ...g, tables: g.tables || [] });
  return g.fix_hint || "";
}

// The members still to load for a group that has `loaded` of its `count`.
export const remaining = (count, loaded) => Math.max(0, count - loaded);

// "287 findings in 2 types", "1 finding in 1 type", "No findings".
export function totalsLine(total, groups) {
  if (!total) return "No findings";
  const n = (groups || []).length;
  return `${total.toLocaleString("en-US")} ${total === 1 ? "finding" : "findings"} in ${n} ${n === 1 ? "type" : "types"}`;
}

// A Type select's option label: "quality.high_speed_residential (271)".
export const typeOptionLabel = (code, n) => `${code} (${n.toLocaleString("en-US")})`;
