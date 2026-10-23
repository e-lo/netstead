// Color palettes, ramps, per-vertex link colors and per-node colors for color-by.
export const FT_PALETTE = {
  motorway: [214, 69, 65], motorway_link: [242, 150, 120], trunk: [232, 119, 34], trunk_link: [245, 182, 132],
  primary: [236, 160, 20], primary_link: [249, 214, 120], secondary: [86, 158, 70], secondary_link: [160, 200, 130],
  tertiary: [56, 150, 140], tertiary_link: [150, 202, 196], residential: [120, 130, 160],
  service: [176, 184, 196], unclassified: [150, 150, 165], living_street: [150, 150, 165], road: [150, 150, 165],
};
export const CAT_PALETTE = [[228, 66, 62], [57, 126, 184], [77, 175, 74], [152, 78, 163], [255, 140, 40],
  [212, 190, 40], [166, 86, 40], [247, 129, 191], [120, 130, 160], [60, 170, 160]];
export const RAMPS = {
  YlOrRd: [[255, 237, 160], [254, 178, 76], [240, 59, 32]],
  Blues: [[222, 235, 247], [107, 174, 214], [8, 81, 156]],
  Viridis: [[68, 1, 84], [33, 145, 140], [253, 231, 37]],
};
const NULL_COLOR = [205, 208, 214];

export const rgb = c => `rgb(${c[0]},${c[1]},${c[2]})`;
export const hex2rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
export const rgb2hex = c => "#" + c.map(x => x.toString(16).padStart(2, "0")).join("");
export const fmt = v => (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10);

export function ramp(name, t) {
  const s = RAMPS[name] || RAMPS.YlOrRd, x = Math.max(0, Math.min(1, t)) * (s.length - 1);
  const i = Math.min(s.length - 2, Math.floor(x)), f = x - i, a = s[i], b = s[i + 1];
  return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * f));
}

export function catColor(prop, cat, i) {
  return (prop && prop.name === "facility_type" && FT_PALETTE[cat]) || CAT_PALETTE[i % CAT_PALETTE.length];
}

// Feature i's colour from `prop` (a categorical palette or the style's ramp), `single` without one, NULL_COLOR
// for a missing value. The same rule colours links and nodes.
function valueColor(i, style, prop, single) {
  if (!prop) return single;
  const v = prop.values[i];
  if (v === null || v === undefined) return NULL_COLOR;
  if (prop.kind === "continuous") return ramp(style.ramp, prop.max > prop.min ? (v - prop.min) / (prop.max - prop.min) : 0.5);
  return catColor(prop, v, prop.categories.indexOf(v));
}

export function buildLinkColors(net, style, prop) {
  const p = prop && style.color_by !== "none" && prop.name === style.color_by ? prop : null;
  const c = new Uint8Array(net.linkStart[net.L.count] * 4);
  for (let i = 0; i < net.L.count; i++) {
    const [r, g, b] = valueColor(i, style, p, style.colors.links);
    for (let v = net.linkStart[i]; v < net.linkStart[i + 1]; v++) { c[v * 4] = r; c[v * 4 + 1] = g; c[v * 4 + 2] = b; c[v * 4 + 3] = 222; }
  }
  return c;
}

const NODE_ALPHA = 150, NODE_ALPHA_COLORED = 215;

// Per-node RGBA for `style.node_color_by` (the node table's property, index-aligned with the buffer's nodes), or the
// single node colour without one; a coloured node is drawn more opaque so its category reads over the links.
export function buildNodeColors(net, style, prop) {
  const p = prop && style.node_color_by !== "none" && prop.name === style.node_color_by ? prop : null;
  const c = new Uint8Array(net.N.count * 4), alpha = p ? NODE_ALPHA_COLORED : NODE_ALPHA;
  for (let i = 0; i < net.N.count; i++) {
    const [r, g, b] = valueColor(i, style, p, style.colors.nodes);
    c[i * 4] = r; c[i * 4 + 1] = g; c[i * 4 + 2] = b; c[i * 4 + 3] = alpha;
  }
  return c;
}

