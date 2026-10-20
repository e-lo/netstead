// Issues panel: the live edit warnings, then validation: run it (a background job), list issues by filter, and link
// issue <-> map marker <-> row. The list, markers and report are read-only views of the last RunValidation and of
// GET /edits; nothing here is recorded. The markers are a core layer in the layer registry ("Issues" in Overlays).
// Issue messages quote data values: every one goes through esc().
import { getJSON, netPath } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { runCore } from "./editcmds.js";
import { SEVERITIES, SEVERITY_RGB, issueMeta, issuesQuery, markerPoints, rangeLabel, statusLine } from "./issuelist.js";
import { addLayer, flyTo } from "./map.js";
import { CORE } from "./slots.js";
import { store } from "./store.js";
import { jumpTo, tableVisible } from "./table.js";
import { showPanel } from "./workspaces.js";

const PAGE = 100;
let offset = 0, seq = 0, metaKey = null;
const fail = e => toast(e.message);
const activeId = () => { const s = store.get().server; return s && s.active; };

// Server state arrived: reload markers and the list when the active network's issue set (or its staleness) moved.
export function onIssuesState(s) {
  const meta = issueMeta(s.server), key = JSON.stringify([s.server && s.server.active, meta]);
  renderStatus(meta);
  if (key === metaKey) return;
  metaKey = key;
  loadMarkers().catch(fail);
  reloadIssues({ restart: true });
}

export function reloadIssues({ restart = false } = {}) {
  if (restart) offset = 0;
  loadList().catch(fail);
}

function renderStatus(meta) {
  const id = activeId(), current = Boolean(meta && !meta.stale && id);
  $("iss-status").textContent = statusLine(meta);
  const run = $("iss-run"), label = meta && meta.stale ? "Re-run validation" : "Run validation";
  run.dataset.tip = label;
  run.setAttribute("aria-label", label);
  const report = $("iss-report");
  report.classList.toggle("off", !current);
  report.setAttribute("aria-disabled", String(!current));
  report.tabIndex = current ? 0 : -1;
  report.href = current ? netPath(id, "report.html") : "#";
}

// Export report: fetched first, so a refusal (stale, or the reports extra missing) is a toast, not a saved error page.
async function exportReport(e) {
  e.preventDefault();
  const a = $("iss-report");
  if (a.classList.contains("off")) return;
  const r = await fetch(a.href);
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(typeof j.detail === "string" ? j.detail : `export failed (${r.status})`);
  }
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await r.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name ? name[1] : "validation.html";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Pick what "Fix the picked issue…" edits, then run that command (the button and the palette do the same thing).
function fix(target) {
  store.set({ fixTarget: target });
  runCore("fix_issue");
}

// Focus a record: in the table too when it shows (switching to the record's table), else on the map and in Details.
function focusRecord(table, id) {
  if (tableVisible()) jumpTo(table, id).catch(fail);
  else store.set({ focus: { table, id, from: "table" } });
}

// Enter or Space on a focused list row does what a click does.
const onRowKey = (row, act) => { row.onkeydown = e => { if (e.target === row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); act(); } }; };

// The live edit warnings (GET /edits, via store.edits): click one to focus its record; Fix opens the editor on its
// column, so a missing foreign key or an empty required field can be put right where it was found.
export function renderEditWarnings(edits) {
  const list = edits && edits.net_id === activeId() ? edits.warnings : [];
  $("iss-edit-count").textContent = list.length;
  $("iss-edit").innerHTML = list.length ? list.map((w, k) =>
    `<div class="iss sev-warning" data-w="${k}" tabindex="0"><span class="sev-dot" aria-hidden="true"></span><div class="iss-body">` +
    `<div><b>${esc(w.code)}</b> <span class="diag">${esc(w.table)} ${esc(w.key)}${w.column ? ` · ${esc(w.column)}` : ""}</span></div>` +
    `<div class="iss-msg">${esc(w.message)}</div></div><button class="mini ghost iss-fix">Fix</button></div>`).join("")
    : "";  // the count beside the label says there are none
  for (const row of $("iss-edit").querySelectorAll(".iss")) {
    const w = list[Number(row.dataset.w)], target = { table: w.table, id: w.key, column: w.column, note: w.message };
    const pick = () => { store.set({ fixTarget: target }); focusRecord(w.table, w.key); };
    row.onclick = e => { if (!e.target.closest(".iss-fix")) pick(); };
    onRowKey(row, pick);
    row.querySelector(".iss-fix").onclick = () => fix(target);
  }
}

