// Area step: Draw / Coordinates / Place / Local file. Every map tab ends in one bbox, previewed on its own small map.
import { getJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { createFileBrowser } from "./filebrowser.js";
import { candidatesHTML } from "./placesearch.js";
import { store } from "./store.js";

const M_PER_DEG_LAT = 111320; // same spherical approximation as netstead.osm.query.point_buffer_bbox
const OPPOSITE = [2, 3, 0, 1]; // corner order SW, SE, NE, NW; dragging one keeps its opposite fixed
const FILE_KINDS = { osm: ["osm", "json"], overture: ["overture"] };
const EMPTY = { type: "FeatureCollection", features: [] };

let map = null, markers = [], drawStart = null, drawEnd = null, drawing = false, searchSeq = 0, fitToNetwork = true;
let tab = "draw", area = null, inputFile = null, candidates = [], fileBrowser = null, onChange = () => {};

const round6 = v => Math.round(v * 1e6) / 1e6;
const ring = b => [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]];
const corners = b => [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
const polygonFeature = coords => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [coords] } });
const lonLat = poly => poly.map(([lat, lon]) => [lon, lat]); // place polygons are (lat, lon), like the server's
const bboxOf = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])].map(round6);
// Map coordinates run past ±180 once the map is panned across the antimeridian; bring one back to [-180, 180].
const wrapLng = lng => { const w = ((((lng + 180) % 360) + 360) % 360) - 180; return w === -180 && lng > 0 ? 180 : w; };

// "" when `b` is a usable W,S,E,N bbox, else why not (mirrors the server's Area validation).
function bboxProblem(b) {
  if (!b || b.length !== 4 || !b.every(Number.isFinite)) return "The area needs four numbers: W,S,E,N.";
  if (!(b[0] >= -180 && b[2] <= 180 && b[1] >= -90 && b[3] <= 90)) {
    return "The area must lie within -180..180 longitude and -90..90 latitude.";
  }
  if (!(b[0] < b[2] && b[1] < b[3])) return "The area must have W<E and S<N.";
  return "";
}

export function pointBbox(lat, lon, m) {
  const dlat = m / M_PER_DEG_LAT, cos = Math.cos((lat * Math.PI) / 180);
  const dlon = cos ? m / (M_PER_DEG_LAT * cos) : dlat;
  return [lon - dlon, lat - dlat, lon + dlon, lat + dlat];
}

export function areaBbox(a) { return a.kind === "point" ? pointBbox(a.lat, a.lon, a.buffer_m) : a.bbox; }

// What the build action needs from this step: {area} or {input_file}, or null when nothing is chosen yet.
export function areaChoice() {
  if (tab === "file") return inputFile ? { input_file: inputFile } : null;
  // Also covers a click without a drag, a corner dragged onto its opposite, and a buffer past the poles.
  return area && !bboxProblem(areaBbox(area)) ? { area } : null;
}

function setArea(next, { fit = false, quiet = false } = {}) {
  area = next;
  render();
  const b = area && areaBbox(area);
  if (fit && b && !bboxProblem(b)) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 300 });
  if (!quiet) onChange();
}

// Keep a drawn or dragged box: wrap its longitudes; refuse one that crosses the antimeridian
// (restoring `previous`, the box before a corner drag, or clearing a fresh draw).
function commitBbox(raw, previous = null) {
  const b = [wrapLng(raw[0]), raw[1], wrapLng(raw[2]), raw[3]].map(round6);
  if (raw[0] < raw[2] && b[0] >= b[2]) {
    toast("That box crosses the antimeridian (180°); draw it on one side.");
    setArea(previous);
    return;
  }
  setArea({ kind: "bbox", bbox: b });
}

function render() {
  if (!map || !map.getSource("ap-area")) return;
  const shape = !area ? null : area.kind === "place" && area.polygon ? lonLat(area.polygon) : ring(areaBbox(area));
  map.getSource("ap-area").setData(shape ? polygonFeature(shape) : EMPTY);
  const editable = area && area.kind === "bbox";
  markers.forEach((m, i) => {
    if (editable) m.setLngLat(corners(area.bbox)[i]).addTo(map); else m.remove();
  });
  const b = area && areaBbox(area);
  $("ap-summary").textContent = b ? `W,S,E,N = ${b.map(v => v.toFixed(5)).join(", ")}` : "No area yet.";
}

