// deck.gl-over-MapLibre network rendering, picking, and camera moves.
import { $, esc } from "./dom.js";
import { createClickGuard } from "./gesture.js";
import { layerRegistry } from "./layers.js";
import { arrowPixelOffset, arrowSize, buildArrows, linkPaths, nodeRadiusScale, widthForLanes } from "./netbuf.js";
import { buildLinkColors, buildLinkWidths, buildNodeStyle, dimUnmatched } from "./palette.js";
import { commandContext, frozenCopy } from "./commands.js";
import { CORE } from "./slots.js";
import { activeSelection, store } from "./store.js";

const OFFSET_EXT = typeof deck.PathStyleExtension === "function" ? new deck.PathStyleExtension({ offset: true }) : null;
// "Offset directions" draws each link row this many path widths to its right (PathStyleExtension's unit is the
// row's own clamped pixel width, so LINK_MIN_PX is part of that width); the row's arrow is placed on the same line.
const OFFSET_AMT = 0.8, LINK_MIN_PX = 1, ARROW_ZOOM = 13;
const HIGHLIGHT_COLOR = [45, 210, 230];
const FOCUS_COLOR = [255, 0, 200, 240];
const RELATED_COLOR = [45, 210, 230, 110]; // the highlight hue, lighter
// Under the focus and highlight strokes, so they read on the light Positron basemap and over white highway casings.
const CASING_COLOR = [20, 20, 30, 220];
// A white arrowhead with a dark outline, for the same reason as the casing.
const ARROW_SVG = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">' +
  '<polygon points="12,2 21,22 12,16.5 3,22" fill="white" stroke="#14141e" stroke-width="1.5" stroke-linejoin="round"/></svg>');
const TOOLTIP_STYLE = { background: "#11151a", color: "#e6e8ec", fontSize: "12px", padding: "6px 8px",
  borderRadius: "6px", border: "1px solid #2a2f3a" };

let map = null, overlay = null;
let handlers = { onLinkClick() {}, onNodeClick() {}, onBoxSelect() {}, onContextMenu() {}, onLayerError(e) { console.error(e); } };
let colorCache = { key: null, colors: null }, widthCache = { key: null, widths: null }, nodeCache = { key: null };
let pathsCache = { key: null, paths: null }, arrowCache = { key: null, arrows: null };
let labelsShown = true;
// Whether the current style's layers exist (style.load fired). Not map.isStyleLoaded(): that also waits for
// every tile source, so a label toggle right after a basemap swap would be dropped.
let styleReady = false;

export const hasOffset = () => OFFSET_EXT !== null;

export function initMap(style, hooks) {
  handlers = { ...handlers, ...hooks };
  map = new maplibregl.Map({ container: "map", style, center: [-98.5, 39.8], zoom: 3 });
  map.addControl(new maplibregl.NavigationControl(), "top-left");
  overlay = new deck.MapboxOverlay({ interleaved: false, layers: [], getTooltip });
  map.addControl(overlay);
  wireContextMenu();
  new ResizeObserver(() => map.resize()).observe($("map"));
  map.on("zoomend", () => { if (store.get().server) render(); }); // arrows and node sizes follow the zoom
  // Ready once the style is parsed, not on "load": that waits for every basemap tile of the opening
  // (continental) view, so the network would be drawn and fitted seconds late, or never offline.
  map.on("style.load", () => { styleReady = true; });
  map.once("style.load", hooks.onReady);
  wireBoxSelect();
}

// A right-click (or Ctrl+click on a Mac) on a feature opens its menu, but never at the end of a right-drag rotate.
function wireContextMenu() {
  const guard = createClickGuard();
  map.getCanvasContainer().addEventListener("mousedown", e => guard.down(e.clientX, e.clientY), true);
  window.addEventListener("mousemove", e => guard.move(e.clientX, e.clientY), true);
  window.addEventListener("mouseup", e => { const open = guard.up(e.clientX, e.clientY); if (open) open(); }, true);
  map.on("contextmenu", e => {
    const target = recordAt(e.point);
    if (!target) return;
    const at = { x: e.originalEvent.clientX, y: e.originalEvent.clientY };
    const open = guard.menu(() => handlers.onContextMenu(target, at));
    if (open) open();
  });
}

export function resizeSoon() { if (map) setTimeout(() => map.resize(), 60); }