async function loadMarkers() {
  const id = activeId();
  if (!id || !issueMeta(store.get().server)) { store.set({ issues: null }); return; }
  const j = await getJSON(netPath(id, "issues/markers"));
  if (activeId() === id) store.set({ issues: { ...j, net_id: id } });
}

async function loadList() {
  const id = activeId(), mine = ++seq, s = store.get();
  if (!id || !issueMeta(s.server)) {
    $("iss-list").innerHTML = '<span class="empty">Run validation to list issues.</span>';
    $("iss-unlocated").innerHTML = ""; $("iss-range").textContent = ""; $("iss-unloc-count").textContent = "0";
    $("iss-prev").disabled = true; $("iss-next").disabled = true;
    return;
  }
  const f = s.issueFilter, record = f.thisRecord && s.focus ? s.focus : null;
  const [page, unlocated] = await Promise.all([
    getJSON(netPath(id, `issues?${issuesQuery({ ...f, record }, offset, PAGE)}`)),
    getJSON(netPath(id, `issues?${issuesQuery({ located: "no" }, 0, 50)}`)),
  ]);
  if (mine !== seq) return;
  renderFacets(page.facets, f);
  $("iss-list").innerHTML = page.issues.map(issueHTML).join("") || '<span class="empty">No issues match.</span>';
  $("iss-range").textContent = rangeLabel(offset, page.issues.length, page.total);
  $("iss-prev").disabled = offset <= 0;
  $("iss-next").disabled = offset + PAGE >= page.total;
  $("iss-unloc-count").textContent = unlocated.total;
  const more = unlocated.total - unlocated.issues.length;
  $("iss-unlocated").innerHTML = unlocated.issues.map(issueHTML).join("") + (more > 0 ? `<div class="empty">and ${more} more</div>` : "");
  wireRows($("iss-list"), page.issues);
  wireRows($("iss-unlocated"), unlocated.issues);
  markFocusedIssue();
}

function fill(select, options, value) {
  select.innerHTML = options.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("");
  select.value = value;
}

function renderFacets(facets, f) {
  fill($("iss-sev"), [["", "All severities"], ...SEVERITIES.map(s => [s, `${s} (${(facets.severity || {})[s] || 0})`])], f.severity[0] || "");
  fill($("iss-table"), [["", "All tables"], ...Object.entries(facets.table || {}).map(([t, n]) => [t, `${t} (${n})`])], f.table);
  fill($("iss-code"), [["", "All codes"], ...Object.entries(facets.code || {}).map(([c, n]) => [c, `${c} (${n})`])], f.code);
}

function issueHTML(i) {
  const where = i.key != null ? `${i.table} ${i.key}` : i.table || "";
  const button = i.fixable ? `<button class="mini ghost iss-fix">Fix</button>` : "";
  return `<div class="iss sev-${esc(i.severity)}" data-i="${esc(i.i)}" tabindex="0"><span class="sev-dot" aria-hidden="true"></span>` +
    `<div class="iss-body"><div><b>${esc(i.code)}</b> <span class="diag">${esc(where)}${i.column ? ` · ${esc(i.column)}` : ""}</span></div>` +
    `<div class="iss-msg">${esc(i.message)}</div>${i.fix_hint ? `<div class="diag">${esc(i.fix_hint)}</div>` : ""}</div>${button}</div>`;
}

const fixTargetOf = i => (i.fixable ? { table: i.table, id: i.key, column: i.column, note: `${i.code}: ${i.message}` } : null);

