// Header: network switcher, the Open menu (recents, then Browse / import…), the validity light, and the utterance
// box (the command bar: Enter selects, as it always has; Ask, Ctrl+Enter or ⌘Enter sends the text to the
// assistant). All via actions.
import { dispatch } from "./api.js";
import { ask } from "./assistant.js";
import { barRoute } from "./assistmodel.js";
import { $, esc, toast } from "./dom.js";
import { dirtyBadge } from "./editmodel.js";
import { icon } from "./icons.js";
import { fitLinks } from "./map.js";
import { recentEntries } from "./recents.js";
import { slots } from "./slots.js";
import { derivedTitle, networkBadge } from "./tabs.js";
import { validityState } from "./validity.js";
import { showPanel } from "./workspaces.js";
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

// The Open menu's recent entries (up to three), each a menu item that re-opens the network; its title is the path.
function renderRecents() {
  const list = recentEntries(loadRecent());
  $("open-recents").innerHTML = list.length
    ? list.map(r => `<button role="menuitem" data-path="${esc(r.path)}" title="${esc(r.path)}">${esc(r.label)}</button>`).join("")
    : '<span class="empty">No recent networks</span>';
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
}

function openMenuIsOpen() { return !$("open-menu").hidden; }

function setOpenMenu(open) {
  $("open-menu").hidden = !open;
  $("open-wizard").setAttribute("aria-expanded", String(open));
  if (open) {
    renderRecents();
    const first = $("open-menu").querySelector('[role="menuitem"]');
    if (first) first.focus();
  }
}

// Arrow keys move between the items; Escape closes and returns focus to the Open button.
function onOpenMenuKey(e) {
  const items = [...$("open-menu").querySelectorAll('[role="menuitem"]')];
  const i = items.indexOf(document.activeElement);
  if (e.key === "Escape") { e.preventDefault(); setOpenMenu(false); $("open-wizard").focus(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
}

// The validity light: the level's icon and colour class, the tooltip and the accessible name, from the server state.
export function renderValidity(server) {
  const v = validityState(server), b = $("valid-light");
  b.className = `iconbtn vl-${v.level}`;
  b.innerHTML = icon(v.icon);
  b.dataset.tip = v.tip;
  b.setAttribute("aria-label", `Validation: ${v.tip}`);
}

export function renderHeader(server) {
  const sel = $("net-select");
  sel.innerHTML = server.networks.length
    ? server.networks.map(n => {
      const mark = networkBadge(n); // a clean base network's option is exactly as before
      const marks = [dirtyBadge((server.edits || {})[n.id]), mark].filter(Boolean).join(" • "); // unsaved edits first
      return `<option value="${esc(n.id)}"${n.id === server.active ? " selected" : ""}` +
        `${mark ? ` title="${esc(derivedTitle(n, server.networks))}"` : ""}>${esc(n.label)}${marks ? ` • ${esc(marks)}` : ""}</option>`;
    }).join("")
    : '<option value="">No network open</option>';
  sel.disabled = !server.networks.length;
  const h = server.networks.find(n => n.id === server.active);
  $("count").textContent = h ? `${h.links.toLocaleString()} links · ${h.nodes.toLocaleString()} nodes` : "";
  renderValidity(server);
}

export function wireHeader() {
  $("net-select").onchange = e => run({ type: "set_active_network", net_id: e.target.value });
  $("open-wizard").onclick = () => setOpenMenu(!openMenuIsOpen());
  $("open-menu").onclick = e => {
    const item = e.target.closest('[role="menuitem"]');
    if (!item) return;
    setOpenMenu(false);
    if (item.id === "open-browse") { openWizard(); return; }
    const r = recentEntries(loadRecent(), RECENT_MAX).find(x => x.path === item.dataset.path);
    if (r) run({ type: "open_network", source: r.path, label: r.label });
  };
  $("open-menu").onkeydown = onOpenMenuKey;
  document.addEventListener("pointerdown", e => { if (openMenuIsOpen() && !e.target.closest("#open-wrap")) setOpenMenu(false); });
  // The validity light opens the Validate panel (the Issues panel until that rename lands).
  $("valid-light").onclick = () => showPanel(slots.panels.get("validate") ? "validate" : "issues");
  const select = async () => {
    const utterance = $("utterance").value.trim();
    if (!utterance) return;
    $("go").disabled = true;
    await run({ type: "select", utterance }, sel => fitLinks(sel.link_ids));
    $("go").disabled = false;
  };
  $("go").onclick = select;
  $("ask").onclick = () => ask($("utterance").value, { fromBar: true });
  $("utterance").onkeydown = e => {
    if (e.isComposing) return;
    const route = barRoute(e);
    if (route === "select") select();
    else if (route === "ask") { e.preventDefault(); ask($("utterance").value, { fromBar: true }); }
  };
}
