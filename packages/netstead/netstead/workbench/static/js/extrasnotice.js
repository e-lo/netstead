// The Open / Import wizard's "install this extra" notice (import-free: unit-tested under node).

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

// The extras a choice needs: an OpenStreetMap build always needs [osm]; an Overture build needs [overture], and a
// place-name area (geocoded through OSM) also [osm].
export function extrasFor(source, { place = false } = {}) {
  if (source === "osm") return ["osm"];
  if (source === "overture") return place ? ["overture", "osm"] : ["overture"];
  return [];
}

// The needed extras that are not installed, given the server's `installed` map. An unknown extra counts as present
// (the server may not have answered yet; the build still reports a missing extra itself).
export const missingExtras = (needed, installed) => needed.filter(name => installed && installed[name] === false);

// The notice: which extras to install, each command (uv first, then pip) in a code box with a Copy button, and a
// link to the install docs. `info` is the /api/extras answer; "" when nothing is missing.
export function noticeHTML(missing, info) {
  if (!missing.length) return "";
  const names = missing.map(n => `[${n}]`).join(" and ");
  const commands = (info.install || {})[[...missing].sort().join(",")];
  const boxes = commands ? ["uv", "pip"].filter(k => commands[k]).map(k =>
    `<div class="ex-cmd"><span class="ex-tool">${k}</span><code>${esc(commands[k])}</code>` +
    `<button class="mini" data-copy>Copy</button></div>`).join("") : "";
  return `<div class="ex-notice" role="note"><div>Install the ${esc(names)} extra${missing.length > 1 ? "s" : ""} to use this:</div>` +
    `${boxes}<a href="${esc(info.docs_url)}" target="_blank" rel="noopener">How to install</a></div>`;
}
