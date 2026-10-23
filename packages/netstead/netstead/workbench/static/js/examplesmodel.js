// The Open / Import wizard's "Included examples" list (import-free: unit-tested under node).

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

// One button per bundled example, carrying what open_network needs; "" when there are none. `list` is /api/examples.
export function examplesHTML(list) {
  if (!list.length) return "";
  const items = list.map(x =>
    `<button class="wz-choice wz-example" data-example-source="${esc(x.source)}" data-example-name="${esc(x.name)}">` +
    `<b>${esc(x.name)} <span class="ex-size">${x.links.toLocaleString("en-US")} links, ${x.nodes.toLocaleString("en-US")} nodes</span></b>` +
    `<span>${esc(x.description)}</span></button>`).join("");
  return `<div class="wz-examples-title" role="heading" aria-level="4">Included examples</div>${items}`;
}