// Per-vertex link colours; links the recorded Filter does not match are dimmed. The Set is replaced (never changed)
// when the filter or the network changes, so it can key the cache.
let filterSeq = 0, lastFilterIds = null;
function linkColors(s) {
  const st = s.server.style;
  if (s.filterIds !== lastFilterIds) { lastFilterIds = s.filterIds; filterSeq++; }
  const key = JSON.stringify([s.netKey, st.color_by, st.ramp, st.colors.links, s.prop && s.prop.name, filterSeq]);
  if (colorCache.key !== key) {
    const colors = buildLinkColors(s.net, st, s.prop);
    colorCache = { key, colors: s.filterIds ? dimUnmatched(colors, s.net, s.attrs.link_id, s.filterIds) : colors };
  }
  return colorCache.colors;
}

// Per-node colours and radii (semantic for ctrl_type), with positions in draw order (larger markers last), cached
// like the links'. `order` maps a drawn index back to the node's index (null: the same).
function nodeStyle(s) {
  const st = s.server.style, np = s.nodeProp;
  const key = JSON.stringify([s.netKey, st.node_color_by, st.ramp, st.colors.nodes, np && np.name, np && np.netKey]);
  if (nodeCache.key !== key) {
    const { colors, radii, order } = buildNodeStyle(s.net, st, np), pos = s.net.nodePositions;
    if (!order) nodeCache = { key, colors, radii, positions: pos, order };
    else {
      const positions = new Float64Array(pos.length), c = new Uint8Array(colors.length), r = new Float32Array(radii.length);
      order.forEach((src, k) => { positions.set(pos.subarray(src * 2, src * 2 + 2), k * 2); c.set(colors.subarray(src * 4, src * 4 + 4), k * 4); r[k] = radii[src]; });
      nodeCache = { key, colors: c, radii: r, positions, order };
    }
  }
  return nodeCache;
}
const nodeIndex = i => (nodeCache.order ? nodeCache.order[i] : i);

// Per-vertex widths for `style.width_by`, or null (the lane-count widths).
function linkWidths(s) {
  const st = s.server.style, wp = s.widthProp;
  const key = JSON.stringify([s.netKey, st.width_by, wp && wp.name, wp && wp.netKey]);
  if (widthCache.key !== key) widthCache = { key, widths: buildLinkWidths(s.net, st, wp) };
  return widthCache.widths;
}

// One direction arrow per link row, on the line the row is drawn on (offset on: its right-hand line).
function linkArrows(s, offsetOn) {
  const net = s.net;
  if (pathsCache.key !== s.netKey) pathsCache = { key: s.netKey, paths: linkPaths(net.L.count, net.linkStart, net.linkPositions) };
  const key = JSON.stringify([s.netKey, offsetOn]);
  if (arrowCache.key !== key) arrowCache = { key, arrows: buildArrows(pathsCache.paths, { offset: offsetOn }) };
  return arrowCache.arrows;
}

function baseLayers(s) {
  const net = s.net, style = s.server.style, colors = linkColors(s), widths = linkWidths(s) || net.linkWidths;
  const layers = [];
  if (style.show.links) {
    const offsetOn = Boolean(OFFSET_EXT && style.offset);
    const props = { id: "links", _pathType: "open",
      data: { length: net.L.count, startIndices: net.linkStart,
        attributes: { getPath: { value: net.linkPositions, size: 2 }, getWidth: { value: widths, size: 1 },
                      getColor: { value: colors, size: 4 } } },
      widthUnits: "pixels", widthMinPixels: LINK_MIN_PX, capRounded: true, jointRounded: true,
      pickable: true, autoHighlight: true, highlightColor: [255, 140, 59, 235],
      onClick: info => { if (info && info.index >= 0) handlers.onLinkClick(store.get().attrs.link_id[info.index]); } };
    if (OFFSET_EXT) { props.extensions = [OFFSET_EXT]; props.getOffset = offsetOn ? OFFSET_AMT : 0; }
    layers.push(new deck.PathLayer(props));
    const zoom = map.getZoom();
    if (style.show_direction && zoom >= ARROW_ZOOM) {
      // A row's drawn width in pixels, as the PathLayer clamps it (widths are per vertex; a row's vertices share one).
      const widthPx = d => Math.max(LINK_MIN_PX, widths[net.linkStart[d.link]]);
      layers.push(new deck.IconLayer({ id: "arrows", data: linkArrows(s, offsetOn),
        getIcon: () => ({ url: ARROW_SVG, width: 24, height: 24, anchorY: 12 }),
        getPosition: d => d.position, getAngle: d => d.angle,
        getPixelOffset: d => arrowPixelOffset(d.angle, d.side, widthPx(d), OFFSET_AMT),
        getSize: arrowSize(zoom), sizeUnits: "pixels", getColor: [255, 255, 255, 240], pickable: false,
        updateTriggers: { getPixelOffset: [offsetOn, widthCache.key] } }));
    }
  }
  // Nodes draw above links, and are pickable, so a map click can focus a node. Their colours come from the node
  // table's `node_color_by` column (nodeColors), the single node colour without one.
  if (style.show.nodes) layers.push(new deck.ScatterplotLayer({ id: "nodes",
    data: { length: net.N.count, attributes: { getPosition: { value: nodeStyle(s).positions, size: 2 },
                                               getFillColor: { value: nodeStyle(s).colors, size: 4 },
                                               getRadius: { value: nodeStyle(s).radii, size: 1 } } },
    radiusUnits: "pixels", radiusScale: nodeRadiusScale(map.getZoom()), radiusMinPixels: 1.5,
    stroked: true, filled: true, lineWidthMinPixels: 1, lineWidthUnits: "pixels",
    getLineColor: [20, 20, 30, 220],
    pickable: true, autoHighlight: true, highlightColor: [255, 140, 59, 235],
    onClick: info => { if (info && info.index >= 0) handlers.onNodeClick(net.nodeIds[nodeIndex(info.index)]); } }));
  return layers;
}

