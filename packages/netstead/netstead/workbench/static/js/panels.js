// The Layers & legend card on the map (layers with a visibility eye and swatch, color links / nodes by with the legend
// under each, width, direction and the links filter in the Links row's options, overlays, basemap labels). Every
// control change is a recorded Action: `style` (color, width, switches) or `filter` (Filter links).
import { dispatch } from "./api.js";
import { $, esc, mountIcons, toast } from "./dom.js";
import { filterKey, filterValue, filterView, takesNoValue } from "./filtermodel.js";
import { hasOffset } from "./map.js";
import { RAMPS, catColor, fmt, hex2rgb, nodeLegendItems, rgb, rgb2hex } from "./palette.js";
import { store } from "./store.js";
import { CARD_KEY, parseCardOpen, swatchPatch, swatchTip } from "./viewmodel.js";

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

export function wirePanels() {
  $("colorby").onchange = e => style({ color_by: e.target.value });
  $("node-colorby").onchange = e => style({ node_color_by: e.target.value });
  $("ramp").onchange = e => style({ ramp: e.target.value });
  $("widthby").onchange = e => style({ width_by: e.target.value });
  for (const [id, , patch] of SWITCHES) $(id).onchange = e => style(patch(e.target.checked));
  for (const [id, key] of COLORS) $(id).onchange = e => style(swatchPatch(key, hex2rgb(e.target.value), store.get().server.style));
  wireCard();
  if (!hasOffset()) $("tg-offset").disabled = true;
  $("plugin-layers").onchange = e => {
    const el = e.target.closest("input[data-layer]");
    if (!el) return;
    const hidden = new Set(store.get().hiddenLayers);
    if (el.checked) hidden.delete(el.dataset.layer); else hidden.add(el.dataset.layer);
    store.set({ hiddenLayers: hidden });
  };
}

// Overlays: each titled registry layer (a plugin's, or core's issue markers) is a row of the Layers card with a
// visibility eye. Hiding is view state, like the focus: it is per tab and never recorded. Nothing renders while
// there are none.
export function renderPluginLayers(list, hidden) {
  $("plugin-layers").innerHTML = list.length
    ? '<div class="lc-sub">Overlays</div>' + list.map(l => `<div class="row lc-line"><label class="eye">` +
      `<input type="checkbox" data-layer="${esc(l.id)}" aria-label="Show ${esc(l.title)}"${hidden.has(l.id) ? "" : " checked"}>` +
      `<span class="eye-on" data-icon="eye"></span><span class="eye-off" data-icon="eye-off"></span></label>` +
      `<span class="lbl">${esc(l.title)}</span>` +
      (l.description ? `<button class="iconbtn" data-icon="info" data-tip="${esc(l.description)}" aria-label="${esc(l.description)}"></button>` : "") +
      `</div>`).join("")
    : "";
  mountIcons($("plugin-layers"));
}

// The card is open or collapsed to its Layers button (remembered per browser, never recorded: it is view state;
// unreadable storage means open). A row's chevron shows or hides that layer's other options.
function setCardOpen(open, { save = true, focus = false } = {}) {
  $("layers-card").dataset.open = String(open);
  $("layers-toggle").setAttribute("aria-expanded", String(open));
  if (save) { try { localStorage.setItem(CARD_KEY, String(open)); } catch { /* private mode */ } }
  if (focus) $(open ? "layers-hide" : "layers-toggle").focus();
}

function wireCard() {
  let stored = null;
  try { stored = localStorage.getItem(CARD_KEY); } catch { /* unreadable: open */ }
  setCardOpen(parseCardOpen(stored), { save: false });
  $("layers-toggle").onclick = () => setCardOpen(true, { focus: true });
  $("layers-hide").onclick = () => setCardOpen(false, { focus: true });
  $("layers-card").addEventListener("click", e => {
    const more = e.target.closest(".lc-more");
    if (!more) return;
    const panel = $(more.getAttribute("aria-controls")), open = panel.hidden;
    panel.hidden = !open;
    more.setAttribute("aria-expanded", String(open));
  });
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
  $("lc-links-more").classList.toggle("active", v.canClear);
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

// One legend block under a layer's "by" picker: a ramp (gradient with min and max) or the categories' swatches.
function legendBlock(st, prop, shape) {
  if (prop.kind === "continuous") {
    return `<div class="lg-grad" style="background:linear-gradient(90deg, ${RAMPS[st.ramp].map(rgb).join(", ")})"></div>` +
      `<div class="lg-scale"><span>${fmt(prop.min)}</span><span>${fmt(prop.max)}</span></div>`;
  }
  const items = shape === "lg-dot" ? nodeLegendItems(prop)
    : prop.categories.map((c, i) => ({ category: c, color: catColor(prop, c, i), radius: null }));
  return items.map(it => {
    const size = it.radius ? `width:${it.radius * 2}px;height:${it.radius * 2}px;margin:0 ${8 - it.radius}px;` : "";
    return `<div class="lg-item"><span class="lg-sw ${shape}" style="${size}background:${rgb(it.color)}"></span>${esc(it.category)}</div>`;
  }).join("");
}

// The legend fills the Links and Nodes rows of the card while "Show legend" is on and that layer is colored by a
// column (the loaded property must be the styled one).
export function renderLegend(st, prop, nodeProp) {
  const links = st.show_legend && st.color_by !== "none" && prop && prop.name === st.color_by;
  const nodes = st.show_legend && st.node_color_by !== "none" && nodeProp && nodeProp.name === st.node_color_by;
  $("lg-links").innerHTML = links ? legendBlock(st, prop, "lg-line") : "";
  $("lg-nodes").innerHTML = nodes ? legendBlock(st, nodeProp, "lg-dot") : "";
}