// Semantic node styling: for these columns a category carries both a colour and a size, most important first.
export const NODE_SEMANTIC = {
  ctrl_type: {
    signal: { color: [214, 39, 40], radius: 7 },
    stop_4_way: { color: [245, 130, 32], radius: 5.5 },
    stop: { color: [240, 180, 40], radius: 4.5 },
    stop_2_way: { color: [240, 180, 40], radius: 4.5 },
    yield: { color: [250, 220, 70], radius: 4 },
    no_control: { color: [150, 155, 165, 150], radius: 2.5 },
  },
};
export const NODE_RADIUS = 2, SEMANTIC_ALPHA = 230;

// Per-node colours (RGBA) and radii (px) for `style.node_color_by`. A semantic column (NODE_SEMANTIC) maps known
// categories to their colour and size; anything else uses the generic palette at NODE_RADIUS. `order` lists node
// indices from smallest to largest radius (stable), so larger markers draw last; null when all radii are equal.
export function buildNodeStyle(net, style, prop) {
  const p = prop && style.node_color_by !== "none" && prop.name === style.node_color_by ? prop : null;
  const sem = p && NODE_SEMANTIC[p.name];
  const colors = buildNodeColors(net, style, p), n = net.N.count, radii = new Float32Array(n).fill(NODE_RADIUS);
  if (!sem) return { colors, radii, order: null };
  let uniform = true;
  for (let i = 0; i < n; i++) {
    const s = sem[p.values[i]];
    if (!s) continue;
    colors.set([s.color[0], s.color[1], s.color[2], s.color[3] === undefined ? SEMANTIC_ALPHA : s.color[3]], i * 4);
    radii[i] = s.radius;
    if (s.radius !== NODE_RADIUS) uniform = false;
  }
  if (uniform) return { colors, radii, order: null };
  const order = Uint32Array.from({ length: n }, (_, i) => i).sort((a, b) => radii[a] - radii[b] || a - b);
  return { colors, radii, order };
}

// The legend entries for a node column: [{category, color, radius}] in importance order for a semantic column
// (known categories first, signal to no_control), the generic palette (radius null: the legend's own size) otherwise.
export function nodeLegendItems(prop) {
  const sem = NODE_SEMANTIC[prop.name];
  const generic = prop.categories.map((c, i) => ({ category: c, color: catColor(prop, c, i), radius: null }));
  if (!sem) return generic;
  const rank = Object.keys(sem);
  const known = prop.categories.filter(c => sem[c]).sort((a, b) => rank.indexOf(a) - rank.indexOf(b));
  const rest = generic.filter(g => !sem[g.category]);
  return [...known.map(c => ({ category: c, color: sem[c].color.slice(0, 3), radius: sem[c].radius })), ...rest];
}

const MIN_W = 1.5, MAX_W = 10, NULL_W = 1;

// Per-vertex link widths for `style.width_by` (a continuous property), linear between MIN_W and MAX_W; null values
// draw at NULL_W. Null when not sizing by a property: the caller keeps the lane-count widths (net.linkWidths).
export function buildLinkWidths(net, style, prop) {
  if (!prop || prop.kind !== "continuous" || style.width_by !== prop.name) return null;
  const w = new Float32Array(net.linkStart[net.L.count]), span = prop.max - prop.min;
  for (let i = 0; i < net.L.count; i++) {
    const v = prop.values[i];
    const px = v === null || v === undefined ? NULL_W : MIN_W + (span > 0 ? (v - prop.min) / span : 0.5) * (MAX_W - MIN_W);
    for (let k = net.linkStart[i]; k < net.linkStart[i + 1]; k++) w[k] = px;
  }
  return w;
}

// Dim (alpha 40) the vertices of links whose id is not in `keep`; `ids[i]` is link i's id. Changes `colors` in place.
export function dimUnmatched(colors, net, ids, keep) {
  for (let i = 0; i < net.L.count; i++) {
    if (keep.has(ids[i])) continue;
    for (let k = net.linkStart[i]; k < net.linkStart[i + 1]; k++) colors[k * 4 + 3] = 40;
  }
  return colors;
}
