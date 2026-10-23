// The View panel (layers, color links / nodes by, width, direction, filter links) and the map's
// legend. Every control change is a recorded Action: `style` (color, width, switches) or `filter` (Filter links).
import { dispatch } from "./api.js";
import { $, esc, mountIcons, toast } from "./dom.js";
import { filterKey, filterValue, filterView, takesNoValue } from "./filtermodel.js";
import { hasOffset } from "./map.js";
import { RAMPS, catColor, fmt, hex2rgb, nodeLegendItems, rgb, rgb2hex } from "./palette.js";
import { ALL_WORKSPACES, CORE } from "./slots.js";
import { store } from "./store.js";
import { SECTIONS_KEY, parseClosed, serializeClosed, swatchPatch, swatchTip } from "./viewmodel.js";
import { addPanel } from "./workspaces.js";

const style = patch => dispatch({ type: "style", ...patch }).catch(e => toast(e.message));

const SWITCHES = [
  ["tg-links", st => st.show.links, v => ({ show: { links: v } })],
  ["tg-nodes", st => st.show.nodes, v => ({ show: { nodes: v } })],
  ["tg-selection", st => st.show.selection, v => ({ show: { selection: v } })],
  ["tg-labels", st => st.show.labels, v => ({ show: { labels: v } })],
  ["tg-offset", st => st.offset, v => ({ offset: v })],
  ["tg-direction", st => st.show_direction, v => ({ show_direction: v })],
  ["tg-legend", st => st.show_legend, v => ({ show_legend: v })],
];
const COLORS = [["col-links", "links"], ["col-nodes", "nodes"], ["col-selection", "selection"]];

// The View tab, right after Find: its markup is static (index.html #dock-view), wired by wirePanels below.
export function registerViewPanel() {
  addPanel(CORE, { id: "view", title: "View", icon: "sliders", workspace: ALL_WORKSPACES, order: 5 });
}

export function wirePanels() {
  $("colorby").onchange = e => style({ color_by: e.target.value });
  $("node-colorby").onchange = e => style({ node_color_by: e.target.value });
  $("ramp").onchange = e => style({ ramp: e.target.value });
  $("widthby").onchange = e => style({ width_by: e.target.value });
  for (const [id, , patch] of SWITCHES) $(id).onchange = e => style(patch(e.target.checked));
  for (const [id, key] of COLORS) $(id).onchange = e => style(swatchPatch(key, hex2rgb(e.target.value), store.get().server.style));
  wireSections();
  if (!hasOffset()) $("tg-offset").disabled = true;
  $("plugin-layers").onchange = e => {
    const el = e.target.closest("input[data-layer]");
    if (!el) return;
    const hidden = new Set(store.get().hiddenLayers);
    if (el.checked) hidden.delete(el.dataset.layer); else hidden.add(el.dataset.layer);
    store.set({ hiddenLayers: hidden });
  };
}

// View → Overlays: each titled registry layer (a plugin's, or core's issue markers) with a show/hide switch.
// Hiding is view state, like the focus: it is per tab and never recorded. Nothing renders while there are none.
export function renderPluginLayers(list, hidden) {
  $("plugin-layers").innerHTML = list.length
    ? `<details class="vsec" data-sec="overlays"${closedSections.has("overlays") ? "" : " open"}><summary>Overlays</summary>` +
      list.map(l => `<div class="row"><label class="sw">` +
      `<input type="checkbox" data-layer="${esc(l.id)}" aria-label="${esc(l.title)}"${hidden.has(l.id) ? "" : " checked"}>` +
      `<span></span></label><span class="lbl">${esc(l.title)}</span>` +
      (l.description ? `<button class="iconbtn" data-icon="info" data-tip="${esc(l.description)}" aria-label="${esc(l.description)}"></button>` : "") +
      `</div>`).join("") + "</details>"
    : "";
  mountIcons($("plugin-layers"));
}

// Folded View sections, remembered per browser (never recorded: it is view state). Unreadable storage means open.
const readStore = () => { try { return localStorage.getItem(SECTIONS_KEY); } catch { return null; } };
let closedSections = parseClosed(readStore());
function wireSections() {
  const view = $("dock-view");
  for (const d of view.querySelectorAll("details[data-sec]")) d.open = !closedSections.has(d.dataset.sec);
  // `toggle` doesn't bubble, so listen in the capture phase (also catches the Overlays section drawn later).
  view.addEventListener("toggle", e => {
    const d = e.target;
    if (!d.dataset || !d.dataset.sec) return;
    if (d.open) closedSections.delete(d.dataset.sec); else closedSections.add(d.dataset.sec);
    try { localStorage.setItem(SECTIONS_KEY, serializeClosed(closedSections)); } catch { /* private mode */ }
  }, true);
}

const option = (value, text) => Object.assign(document.createElement("option"), { value, textContent: text });