// A highlight PathLayer over a set of link ids (shared by selection + highlights).
function idPathLayer(net, layerId, ids, color, extraWidth) {
  const positions = [], startIndices = [0], widths = [];
  for (const id of ids) {
    const i = net.id2idx.get(id);
    if (i == null) continue;
    const w = widthForLanes(net.linkLanes[i]) + extraWidth;
    for (let k = net.linkStart[i] * 2; k < net.linkStart[i + 1] * 2; k += 2) {
      positions.push(net.linkPositions[k], net.linkPositions[k + 1]); widths.push(w);
    }
    startIndices.push(positions.length / 2);
  }
  if (startIndices.length < 2) return null;
  return new deck.PathLayer({ id: layerId, _pathType: "open",
    data: { length: startIndices.length - 1, startIndices: new Uint32Array(startIndices),
      attributes: { getPath: { value: new Float32Array(positions), size: 2 }, getWidth: { value: new Float32Array(widths), size: 1 } } },
    getColor: color, widthUnits: "pixels", widthMinPixels: 3, capRounded: true, jointRounded: true,
    parameters: { depthTest: false } });
}

// idPathLayer twice: a 2 px wider dark casing under the coloured stroke, so the focus and highlights stay legible
// whatever the basemap draws beneath them.
function casedPathLayers(net, layerId, ids, color, extraWidth) {
  const stroke = idPathLayer(net, layerId, ids, color, extraWidth);
  return stroke && [idPathLayer(net, layerId + "-casing", ids, CASING_COLOR, extraWidth + 2), stroke];
}

function selectionLayers(net, style, sel) {
  const layers = [];
  const path = idPathLayer(net, "selection", sel.link_ids, [...style.colors.selection, 255], 3);
  if (path) layers.push(path);
  if (sel.anchors.length) layers.push(new deck.ScatterplotLayer({ id: "anchors", data: sel.anchors,
    getPosition: a => [a.lon, a.lat], getRadius: 7, radiusUnits: "pixels",
    getFillColor: a => (a.role === "from" ? [53, 196, 106] : [224, 82, 77]),
    getLineColor: [17, 21, 26], lineWidthMinPixels: 2, stroked: true, parameters: { depthTest: false } }));
  return layers;
}

// Records a foreign key away from the focus/highlights: lighter links, and rings on nodes.
function relatedLayers(net, related) {
  const layers = [], links = related.map.link, nodes = related.map.node;
  if (links && links.ids.length) {
    const l = idPathLayer(net, "related-links", links.ids, RELATED_COLOR, 1.5);
    if (l) layers.push(l);
  }
  if (nodes && nodes.ids.length) {
    const idx = nodes.ids.map(id => net.nodeId2idx.get(id)).filter(i => i != null);
    layers.push(new deck.ScatterplotLayer({ id: "related-nodes", data: idx,
      getPosition: i => [net.nodePositions[i * 2], net.nodePositions[i * 2 + 1]], getRadius: 5, radiusUnits: "pixels",
      stroked: true, filled: false, getLineColor: RELATED_COLOR, lineWidthMinPixels: 2, parameters: { depthTest: false } }));
  }
  return layers;
}

