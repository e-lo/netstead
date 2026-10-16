// Layers / map-settings panels and the legend. Every control change is a recorded Action: `style` (colour, width,
// switches) or `filter` (Layers → Filter links).
import { dispatch } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { filterKey, filterValue, filterView, takesNoValue } from "./filtermodel.js";
import { hasOffset } from "./map.js";
import { RAMPS, catColor, fmt, hex2rgb, rgb, rgb2hex } from "./palette.js";
import { store } from "./store.js";

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
  const sp = $("settings-panel"), lp = $("layers-panel"), bs = $("btn-settings"), bl = $("btn-layers");
  const open = which => {
    const toS = which === "s" && !sp.classList.contains("open"), toL = which === "l" && !lp.classList.contains("open");
    sp.classList.toggle("open", toS); lp.classList.toggle("open", toL);
    bs.classList.toggle("on", toS); bl.classList.toggle("on", toL);
  };
  bs.onclick = () => open("s");
  bl.onclick = () => open("l");
  $("colorby").onchange = e => style({ color_by: e.target.value });
  $("ramp").onchange = e => style({ ramp: e.target.value });
  $("widthby").onchange = e => style({ width_by: e.target.value });
  for (const [id, , patch] of SWITCHES) $(id).onchange = e => style(patch(e.target.checked));
  for (const [id, key] of COLORS) $(id).onchange = e => style({ colors: { [key]: hex2rgb(e.target.value) } });
  if (!hasOffset()) $("tg-offset").disabled = true;
  $("plugin-layers").onchange = e => {
    const el = e.target.closest("input[data-layer]");
    if (!el) return;
    const hidden = new Set(store.get().hiddenLayers);
    if (el.checked) hidden.delete(el.dataset.layer); else hidden.add(el.dataset.layer);
    store.set({ hiddenLayers: hidden });
  };
}

// Layers → Overlays: each titled registry layer (a plugin's, or later core's issue markers) with a show/hide switch.
// Hiding is view state, like the focus: it is per tab and never recorded. Nothing renders while there are none.
export function renderPluginLayers(list, hidden) {
  $("plugin-layers").innerHTML = list.length
    ? '<h4 style="margin-top:14px">Overlays</h4>' + list.map(l => `<div class="row"><label class="sw">` +
      `<input type="checkbox" data-layer="${esc(l.id)}" aria-label="${esc(l.title)}"${hidden.has(l.id) ? "" : " checked"}>` +
      `<span></span></label><span class="lbl">${esc(l.title)}</span></div>`).join("")
    : "";
}

const option = (value, text) => Object.assign(document.createElement("option"), { value, textContent: text });

// Colour by any property; width by a numeric one (or the lane count); filter on any.
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
  $("colorby").value = st.color_by;
  $("ramp").value = st.ramp;
  const opt = $("colorby").selectedOptions[0];
  $("ramp-row").style.display = opt && opt.dataset.kind === "continuous" ? "flex" : "none";
  const width = st.width_by || "none";
  if (width !== "none" && ![...$("widthby").options].some(o => o.value === width)) {
    $("widthby").appendChild(option(width, width)); // not loaded yet (properties arrive after the state)
  }
  $("widthby").value = width;
  for (const [id, get] of SWITCHES) $(id).checked = get(st);
  for (const [id, key] of COLORS) $(id).value = rgb2hex(st.colors[key]);
}

export function renderLegend(st, prop) {
  const el = $("legend");
  if (!st.show_legend || st.color_by === "none" || !prop || prop.name !== st.color_by) { el.style.display = "none"; return; }
  el.style.display = "block";
  let html = `<div class="lg-title">${esc(prop.name)}</div>`;
  if (prop.kind === "continuous") {
    html += `<div class="lg-grad" style="background:linear-gradient(90deg, ${RAMPS[st.ramp].map(rgb).join(", ")})"></div>` +
      `<div class="lg-scale"><span>${fmt(prop.min)}</span><span>${fmt(prop.max)}</span></div>`;
  } else {
    html += prop.categories.map((c, i) =>
      `<div class="lg-item"><span class="lg-sw" style="background:${rgb(catColor(prop, c, i))}"></span>${esc(c)}</div>`).join("");
  }
  el.innerHTML = html;
}