// Color by any property; width by a numeric one (or the lane count); filter on any.
export function populateColorby(props) {
  const sel = $("colorby"), width = $("widthby"), col = $("flt-col"), picked = col.value;
  sel.innerHTML = '<option value="none">None (single color)</option>';
  width.innerHTML = '<option value="none">Lane count</option>';
  col.innerHTML = "";
  for (const p of props) {
    const o = option(p.name, p.name + (p.kind === "continuous" ? " (num)" : ""));
    o.dataset.kind = p.kind;
    sel.appendChild(o);
    if (p.kind === "continuous") width.appendChild(option(p.name, p.name));
    col.appendChild(option(p.name, p.name));
  }
  if (props.some(p => p.name === picked)) col.value = picked;
}

// Color nodes by any property of the node table (populated like the links' list).
export function populateNodeColorby(props) {
  const sel = $("node-colorby");
  sel.innerHTML = '<option value="none">None (single color)</option>';
  for (const p of props) {
    const o = option(p.name, p.name + (p.kind === "continuous" ? " (num)" : ""));
    o.dataset.kind = p.kind;
    sel.appendChild(o);
  }
}

// Set a select to `value`; a column its list lacks (the list has not arrived yet, or the table has no such column)
// shows as "none" rather than as an invented option.
function pick(select, value) {
  select.value = [...select.options].some(o => o.value === value) ? value : "none";
}
const continuous = select => { const o = select.selectedOptions[0]; return Boolean(o && o.dataset.kind === "continuous"); };

export function wireFilter() {
  const apply = () => {
    const op = $("flt-op").value, col = $("flt-col").value;
    if (!col) { toast("Open a network with link columns to filter."); return; }
    dispatch({ type: "filter", where: [{ col, op, val: filterValue(op, $("flt-val").value) }] })
      .catch(e => toast(e.message));
  };
  $("flt-apply").onclick = apply;
  $("flt-val").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); apply(); } };
  $("flt-op").onchange = e => { $("flt-val").disabled = takesNoValue(e.target.value); };
  $("flt-clear").onclick = () => dispatch({ type: "filter", where: null }).catch(e => toast(e.message));
}

// The active network's recorded filter: its match count, and its condition in the controls (or, when the controls
// can't send it back, named read-only). The controls change only when the recorded filter does, and never while
// one of them is being edited.
let shownFilter = null;
export function renderFilter(server) {
  const v = filterView(server);
  $("flt-count").textContent = v.count;
  $("flt-clear").disabled = !v.canClear;
  $("flt-note").textContent = v.note;
  $("flt-note").hidden = !v.note;
  const key = filterKey(server);
  if (key === shownFilter || ["flt-col", "flt-op", "flt-val"].includes(document.activeElement && document.activeElement.id)) return;
  shownFilter = key;
  if (!v.controls) return;
  const c = v.controls;
  if (c.col != null && [...$("flt-col").options].some(o => o.value === c.col)) $("flt-col").value = c.col;
  $("flt-op").value = c.op;
  $("flt-val").value = c.val;
  $("flt-val").disabled = c.valDisabled;
}

export function syncControls(st) {
  pick($("colorby"), st.color_by || "none");
  pick($("node-colorby"), st.node_color_by || "none");
  $("ramp").value = st.ramp;
  $("ramp-row").style.display = continuous($("colorby")) || continuous($("node-colorby")) ? "flex" : "none";
  pick($("widthby"), st.width_by || "none");
  for (const [id, get] of SWITCHES) $(id).checked = get(st);
  for (const [id, key] of COLORS) {
    $(id).value = rgb2hex(st.colors[key]);
    $(id).title = swatchTip(key, st);
  }
}

// One legend block: the property's name (with what it colors), then a ramp or the categories' swatches.
function legendBlock(st, prop, what, shape) {
  let html = `<div class="lg-title">${esc(what)}: ${esc(prop.name)}</div>`;
  if (prop.kind === "continuous") {
    html += `<div class="lg-grad" style="background:linear-gradient(90deg, ${RAMPS[st.ramp].map(rgb).join(", ")})"></div>` +
      `<div class="lg-scale"><span>${fmt(prop.min)}</span><span>${fmt(prop.max)}</span></div>`;
  } else {
    const items = shape === "lg-dot" ? nodeLegendItems(prop)
      : prop.categories.map((c, i) => ({ category: c, color: catColor(prop, c, i), radius: null }));
    html += items.map(it => {
      const size = it.radius ? `width:${it.radius * 2}px;height:${it.radius * 2}px;margin:0 ${8 - it.radius}px;` : "";
      return `<div class="lg-item"><span class="lg-sw ${shape}" style="${size}background:${rgb(it.color)}"></span>${esc(it.category)}</div>`;
    }).join("");
  }
  return `<div class="lg-block">${html}</div>`;
}

// The legend shows whichever of the link and node colorings is on (the loaded property must be the styled one).
export function renderLegend(st, prop, nodeProp) {
  const el = $("legend"), blocks = [];
  if (st.show_legend && st.color_by !== "none" && prop && prop.name === st.color_by) blocks.push(legendBlock(st, prop, "Links", "lg-line"));
  if (st.show_legend && st.node_color_by !== "none" && nodeProp && nodeProp.name === st.node_color_by) blocks.push(legendBlock(st, nodeProp, "Nodes", "lg-dot"));
  if (!blocks.length) { el.style.display = "none"; return; }
  el.style.display = "block";
  el.innerHTML = blocks.join("");
}