function makeMarkers() {
  markers = [0, 1, 2, 3].map(i => {
    const m = new maplibregl.Marker({ draggable: true, color: "#2dd2e6", scale: 0.6 });
    // Captured at dragstart, so dragging past the opposite corner cannot swap which one is fixed.
    let fixed = null, before = null;
    m.on("dragstart", () => { before = area; fixed = corners(area.bbox)[OPPOSITE[i]]; });
    m.on("drag", () => {
      const p = m.getLngLat();
      // `fixed` is wrapped but the marker may sit on a panned world copy: bring it within 180° of `fixed`.
      const lng = fixed[0] + ((((p.lng - fixed[0]) % 360) + 540) % 360) - 180;
      area = { kind: "bbox", bbox: bboxOf([lng, p.lat], fixed) };
      map.getSource("ap-area").setData(polygonFeature(ring(area.bbox)));
    });
    m.on("dragend", () => commitBbox(area.bbox, before));
    return m;
  });
}

function wireDraw() {
  map.on("mousedown", e => {
    if (!drawing) return;
    e.preventDefault();
    drawStart = [e.lngLat.lng, e.lngLat.lat];
  });
  map.on("mousemove", e => {
    if (!drawStart) return;
    drawEnd = [e.lngLat.lng, e.lngLat.lat];
    setArea({ kind: "bbox", bbox: bboxOf(drawStart, drawEnd) }, { quiet: true });
  });
  // On the document, so a button released outside the map still ends the draw.
  document.addEventListener("mouseup", () => {
    if (!drawStart) return;
    const raw = drawEnd && bboxOf(drawStart, drawEnd);
    drawStart = null; drawEnd = null; drawing = false;
    map.dragPan.enable(); map.getCanvas().style.cursor = ""; $("ap-draw").classList.remove("on");
    if (raw) commitBbox(raw); else onChange();
  });
}

// The active network's [[W,S],[E,N]] extent, or null (then the map starts at the US-wide default).
function networkBounds() {
  const pos = store.get().net && store.get().net.nodePositions;
  if (!pos || !pos.length) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (let k = 0; k < pos.length; k += 2) {
    w = Math.min(w, pos[k]); e = Math.max(e, pos[k]); s = Math.min(s, pos[k + 1]); n = Math.max(n, pos[k + 1]);
  }
  return [w, s, e, n].every(Number.isFinite) ? [[w, s], [e, n]] : null;
}

// Create the preview map the first time the Area step is shown (a hidden container has no size).
// Each new build starts the view at the active network's extent when there is one.
export function showAreaMap(style) {
  const bounds = fitToNetwork ? networkBounds() : null;
  fitToNetwork = false;
  if (map) {
    map.resize();
    if (bounds) map.fitBounds(bounds, { padding: 30, maxZoom: 14, duration: 0 });
    return;
  }
  map = new maplibregl.Map(bounds
    ? { container: "ap-map", style, bounds, fitBoundsOptions: { padding: 30, maxZoom: 14 } }
    : { container: "ap-map", style, center: [-98.5, 39.8], zoom: 3 });
  map.on("load", () => {
    map.addSource("ap-area", { type: "geojson", data: EMPTY });
    map.addSource("ap-cands", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "ap-cands-line", type: "line", source: "ap-cands",
      paint: { "line-color": "#9aa3b2", "line-width": 1, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "ap-area-fill", type: "fill", source: "ap-area", paint: { "fill-color": "#2dd2e6", "fill-opacity": 0.12 } });
    map.addLayer({ id: "ap-area-line", type: "line", source: "ap-area", paint: { "line-color": "#2dd2e6", "line-width": 2 } });
    render();
  });
  makeMarkers();
  wireDraw();
}

