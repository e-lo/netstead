// Decode the binary network payload from /network.bin (layout: netstead.viz.buffers.pack_network), and the
// direction-arrow geometry map.js draws over it.
export function widthForLanes(lanes) { return Math.max(1.2, Math.min(9, 0.9 + 0.8 * lanes)); }

const take = (buf, off, bytes, Ctor) => new Ctor(buf.slice(off, off + bytes));

// The point half-way along `points` ([[x, y], ...]) by length, not the middle vertex: a link with many short
// vertices at one end and one long segment at the other still gets its arrow in the visual middle. A single
// point has no midpoint (null); a zero-length path returns its first point.
export function midpointByLength(points) { const m = midpointAlong(points); return m && m.position; }

// midpointByLength plus the index of the segment (points[i] -> points[i + 1]) the midpoint lies on.
function midpointAlong(points) {
  if (points.length < 2) return null;
  const seg = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    seg.push(d); total += d;
  }
  if (total === 0) return { position: [points[0][0], points[0][1]], segment: 0 };
  let left = total / 2;
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i]) {
      const t = left / seg[i], a = points[i], b = points[i + 1];
      return { position: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], segment: i };
    }
    left -= seg[i];
  }
  const last = points.length - 1;
  return { position: [points[last][0], points[last][1]], segment: last - 1 };
}

// Travel direction along segment `i`, as deck.gl's IconLayer `getAngle` (degrees, counter-clockwise, 0 = the
// icon's own up = north).
function segmentAngle(points, i) {
  const a = points[i], b = points[i + 1];
  return -Math.atan2(b[0] - a[0], b[1] - a[1]) * 180 / Math.PI;
}

// One arrow per link row (so a two-way street gets one per direction), at its path's midpoint by length:
// `{link, position, angle, side}`. `side` is which offset line the row is drawn on: +1 (the right-hand side,
// where the map's PathStyleExtension draws a positive offset) when `offset` is on, 0 (the centreline) when
// off. Paths of fewer than two points get no arrow.
export function buildArrows(paths, { offset = false } = {}) {
  const side = offset ? 1 : 0, arrows = [];
  for (let i = 0; i < paths.length; i++) {
    const mid = midpointAlong(paths[i]);
    if (!mid) continue;
    arrows.push({ link: i, position: mid.position, angle: segmentAngle(paths[i], mid.segment), side });
  }
  return arrows;
}

// The decoded network's link paths as point arrays, in link order (the form buildArrows takes).
export function linkPaths(count, linkStart, positions) {
  const paths = new Array(count);
  for (let i = 0; i < count; i++) {
    const points = [];
    for (let v = linkStart[i]; v < linkStart[i + 1]; v++) points.push([positions[v * 2], positions[v * 2 + 1]]);
    paths[i] = points;
  }
  return paths;
}

// Where an arrow sits relative to its link's centreline, in screen pixels (+y down, deck.gl's `getPixelOffset`):
// `side * amt` path widths to the right of travel, the same line the PathStyleExtension draws the row on when
// its offset is `amt` (its unit is the path's own clamped pixel width). With `angle` as buildArrows gives it,
// the right-hand perpendicular on screen is (cos angle, -sin angle): eastbound (-90) -> (0, 1), straight down.
export function arrowPixelOffset(angle, side, widthPx, amt) {
  const r = angle * Math.PI / 180, d = side * amt * widthPx;
  return [d * Math.cos(r), -d * Math.sin(r)];
}

// Arrow height in pixels: readable at the zoom they first appear (13), growing to 18 px by zoom 18.
export function arrowSize(zoom) { return Math.max(8, Math.min(18, 8 + 2 * (zoom - 13))); }

export function decodeNetwork(buf) {
  const hlen = new DataView(buf).getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
  const L = header.links, N = header.nodes;
  let off = 4 + hlen;
  const linkPositions = take(buf, off, L.positionsBytes, Float32Array); off += L.positionsBytes;
  const linkStart = take(buf, off, L.startIndicesBytes, Uint32Array); off += L.startIndicesBytes;
  const linkIds = take(buf, off, L.idsBytes, Float64Array); off += L.idsBytes;
  const linkLanes = take(buf, off, L.lanesBytes, Uint8Array); off += L.lanesBytes;
  const nodePositions = take(buf, off, N.positionsBytes, Float32Array); off += N.positionsBytes;
  const nodeIds = take(buf, off, N.idsBytes, Float64Array);
  const id2idx = new Map(), nodeId2idx = new Map();
  for (let i = 0; i < linkIds.length; i++) id2idx.set(linkIds[i], i);
  for (let i = 0; i < nodeIds.length; i++) nodeId2idx.set(nodeIds[i], i);
  const linkWidths = new Float32Array(linkStart[L.count]);
  for (let i = 0; i < L.count; i++) {
    const w = widthForLanes(linkLanes[i]);
    for (let v = linkStart[i]; v < linkStart[i + 1]; v++) linkWidths[v] = w;
  }
  // Arrows are built by the map (map.js linkArrows): which side they sit on follows the offset toggle.
  return { L, N, linkPositions, linkStart, linkIds, linkLanes, linkWidths, nodePositions, nodeIds, id2idx, nodeId2idx };
}