function wireRows(el, issues) {
  const byI = new Map(issues.map(i => [String(i.i), i]));
  for (const row of el.querySelectorAll(".iss")) {
    const issue = byI.get(row.dataset.i);
    row.onclick = e => { if (!e.target.closest(".iss-fix")) selectIssue(issue); };
    onRowKey(row, () => selectIssue(issue));
    const button = row.querySelector(".iss-fix");
    if (button) button.onclick = () => fix(fixTargetOf(issue));
  }
}

// An issue in the list: mark it, make it the fix target, and focus its record. The map flies there and the table
// scrolls to its row.
function selectIssue(issue) {
  const a = issue.anchor || {};
  store.set({ issueFocus: issue.i, fixTarget: fixTargetOf(issue) });
  if (issue.key != null) focusRecord(issue.table, issue.key);
  else if (a.link != null) focusRecord("link", a.link);
  else if (a.node != null) focusRecord("node", a.node);
  else if (a.lonlat) flyTo(a.lonlat[0], a.lonlat[1]);
}

// A marker clicked on the map: open the Issues panel filtered to that record, with the issue marked.
export function onIssueMarker(m) {
  showPanel("issues");
  const patch = { issueFocus: m.i };
  if (m.key != null) {
    patch.focus = { table: m.table, id: m.key, from: "map" };
    patch.issueFilter = { ...store.get().issueFilter, thisRecord: true };
  }
  store.set(patch);
}

export function markFocusedIssue() {
  const i = store.get().issueFocus;
  for (const row of document.querySelectorAll("#dock-issues .iss[data-i]")) {
    const on = row.dataset.i === String(i);
    row.classList.toggle("on", on);
    if (on) row.scrollIntoView({ block: "nearest" });
  }
}

// The markers: a dot per located issue, coloured by severity; the picked issue is drawn larger. A core factory
// gets the whole store state as ctx.state (map.js layerContext). Positions are cached per decoded network and
// issue set, so an edit (a new version) re-places them; the set itself stays the one validated, until re-run.
let cache = { net: null, issues: null, points: [] };
function issueLayer(c) {
  const s = c.state;
  if (!s.issues || !c.net || s.issues.net_id !== s.server.active) return null;
  if (cache.net !== c.net || cache.issues !== s.issues) cache = { net: c.net, issues: s.issues, points: markerPoints(s.issues.markers, c.net) };
  return new c.deck.ScatterplotLayer({ id: "issues", data: cache.points, getPosition: p => p.position,
    getRadius: p => (p.i === s.issueFocus ? 8 : 4.5), radiusUnits: "pixels", stroked: true, lineWidthMinPixels: 1,
    getFillColor: p => [...SEVERITY_RGB[p.severity], 230], getLineColor: [17, 21, 26],
    updateTriggers: { getRadius: s.issueFocus }, pickable: true, parameters: { depthTest: false },
    onClick: info => { if (info && info.object) onIssueMarker(info.object); } });
}

// Between the highlights (400) and the focus (500); titled, so Layers → Overlays lists an "Issues" switch.
export function registerIssueLayer() {
  addLayer(CORE, "roadway", "issues", issueLayer, { order: 450, title: "Issues" });
}

function setFilter(patch) { store.set({ issueFilter: { ...store.get().issueFilter, ...patch } }); }

export function wireIssues() {
  $("iss-run").onclick = () => runCore("run_validation");
  $("iss-report").onclick = e => exportReport(e).catch(fail);
  $("iss-sev").onchange = e => setFilter({ severity: e.target.value ? [e.target.value] : [] });
  $("iss-table").onchange = e => setFilter({ table: e.target.value });
  $("iss-code").onchange = e => setFilter({ code: e.target.value });
  $("iss-this").onchange = e => setFilter({ thisRecord: e.target.checked });
  $("iss-prev").onclick = () => { offset = Math.max(0, offset - PAGE); reloadIssues(); };
  $("iss-next").onclick = () => { offset += PAGE; reloadIssues(); };
}