function markerLayer(marker) {
  return new deck.ScatterplotLayer({ id: "marker", data: [marker], getPosition: m => [m.lon, m.lat],
    getRadius: 7, radiusUnits: "pixels", getFillColor: [45, 210, 230], getLineColor: [17, 21, 26],
    lineWidthMinPixels: 2, stroked: true, parameters: { depthTest: false } });
}

function setLabels(show) {
  if (show === labelsShown || !styleReady) return;
  labelsShown = show;
  for (const l of map.getStyle().layers || [])
    if (l.type === "symbol" || l.id === "labels") map.setLayoutProperty(l.id, "visibility", show ? "visible" : "none");
}

// Core's layer groups, bottom to top (layers.js CORE_ORDER). Each returns what render() used to push, or nothing.
function registerCoreLayers() {
  const reg = (id, factory) => layerRegistry.register(CORE, "roadway", id, factory);
  reg("base", c => baseLayers(c.state));
  reg("selection", c => (c.style.show.selection && c.selection ? selectionLayers(c.net, c.style, c.selection) : null));
  reg("related", c => (c.related ? relatedLayers(c.net, c.related) : null));
  reg("highlighted", c => (c.highlights.size ? casedPathLayers(c.net, "highlighted", c.highlights, [...HIGHLIGHT_COLOR, 255], 2.5) : null));
  reg("focus", c => (c.focus && c.focus.table === "link" ? casedPathLayers(c.net, "focus", [c.focus.id], FOCUS_COLOR, 4) : null));
  reg("marker", c => (c.marker ? markerLayer(c.marker) : null));
}

// What core's layer factories see. `deck` is the global deck.gl.
function layerContext(s) {
  return { state: s, server: s.server, style: s.server.style, net: s.net, attrs: s.attrs, selection: activeSelection(s),
    focus: s.focus, highlights: s.highlights, related: s.related, marker: s.marker, zoom: map.getZoom(), deck };
}

// What a plugin's layer factory sees (the cookbook's registerLayer): the command context's frozen copies, plus the
// map's own parts. `highlights` is a copy (a Set); `net` and `attrs`, the decoded network, are too big to copy per
// render and are shared: read them, never change them.
function pluginLayerContext(s) {
  const c = commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace });
  return Object.freeze({ server: c.state, style: c.state.style, net: s.net, attrs: s.attrs, selection: c.selection,
    focus: c.focus, highlights: new Set(s.highlights), related: frozenCopy(s.related), marker: frozenCopy(s.marker),
    zoom: map.getZoom(), deck });
}

const isDeckLayer = layer => typeof deck.Layer !== "function" || layer instanceof deck.Layer;

export function render() {
  if (!overlay) return;
  const s = store.get();
  if (!s.net || !s.server) { overlay.setProps({ layers: [] }); return; }
  const { layers, errors } = layerRegistry.build("roadway", layerContext(s), s.hiddenLayers,
    { isLayer: isDeckLayer, pluginCtx: () => pluginLayerContext(s) });
  overlay.setProps({ layers });
  setLabels(s.server.style.show.labels);
  for (const e of errors) handlers.onLayerError(e);
}

// A plugin's layer (wb.registerLayer): drawn at once, and listed under Layers → Overlays when it has a title.
export function addLayer(owner, component, id, factory, options) {
  const dispose = layerRegistry.register(owner, component, id, factory, options);
  const changed = () => store.set({ layerSeq: store.get().layerSeq + 1 });
  changed();
  return () => { if (dispose()) changed(); };
}

function getTooltip({ layer, index }) {
  const attrs = store.get().attrs;
  if (!layer || layer.id !== "links" || index == null || index < 0 || !attrs) return null;
  const id = attrs.link_id[index], nm = attrs.name[index], rf = attrs.ref[index], ft = attrs.facility_type[index];
  return { html: `<b>link ${esc(id)}</b><br>${nm ? esc(nm) : "<i>unnamed</i>"}${rf ? " · " + esc(rf) : ""}` +
    `<br><span style="color:#8a93a3">${ft ? esc(ft) : ""}</span>`, style: TOOLTIP_STYLE };
}

// The record under a right-click: a link or node feature ({table, id}), or null.
function recordAt(point) {
  const s = store.get();
  const info = overlay.pickObject({ x: point.x, y: point.y, radius: 4, layerIds: ["nodes", "links"] });
  if (!info || info.index == null || info.index < 0 || !s.net) return null;
  if (info.layer.id === "links") return { table: "link", id: s.attrs.link_id[info.index] };
  if (info.layer.id === "nodes") return { table: "node", id: s.net.nodeIds[nodeIndex(info.index)] };
  return null;
}

