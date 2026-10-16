// Header: network switcher, Open / Import… and Recent, and the utterance box. All via actions.
import { dispatch } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { fitLinks } from "./map.js";
import { derivedTitle, networkBadge } from "./tabs.js";
import { openWizard } from "./wizard.js";

// Recents live in this browser's localStorage: a per-user convenience that needs no server code.
// They are only shortcuts: re-opening one is a normal open_network action, checked against io.allowed_roots.
const RECENT_KEY = "netstead.workbench.recent";
const RECENT_MAX = 10;

async function run(action, after) {
  try { const result = await dispatch(action); if (after) after(result); } catch (e) { toast(e.message); }
}

// A remote URL is kept without its query string or fragment (a presigned signature or SAS token must
// not sit in localStorage); re-opening a signed URL from Recent therefore needs a fresh link.
function withoutSecrets(source) {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(source) && !/^(duckdb|file):/i.test(source)
    ? source.replace(/[?#].*$/, "")
    : source;
}

function loadRecent() {
  let list;
  try { list = JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; }
  return list.map(r => {  // also scrubs entries saved before sources were cleaned (label included)
    const source = withoutSecrets(String(r.source));
    return source === r.source ? r : { source, label: String(r.label).replace(/[?#].*$/, "") };
  });
}

function saveRecent(list) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) { /* storage unavailable: recents aren't kept */ }
}

export function renderRecent() {
  const list = loadRecent();
  $("recent").innerHTML = '<option value="">Recent…</option>' +
    list.map((r, i) => `<option value="${i}" title="${esc(r.source)}">${esc(r.label)}</option>`).join("");
  $("recent").disabled = !list.length;
}

// Called for every history entry (from the UI, Python, or the CLI): remember successful opens and builds.
export function rememberRecent(entry) {
  if (!entry.ok) return;
  const a = entry.action;
  const raw = a.type === "open_network" ? a.source : a.type === "build_network" ? entry.result.output : null;
  if (!raw) return;
  const source = withoutSecrets(raw);
  const label = a.label || (a.type === "build_network" ? a.name : source.replace(/[\\/]+$/, "").split(/[\\/]/).pop());
  saveRecent([{ source, label }, ...loadRecent().filter(r => r.source !== source)].slice(0, RECENT_MAX));
  renderRecent();
}

export function renderHeader(server) {
  const sel = $("net-select");
  sel.innerHTML = server.networks.length
    ? server.networks.map(n => {
      const mark = networkBadge(n); // a base network's option is exactly as before
      return `<option value="${esc(n.id)}"${n.id === server.active ? " selected" : ""}` +
        `${mark ? ` title="${esc(derivedTitle(n, server.networks))}"` : ""}>${esc(n.label)}${mark ? ` • ${esc(mark)}` : ""}</option>`;
    }).join("")
    : '<option value="">No network open</option>';
  sel.disabled = !server.networks.length;
  const h = server.networks.find(n => n.id === server.active);
  $("count").textContent = h ? `${h.links.toLocaleString()} links · ${h.nodes.toLocaleString()} nodes` : "";
}

export function wireHeader() {
  $("net-select").onchange = e => run({ type: "set_active_network", net_id: e.target.value });
  $("open-wizard").onclick = () => openWizard();
  $("recent").onchange = e => {
    const r = loadRecent()[Number(e.target.value)];
    e.target.value = "";
    if (r) run({ type: "open_network", source: r.source, label: r.label });
  };
  const select = async () => {
    const utterance = $("utterance").value.trim();
    if (!utterance) return;
    $("go").disabled = true;
    await run({ type: "select", utterance }, sel => fitLinks(sel.link_ids));
    $("go").disabled = false;
  };
  $("go").onclick = select;
  $("utterance").onkeydown = e => { if (e.key === "Enter") select(); };
}
