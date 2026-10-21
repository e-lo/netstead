// The header's validity light (pure: no DOM, no imports). From the server state's `issues[active]`
// ({version, stale, counts: {error, warning, info}}) to one of five levels, the icon that shows it and its tooltip.

const SEVERITIES = ["error", "warning", "info"];

// "3 errors, 1 warning, 16 info": singular and plural right, and zero parts left out unless `keepZeros`.
function countsText(counts, keepZeros) {
  return SEVERITIES
    .filter(s => keepZeros.includes(s) || counts[s])
    .map(s => `${(counts[s] || 0).toLocaleString("en-US")} ${s}${(counts[s] || 0) === 1 || s === "info" ? "" : "s"}`)
    .join(", ");
}

export function validityState(server) {
  const meta = server && server.issues && server.active != null ? server.issues[server.active] : null;
  if (!meta) return { level: "unknown", icon: "circle", tip: "Not validated yet" };
  if (meta.stale) {
    return { level: "stale", icon: "circle", tip: `Validated v${meta.version}; the network changed since: re-run validation` };
  }
  const counts = meta.counts || {};
  if (counts.error > 0) return { level: "error", icon: "x-circle", tip: countsText(counts, []) };
  if (counts.warning > 0) return { level: "warn", icon: "alert-triangle", tip: countsText(counts, []) };
  return { level: "clean", icon: "check-circle", tip: `Valid: ${countsText(counts, ["error", "warning"])}` };
}
