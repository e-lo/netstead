// Which map feature a pointer means when several are under it. DOM-free (tested under node).

// Plugin and overlay layers (any id but nodes/links) win, then nodes, then links. A layer that is switched off
// never wins: core's by its Show switch, an overlay by being in `hidden`.
export function pickPriority(hits, style, hidden = new Set()) {
  const rank = h => {
    if (h.layerId === "nodes") return style.show.nodes ? 1 : -1;
    if (h.layerId === "links") return style.show.links ? 2 : -1;
    return hidden.has(h.layerId) ? -1 : 0;
  };
  let best = null, bestRank = -1;
  for (const h of hits || []) {
    const r = rank(h);
    if (r >= 0 && (best === null || r < bestRank)) { best = h; bestRank = r; }
  }
  return best;
}