function setTab(name) {
  tab = name;
  for (const b of document.querySelectorAll("#ap-tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".ap-pane")) p.hidden = p.dataset.pane !== name;
  $("ap-map").hidden = name === "file";
  $("ap-summary").hidden = name === "file";
  if (name !== "file" && map) map.resize();
  onChange();
}

function applyCoordinates() {
  const nums = s => s.split(",").map(v => Number(v.trim())).filter(v => Number.isFinite(v));
  const bbox = nums($("ap-bbox").value), point = nums($("ap-point").value), buffer = Number($("ap-buffer").value);
  let next = null, problem = "";
  if (bbox.length === 4) {
    next = { kind: "bbox", bbox: bbox.map(round6) };
    problem = bboxProblem(next.bbox);
  } else if (point.length === 2 && buffer > 0) {
    const [lat, lon] = point;
    next = { kind: "point", lat, lon, buffer_m: buffer };
    problem = !(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180)
      ? "lat must be within -90..90 and lon within -180..180 (enter lat,lon, latitude first)."
      : bboxProblem(pointBbox(lat, lon, buffer)) && "The buffer reaches past 180° longitude or a pole; use a smaller one.";
  } else {
    problem = "Enter W,S,E,N, or lat,lon plus a buffer in metres";
  }
  if (problem) { toast(problem); setArea(null); return; }
  setArea(next, { fit: true });
}

async function searchPlace() {
  const q = $("ap-q").value.trim();
  if (!q) return;
  const seq = ++searchSeq; // a slower, older search must not overwrite a newer one
  $("ap-cands").innerHTML = '<span class="empty">Searching…</span>';
  let result;
  try {
    result = { candidates: (await getJSON(`/api/geocode?q=${encodeURIComponent(q)}`)).candidates };
  } catch (error) { result = { error }; }
  if (seq !== searchSeq) return;
  candidates = result.candidates || [];
  $("ap-cands").innerHTML = candidatesHTML(result);
  const outlines = candidates.map(c => polygonFeature(c.polygon ? lonLat(c.polygon) : ring(c.bbox)));
  if (map && map.getSource("ap-cands")) map.getSource("ap-cands").setData({ type: "FeatureCollection", features: outlines });
}

function pickCandidate(i) {
  const c = candidates[i];
  for (const el of document.querySelectorAll("#ap-cands .cand")) el.classList.toggle("on", Number(el.dataset.i) === i);
  const b = c.bbox, problem = b && b[0] >= b[2] ? "That place crosses the antimeridian (180°); draw or type a box on one side."
    : bboxProblem(b);
  if (problem) { toast(problem); setArea(null); return; }
  setArea({ kind: "place", name: c.display_name, bbox: c.bbox, polygon: c.polygon }, { fit: true });
}

// Reset for a new build of `source` ("osm" | "overture").
export function resetAreaPicker(source) {
  area = null; inputFile = null; candidates = []; drawing = false; drawStart = null; drawEnd = null; searchSeq++;
  fitToNetwork = true;
  $("ap-draw").classList.remove("on");
  if (map) { map.dragPan.enable(); map.getCanvas().style.cursor = ""; }
  $("ap-cands").innerHTML = "";
  $("ap-file-note").textContent = source === "osm" ? "A .osm XML file or an Overpass JSON export." : "A folder holding segment.parquet + connector.parquet.";
  fileBrowser.configure({ kinds: FILE_KINDS[source] });
  fileBrowser.reset();
  if (map && map.getSource("ap-cands")) map.getSource("ap-cands").setData(EMPTY);
  render();
  setTab("draw");
}

export function wireAreaPicker(handler) {
  onChange = handler;
  fileBrowser = createFileBrowser($("ap-file-fb"), { kinds: [], onPick: e => { inputFile = e.target; onChange(); } });
  for (const b of document.querySelectorAll("#ap-tabs button")) b.onclick = () => setTab(b.dataset.tab);
  $("ap-draw").onclick = () => {
    if (!map) return;
    drawing = !drawing;
    $("ap-draw").classList.toggle("on", drawing);
    map.getCanvas().style.cursor = drawing ? "crosshair" : "";
    if (drawing) map.dragPan.disable(); else map.dragPan.enable();
  };
  $("ap-apply").onclick = applyCoordinates;
  $("ap-search").onclick = searchPlace;
  $("ap-q").onkeydown = e => { if (e.key === "Enter") searchPlace(); };
  $("ap-cands").onclick = e => { const c = e.target.closest(".cand"); if (c) pickCandidate(Number(c.dataset.i)); };
}