function fit(bounds, padding, retried, duration = 500) {
  if (bounds.isEmpty()) return;
  const el = map.getContainer(), w = el.clientWidth, h = el.clientHeight;
  if (!retried && (w <= padding * 2 || h <= padding * 2)) {
    map.resize();
    requestAnimationFrame(() => fit(bounds, padding, true, duration));
    return;
  }
  const p = Math.min(padding, Math.floor(Math.min(w, h) / 4));
  map.fitBounds(bounds, { padding: p, maxZoom: 15, duration });
}

// `animate: false` jumps (a network just opened: no flight from the continental opening view).
export function fitNetwork({ animate = true } = {}) {
  const net = store.get().net;
  if (!map || !net) return;
  const b = new maplibregl.LngLatBounds();
  for (let i = 0; i < net.nodePositions.length; i += 2) b.extend([net.nodePositions[i], net.nodePositions[i + 1]]);
  fit(b, 60, false, animate ? 500 : 0);
}

export function fitLinks(ids) {
  const net = store.get().net;
  if (!map || !net || !ids || !ids.length) return;
  const b = new maplibregl.LngLatBounds();
  for (const id of ids) {
    const i = net.id2idx.get(id);
    if (i == null) continue;
    for (let k = net.linkStart[i] * 2; k < net.linkStart[i + 1] * 2; k += 2) b.extend([net.linkPositions[k], net.linkPositions[k + 1]]);
  }
  fit(b, 80);
}

export function fitBbox(bbox) { if (map) map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 500 }); }

export function flyToNode(nodeId, { fly = true } = {}) {
  const net = store.get().net, i = net && net.nodeId2idx.get(nodeId);
  if (i == null) return;
  const lon = net.nodePositions[i * 2], lat = net.nodePositions[i * 2 + 1];
  store.set({ marker: { lon, lat } });
  if (fly) map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 15), duration: 500 });
}

// Fly to a point that is not a feature (an issue that reported coordinates) and mark it.
export function flyTo(lon, lat) {
  if (!map) return;
  store.set({ marker: { lon, lat } });
  map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 15), duration: 500 });
}

// shift-drag box select over the links layer (deck region picking) adds to highlights
function wireBoxSelect() {
  const mapEl = $("map"), box = $("boxsel");
  let start = null;
  const deckInstance = () => overlay._deck || (overlay.props && overlay.props.deck) || null;
  mapEl.addEventListener("pointerdown", e => {
    if (!store.get().highlightMode || !e.shiftKey || e.button !== 0) return;
    e.preventDefault(); map.dragPan.disable();
    const r = mapEl.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top, rect: r };
    Object.assign(box.style, { display: "block", left: start.x + "px", top: start.y + "px", width: "0px", height: "0px" });
  });
  mapEl.addEventListener("pointermove", e => {
    if (!start) return;
    const x = e.clientX - start.rect.left, y = e.clientY - start.rect.top;
    Object.assign(box.style, { left: Math.min(x, start.x) + "px", top: Math.min(y, start.y) + "px",
      width: Math.abs(x - start.x) + "px", height: Math.abs(y - start.y) + "px" });
  });
  const finish = e => {
    if (!start) return;
    const x = e.clientX - start.rect.left, y = e.clientY - start.rect.top;
    const x0 = Math.min(x, start.x), y0 = Math.min(y, start.y), w = Math.abs(x - start.x), h = Math.abs(y - start.y);
    box.style.display = "none"; map.dragPan.enable(); start = null;
    const dk = deckInstance(), s = store.get();
    if (!dk || !s.net || w <= 2 || h <= 2) return;
    const highlights = new Set(s.highlights);
    for (const p of dk.pickObjects({ x: x0, y: y0, width: w, height: h, layerIds: ["links"] }))
      if (p.index != null && p.index >= 0) highlights.add(s.net.linkIds[p.index]);
    store.set({ highlights });
    handlers.onBoxSelect();
  };
  mapEl.addEventListener("pointerup", finish);
  mapEl.addEventListener("pointerleave", e => { if (start) finish(e); });
}

// Swap the basemap in place. The deck.gl overlay is a non-interleaved control and survives setStyle;
// label visibility belongs to the old style's layers, so it is re-applied once the new style loads.
export function setBasemap(style) {
  if (!map) return;
  // A new style shows its labels: reset before the swap, so render() re-applies a "labels off" once it loads.
  // diff:false makes MapLibre load the style afresh, which fires style.load (a diffed swap may not).
  labelsShown = true; styleReady = false;
  map.once("style.load", () => render());
  map.setStyle(style, { diff: false });
}

registerCoreLayers();
