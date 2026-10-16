// Layers / map-settings panels and the legend. Every control change is a `style` action.
import { dispatch } from "./api.js";
import { $, esc, toast } from "./dom.js";
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

export function populateColorby(props) {
  const sel = $("colorby");
  sel.innerHTML = '<option value="none">None (single color)</option>';
  for (const p of props) {
    const o = document.createElement("option");
    o.value = p.name; o.dataset.kind = p.kind;
    o.textContent = p.name + (p.kind === "continuous" ? " (num)" : "");
    sel.appendChild(o);
  }
}

export function syncControls(st) {
  $("colorby").value = st.color_by;
  $("ramp").value = st.ramp;
  const opt = $("colorby").selectedOptions[0];
  $("ramp-row").style.display = opt && opt.dataset.kind === "continuous" ? "flex" : "none";
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
