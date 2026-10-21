// The Open menu's recent networks (pure: no DOM, no imports). `stored` is what header.js keeps in localStorage:
// `[{source, label}]`, newest first. Entries without a source are skipped, a path listed twice keeps its newest
// label, and at most `limit` entries are returned, in stored order.

const baseName = source => source.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || source;

export function recentEntries(stored, limit = 3) {
  const out = [], seen = new Set();
  for (const r of Array.isArray(stored) ? stored : []) {
    if (!r || typeof r !== "object" || !r.source) continue;
    const path = String(r.source);
    if (seen.has(path)) continue;
    seen.add(path);
    out.push({ label: r.label ? String(r.label) : baseName(path), path });
    if (out.length >= limit) break;
  }
  return out;
}
