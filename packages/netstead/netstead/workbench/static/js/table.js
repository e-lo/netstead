// Data-table view: a server-paged/sorted/filtered grid linked both ways to the map. A row click focuses
// the record (never a recorded selection: see linking.js); the scope menu filters to the selection, the
// highlights, the records related to them, or the links the recorded Filter matches; related rows are tinted; FK
// cells jump to their target.
import { dispatch, getJSON, netPath, postJSON } from "./api.js";
import { openContextMenu } from "./ctxmenu.js";
import { $, esc, toast } from "./dom.js";
import { cellAction, cellEditable, cellOptions, cellSample, cellWarnings, listOptions, shouldApplyOnLeave } from "./editmodel.js";
import { createGridFilter } from "./gridfilter.js";
import { HOPS_TIP, hopsFromValue, hopsView } from "./hops.js";
import { icon } from "./icons.js";
import { recordIssues } from "./issuelist.js";
import { clampOffset, coerceId, combinedFilter, pageOffset, rowMarks, rowsRequest, scopeHint } from "./linking.js";
import { resizeSoon } from "./map.js";
import { renderRelatedBadges } from "./related.js";
import { activeSelection, store } from "./store.js";
import { rememberView, restoredView } from "./tabs.js";

// `filters` is the table's column-filter model (gridfilter.js): the header's funnel buttons open the `#col-filter`
// popover that sets one column's spec, the `#tbl-chips` strip shows and removes them, `query()` sends its conditions.
const TBL = { loaded: false, name: null, schema: null, offset: 0, limit: 100, sort: null, dir: "asc",
  filters: createGridFilter(), total: 0, seq: 0 };
const VIEWS_KEY = "netstead.views";         // workspace id -> its view (map | split | table)
const LEGACY_VIEW_KEY = "netstead.viewmode"; // the one view remembered before workspaces
const SEARCH_ABOVE = 12; // a value checklist longer than this gets a search box
let located = null;
// The open column-filter popover: its column and (for a value list) the distinct values loaded for it.
let pop = null;
// Row reloads are coalesced (a box-select changes highlights, focus and related in one burst), and skipped
// while the table is hidden: `dirty` makes the next showing reload.
let rowsTimer = null, pendingRestart = false, dirty = false;
// The cell open for editing ({td, html, busy}). Rows never re-render under it (that would drop what is being typed);
// they catch up when it closes.
let editing = null;
// A text column's editor options ("<table>:<column>" -> {values, listLike}) from `/distinct`, kept until the network
// changes (an edit may add a value).
const optionsCache = new Map();

const activeId = () => { const s = store.get().server; return s && s.active; };
const fail = e => toast(e.message);
const tablePath = rest => netPath(activeId(), `table/${encodeURIComponent(TBL.name)}/${rest}`);

export const tableVisible = () => $("stage").dataset.mode !== "map";
export const tableShowing = name => tableVisible() && TBL.name === name;
export const currentViewMode = () => $("stage").dataset.mode;

// Each workspace's last view, as saved in this browser ({} when none or unreadable).
export function savedViews() {
  try {
    const v = JSON.parse(localStorage.getItem(VIEWS_KEY) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch (e) { return {}; } // storage unavailable or garbled
}

// Saved under the active workspace: switching to a plugin workspace with a layout never changes Inspect's view.
export function setViewMode(mode) {
  $("stage").dataset.mode = mode;
  for (const b of document.querySelectorAll("#viewmode button")) b.classList.toggle("on", b.dataset.mode === mode);
  try {
    localStorage.setItem(VIEWS_KEY, JSON.stringify(rememberView(savedViews(), store.get().workspace, mode)));
  } catch (e) { /* storage unavailable: mode just isn't remembered */ }
  if (mode !== "map" && !TBL.loaded) loadTables().catch(fail);
  else if (mode !== "map" && dirty) refreshRows();
  resizeSoon();
}

export function onNetworkChanged() {
  Object.assign(TBL, { loaded: false, name: null, schema: null });
  TBL.seq++; clearTimeout(rowsTimer); // drop pages still loading for the old network
  optionsCache.clear();
  TBL.filters.clear(); closeFilterPopover(); renderChips();
  $("tbl-rail").innerHTML = ""; $("tbl-grid").innerHTML = ""; $("tbl-name").textContent = "—";
  if (tableVisible()) loadTables().catch(fail);
}

// The same network at a new version (an edit or an undo): refresh the rail's row counts and the rows in place,
// keeping the table, page, sort, filters and scope.
export function onNetworkEdited() {
  optionsCache.clear();
  if (!TBL.loaded) return;
  refreshRail().catch(fail);
  refreshRows();
}

async function refreshRail() {
  const id = activeId();
  if (!id) return;
  const j = await getJSON(netPath(id, "tables"));
  const counts = new Map(j.tables.map(t => [t.name, t.rows]));
  for (const el of document.querySelectorAll(".tbl-item")) {
    const n = counts.get(el.dataset.name), rc = el.querySelector(".rc");
    if (rc && n != null) rc.textContent = n.toLocaleString();
  }
}

// Anything a page depends on changed (selection, highlights, focus, scope, hops, related): reload from the first page
// when the set of rows may differ, else in place.
export function refreshRows({ restart = false } = {}) {
  if (!TBL.schema) return;
  pendingRestart ||= restart;
  if (!tableVisible() || editing) { dirty = true; return; }
  clearTimeout(rowsTimer);
  rowsTimer = setTimeout(() => {
    if (!TBL.schema) return; // the network changed meanwhile
    if (pendingRestart) TBL.offset = 0;
    pendingRestart = false; dirty = false;
    loadRows().catch(fail);
  }, 100);
}

async function loadTables() {
  const id = activeId();
  if (!id) return;
  TBL.loaded = true;
  const j = await getJSON(netPath(id, "tables"));
  const rail = $("tbl-rail");
  rail.innerHTML = "";
  for (const t of j.tables) {
    const el = document.createElement("div");
    el.className = "tbl-item"; el.dataset.name = t.name;
    el.innerHTML = `<span>${esc(t.name)}</span><span class="rc">${t.rows.toLocaleString()}</span><span class="rel-badge"></span>`;
    el.onclick = () => selectTable(t.name).catch(fail);
    rail.appendChild(el);
  }
  renderRelatedBadges(store.get().related);
  if (j.tables.length) await selectTable(TBL.name || j.tables[0].name);
}

export async function selectTable(name) {
  Object.assign(TBL, { name, offset: 0, sort: null, dir: "asc" });
  TBL.filters.clear(); closeFilterPopover(); renderChips();
  located = null;
  for (const el of document.querySelectorAll(".tbl-item")) el.classList.toggle("on", el.dataset.name === name);
  TBL.schema = await getJSON(netPath(activeId(), `table/${encodeURIComponent(name)}/schema`));
  $("tbl-name").textContent = name;
  buildGridHeader();
  await loadRows();
}

const gridColumns = () => TBL.schema.columns.filter(c => c.kind !== "geom");

// Each column's header: its name (a click sorts), the sort caret, and a funnel that opens the filter popover (lit
// while that column has a filter).
function buildGridHeader() {
  const active = new Set(TBL.filters.active());
  const arrow = c => (TBL.sort === c ? (TBL.dir === "asc" ? "▲" : "▼") : "");
  const th = gridColumns().map(c => {
    const name = esc(c.name), tip = esc(`Filter ${c.name}`);
    return `<th><span class="cn" data-col="${name}">${name}</span><span class="ar">${arrow(c.name)}</span>` +
      `<button class="iconbtn fbtn${active.has(c.name) ? " on" : ""}" data-col="${name}" data-tip="${tip}" aria-label="${tip}">` +
      `${icon("filter", { size: 13 })}</button></th>`;
  }).join("");
  $("tbl-grid").innerHTML = `<thead><tr>${th}</tr></thead><tbody></tbody>`;
  for (const el of document.querySelectorAll("#tbl-grid .cn")) el.onclick = () => toggleSort(el.dataset.col);
  for (const el of document.querySelectorAll("#tbl-grid .fbtn")) el.onclick = e => {
    e.stopPropagation();
    if (pop && pop.col === el.dataset.col) closeFilterPopover(); else openFilterPopover(el, el.dataset.col).catch(fail);
  };
}

function toggleSort(col) {
  if (TBL.sort === col) TBL.dir = TBL.dir === "asc" ? "desc" : "asc"; else { TBL.sort = col; TBL.dir = "asc"; }
  buildGridHeader(); refreshRows({ restart: true });
}

// A column filter changed: the header's funnels and the chip strip follow, and the rows reload from the first page.
function filtersChanged() {
  buildGridHeader(); renderChips();
  refreshRows({ restart: true });
}

// ---- the column-filter popover ----

const NUM_OP_OPTIONS = [["eq", "="], ["ne", "≠"], ["lt", "<"], ["lte", "≤"], ["gt", ">"], ["gte", "≥"],
  ["between", "between"], ["isnull", "is empty"], ["notnull", "is not empty"]];
const EMPTY_OPS = new Set(["isnull", "notnull"]);
const options = (pairs, chosen) => pairs.map(([v, t]) => `<option value="${v}"${v === chosen ? " selected" : ""}>${t}</option>`).join("");

// Close the popover; `restore` puts the focus back on the funnel that opened it (not after an outside click,
// which has its own target).
function closeFilterPopover({ restore = true } = {}) {
  const el = $("col-filter");
  if (el.hidden) return;
  el.hidden = true; el.innerHTML = "";
  const btn = pop && document.querySelector(`#tbl-grid .fbtn[data-col="${CSS.escape(pop.col)}"]`);
  pop = null;
  if (restore && btn) btn.focus();
}

// Open the popover under `btn` for `col`, prefilled from its current filter. A text or boolean column's value
// list comes from the `/distinct` route; the popover shows at once and fills in when it arrives.
async function openFilterPopover(btn, col) {
  const column = TBL.schema.columns.find(c => c.name === col);
  if (!column) return;
  const el = $("col-filter"), spec = TBL.filters.get(col);
  // A text column's popover opens on the tab its current filter uses (a value list, "contains" or the empty test).
  const tab = spec && spec.kind !== "set" ? spec.kind : "values";
  pop = { col, kind: column.kind, spec, tab, listLike: false, values: null, truncated: false };
  renderPopover();
  el.hidden = false;
  placePopover(btn);
  focusPopover();
  if (column.kind === "num") return;
  const mine = pop;
  let j;
  try { j = await getJSON(tablePath("distinct") + `?col=${encodeURIComponent(col)}`); }
  catch (e) { if (pop === mine) closeFilterPopover(); throw e; } // never left saying "Loading…": the toast says why
  if (pop !== mine) return; // closed or moved to another column meanwhile
  Object.assign(pop, { values: j.values, listLike: Boolean(j.list_like), truncated: Boolean(j.truncated) });
  renderPopover();
  placePopover(btn);
  if (!el.contains(document.activeElement)) focusPopover(); // the loading body had nothing to focus
}

function placePopover(btn) {
  const el = $("col-filter"), r = btn.getBoundingClientRect();
  el.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - el.offsetWidth - 8))}px`;
  el.style.top = `${Math.min(r.bottom + 4, window.innerHeight - el.offsetHeight - 8)}px`;
}

function focusPopover() {
  const first = $("col-filter").querySelector("input:not([type=checkbox]):not([hidden]), select");
  if (first) first.focus();
}

function renderPopover() {
  const el = $("col-filter"), { col, kind, spec } = pop;
  const head = `<div class="cf-head"><span class="cf-col">${esc(col)}</span>` +
    `<button class="iconbtn cf-close" data-tip="Close" aria-label="Close">${icon("x", { size: 14 })}</button></div>`;
  const foot = `<div class="cf-foot"><button class="mini cf-apply">Apply</button>` +
    `<button class="mini ghost cf-clear"${spec ? "" : " disabled"}>Clear</button></div>`;
  el.innerHTML = head + (kind === "num" ? numBody(spec) : textBody(spec)) + foot;
  el.querySelector(".cf-close").onclick = () => closeFilterPopover();
  el.querySelector(".cf-apply").onclick = () => applyPopover();
  el.querySelector(".cf-clear").onclick = () => { TBL.filters.remove(col); closeFilterPopover(); filtersChanged(); };
  const op = el.querySelector(".cf-op");
  if (op) { op.onchange = () => syncNumInputs(); syncNumInputs(); }
  for (const t of el.querySelectorAll(".cf-tabs button")) t.onclick = () => { pop.tab = t.dataset.tab; renderPopover(); focusPopover(); };
  const search = el.querySelector(".cf-search");
  if (search) search.oninput = () => {
    const q = search.value.trim().toLowerCase();
    for (const row of el.querySelectorAll(".cf-list label")) row.hidden = q !== "" && !row.dataset.v.toLowerCase().includes(q);
  };
}

function numBody(spec) {
  const op = spec ? spec.op : "eq";
  const a = spec && spec.kind === "num" ? (spec.op === "between" ? spec.lo : spec.val) : "";
  const b = spec && spec.kind === "num" && spec.op === "between" ? spec.hi : "";
  return `<div class="cf-row"><select class="cf-op" aria-label="Operator">${options(NUM_OP_OPTIONS, op)}</select>` +
    `<input type="number" step="any" class="cf-a" aria-label="Value" value="${esc(a)}">` +
    `<span class="cf-and">and</span><input type="number" step="any" class="cf-b" aria-label="High value" value="${esc(b)}"></div>`;
}

// The second number input shows only for `between`; neither shows for the empty tests.
function syncNumInputs() {
  const el = $("col-filter"), op = el.querySelector(".cf-op").value;
  el.querySelector(".cf-a").hidden = EMPTY_OPS.has(op);
  el.querySelector(".cf-b").hidden = op !== "between";
  el.querySelector(".cf-and").hidden = op !== "between";
}

function textBody(spec) {
  const { tab } = pop;
  const tabs = [["values", "Values"], ["text", "Contains"], ["empty", "Empty"]].map(([k, t]) =>
    `<button type="button" data-tab="${k}" class="${k === tab ? "on" : ""}">${t}</button>`).join("");
  let body;
  if (tab === "text") {
    body = `<div class="cf-row"><input class="cf-text" placeholder="text the value contains" aria-label="Text the value contains" ` +
      `value="${esc(spec && spec.kind === "text" ? spec.val : "")}"></div>`;
  } else if (tab === "empty") {
    const op = spec && spec.kind === "empty" ? spec.op : "isnull";
    body = `<div class="cf-row"><select class="cf-empty" aria-label="Empty test">${options([["isnull", "is empty"], ["notnull", "is not empty"]], op)}</select></div>`;
  } else body = valuesBody(spec);
  return `<div class="cf-tabs" role="tablist">${tabs}</div>${body}`;
}

function valuesBody(spec) {
  const { values, listLike, truncated } = pop;
  if (!values) return `<div class="cf-row empty">Loading values…</div>`;
  const chosen = new Set(spec && spec.kind === "set" ? spec.values : []);
  const mode = spec && spec.kind === "set" ? spec.mode : "any_of";
  const modes = [["any_of", "any of"], ...(listLike ? [["all_of", "all of"]] : []), ["none_of", "none of"]];
  const list = values.map(v => `<label data-v="${esc(v)}"><input type="checkbox" value="${esc(v)}"${chosen.has(v) ? " checked" : ""}> ${esc(v)}</label>`).join("");
  return `<div class="cf-row"><select class="cf-mode" aria-label="Match">${options(modes, mode)}</select>` +
    (values.length > SEARCH_ABOVE ? `<input class="cf-search" placeholder="search values" aria-label="Search values">` : "") + `</div>` +
    `<div class="cf-list">${list || '<span class="empty">no values</span>'}</div>` +
    (truncated ? `<div class="cf-note">Only the first ${values.length} values are listed.</div>` : "");
}

// Read the popover into a spec; a malformed one (no number, no values picked) is refused with a toast and the
// popover stays open.
function applyPopover() {
  const el = $("col-filter"), { col, kind, tab, listLike } = pop;
  let spec;
  if (kind === "num") {
    const op = el.querySelector(".cf-op").value, a = el.querySelector(".cf-a").value, b = el.querySelector(".cf-b").value;
    if (EMPTY_OPS.has(op)) spec = { kind: "empty", op };
    else if (op === "between") spec = { kind: "num", op, lo: a === "" ? NaN : Number(a), hi: b === "" ? NaN : Number(b) };
    else spec = { kind: "num", op, val: a === "" ? NaN : Number(a) };
  } else if (tab === "text") spec = { kind: "text", val: el.querySelector(".cf-text").value };
  else if (tab === "empty") spec = { kind: "empty", op: el.querySelector(".cf-empty").value };
  else {
    const mode = el.querySelector(".cf-mode"), boxes = [...el.querySelectorAll(".cf-list input:checked")];
    spec = { kind: "set", mode: mode ? mode.value : "any_of", values: boxes.map(b => b.value), listLike, bool: kind === "bool" };
  }
  try { TBL.filters.setFilter(col, spec); } catch (e) { toast(e.message); return; }
  closeFilterPopover();
  filtersChanged();
}

// ---- the chip strip ----

function renderChips() {
  const strip = $("tbl-chips"), cols = TBL.filters.active();
  strip.hidden = cols.length === 0;
  strip.innerHTML = cols.map(c => `<span class="chip"><span class="ct">${esc(TBL.filters.chipText(c))}</span>` +
    `<button class="iconbtn cx" data-col="${esc(c)}" data-tip="Remove" aria-label="${esc(`Remove the ${c} filter`)}">${icon("x", { size: 12 })}</button></span>`).join("") +
    (cols.length >= 2 ? `<button class="chip clear">Clear all</button>` : "");
  for (const b of strip.querySelectorAll(".cx")) b.onclick = () => { TBL.filters.remove(b.dataset.col); closeFilterPopover(); filtersChanged(); };
  const clear = strip.querySelector(".clear");
  if (clear) clear.onclick = () => { TBL.filters.clear(); closeFilterPopover(); filtersChanged(); };
}

function query() {
  const s = store.get();
  const viewFilter = s.server && s.server.filters ? s.server.filters[s.server.active] : null;
  return { offset: TBL.offset, limit: TBL.limit, sort: TBL.sort, dir: TBL.dir,
    filter: combinedFilter(TBL.filters.conditions(), s.tableScope, TBL.name, viewFilter),
    ...rowsRequest({ scope: s.tableScope, table: TBL.name, selection: activeSelection(s), highlights: s.highlights,
      focus: s.focus, hops: s.relHops }) };
}

async function loadRows() {
  if (!TBL.name) return; // no table picked yet (a state push can land before the first one is chosen)
  const seq = ++TBL.seq;
  const j = await postJSON(tablePath("rows"), query());
  if (seq !== TBL.seq) return; // superseded by a newer request
  if (editing) { dirty = true; return; } // a cell opened while this page loaded: reload when it closes
  const offset = clampOffset(TBL.offset, TBL.limit, j.total);
  if (offset !== TBL.offset) { TBL.offset = offset; return loadRows(); } // the set shrank under this page
  TBL.total = j.total;
  renderRows(j.columns, j.rows, j.related || []);
  const to = Math.min(TBL.offset + TBL.limit, j.total);
  $("tbl-total").textContent = `· ${j.total.toLocaleString()} row(s)`;
  $("tbl-range").textContent = j.total ? `${TBL.offset + 1}–${to} of ${j.total.toLocaleString()}` : "0";
  $("tbl-hint").textContent = scopeHint(store.get().tableScope, j.total, TBL.name);
  $("tbl-prev").disabled = TBL.offset <= 0;
  $("tbl-next").disabled = to >= j.total;
  await revealFocus();
}

// A grid cell. A navigable foreign key is a link to its row; in the focused row the value is plain text (a click
// edits it) and a small ↗ does the jump.
function cellHTML(value, fk, focused = false) {
  if (value === null) return '<span class="empty">·</span>';
  if (fk && fk.navigable) {
    const link = `<a class="fk${focused ? " go" : ""}" href="#" data-ref="${esc(fk.ref_table)}" data-id="${esc(value)}" ` +
      `data-num="${typeof value === "number" ? 1 : ""}" title="Go to ${esc(fk.ref_table)} ${esc(value)}">${focused ? "↗" : esc(value)}</a>`;
    return focused ? `${esc(value)} ${link}` : link;
  }
  return esc(value);
}

function renderRows(cols, rows, vias) {
  TBL.cols = cols; TBL.rows = rows; // the page, for the cell editor
  const s = store.get(), pk = TBL.schema.primary_key, pkIdx = cols.indexOf(pk);
  const fks = new Map((TBL.schema.foreign_keys || []).map(f => [f.column, f]));
  const selection = activeSelection(s);
  const body = $("tbl-grid").tBodies[0];
  const records = s.issues && s.issues.net_id === activeId() ? s.issues.records : null;
  const warned = s.edits && s.edits.net_id === activeId() ? cellWarnings(s.edits.warnings, TBL.name) : {};
  const schemaCols = new Map(TBL.schema.columns.map(c => [c.name, c]));
  body.innerHTML = rows.map((r, i) => {
    const pv = pkIdx >= 0 ? r[pkIdx] : null;
    const nIss = recordIssues(records, TBL.name, pv).length, rw = warned[String(pv)];
    const focused = Boolean(s.focus && s.focus.table === TBL.name && s.focus.id === pv);
    const marks = rowMarks({ table: TBL.name, id: pv, selection, highlights: s.highlights, focus: s.focus, via: vias[i],
      issues: nIss, warned: Boolean(rw) });
    const tip = [vias[i] ? `related via ${vias[i]}` : "", nIss ? `${nIss} issue${nIss === 1 ? "" : "s"}` : "",
      focused ? "click a cell to edit it" : ""].filter(Boolean).join(" · ");
    const title = tip ? ` title="${esc(tip)}"` : "";
    // A cell with an open edit warning (an out-of-enum value, a missing key) is outlined, carries the message as
    // its tooltip and a warning icon after its text: a kept-but-warned edit is never mistaken for a dropped one.
    const tds = r.map((v, c) => {
      const msgs = rw && rw[cols[c]];
      const cls = [msgs ? "warn" : "", cellEditable(schemaCols.get(cols[c]), pk) ? "" : "ro"].filter(Boolean).join(" ");
      return `<td data-c="${c}"${cls ? ` class="${cls}"` : ""}${msgs ? ` title="${esc(msgs.join("\n"))}"` : ""}>` +
        `${cellHTML(v, fks.get(cols[c]), focused)}${msgs ? icon("alert-triangle", { size: 12 }) : ""}</td>`;
    }).join("");
    return `<tr class="data ${marks.join(" ")}" data-pk="${pv == null ? "" : esc(pv)}"${title}>${tds}</tr>`;
  }).join("");
  for (const tr of body.querySelectorAll("tr.data")) tr.onclick = e => {
    const td = e.target.closest("td[data-c]");
    if (tr.classList.contains("focus") && td && !td.classList.contains("ro")) startCellEdit(tr, td);
    else rowClick(tr.dataset.pk);
  };
  for (const tr of body.querySelectorAll("tr.data")) tr.oncontextmenu = e => {
    if (tr.dataset.pk === "" || !TBL.schema.primary_key) return;
    const target = { table: TBL.name, id: coerceId(tr.dataset.pk, pkNumeric()) };
    if (openContextMenu("row", target, { x: e.clientX, y: e.clientY })) e.preventDefault();
  };
  for (const a of body.querySelectorAll("a.fk")) a.onclick = e => {
    e.preventDefault(); e.stopPropagation();
    jumpTo(a.dataset.ref, coerceId(a.dataset.id, Boolean(a.dataset.num))).catch(fail);
  };
}

// A row click focuses that record. It never dispatches an action, so the recorded selection (and a
// "Selection" scope built on it) is never collapsed to one row.
function rowClick(pkVal) {
  if (pkVal === "" || !TBL.schema || !TBL.schema.primary_key) return;
  store.set({ focus: { table: TBL.name, id: coerceId(pkVal, pkNumeric()), from: "table" } });
}

// The values a text column's editor offers: the spec's allowed values at once, the column's existing values (or,
// for a comma list, its tokens) when `/distinct` answers; cached per table and column until the network changes.
async function columnOptions(col) {
  const key = `${TBL.name}:${col.name}`;
  if (!optionsCache.has(key)) {
    const j = await getJSON(tablePath("distinct") + `?col=${encodeURIComponent(col.name)}`);
    optionsCache.set(key, { values: cellOptions(col, j.values), listLike: Boolean(j.list_like) });
  }
  return optionsCache.get(key);
}

const fillOptions = values => { $("cell-opts").innerHTML = values.map(v => `<option value="${esc(v)}">`).join(""); };

// Edit one cell of the focused row in place: Enter, or leaving the cell with changed text (a spreadsheet's
// convention), applies an EditCells action; Escape discards. A text column's input completes from the spec's
// allowed values and the column's existing ones; a comma-list column completes its last token. Text that does not
// parse (a bad yes/no) is refused before anything is sent, and a refusal from the server keeps the input open with
// what was typed: a failed entry never leaves the old value looking applied, nor sends it. Leaving again with the
// same refused text closes the editor (not a loop of refusals the user cannot escape).
function startCellEdit(tr, td) {
  if (editing) return;
  const c = Number(td.dataset.c), column = TBL.cols[c], row = TBL.rows[[...tr.parentNode.children].indexOf(tr)];
  const col = TBL.schema.columns.find(x => x.name === column);
  if (!row || !cellEditable(col, TBL.schema.primary_key)) return;
  const current = row[c], id = coerceId(tr.dataset.pk, pkNumeric());
  const me = editing = { td, html: td.innerHTML, busy: false };
  const text = col.kind === "str";
  td.innerHTML = `<input class="cell-in"${text ? ' list="cell-opts"' : ""} aria-label="${esc(`${column} of ${TBL.name} ${id}`)}">`;
  const input = td.querySelector("input");
  input.value = current == null ? "" : String(current);
  if (text) {
    fillOptions(cellOptions(col, null)); // the spec's values before the column's arrive
    columnOptions(col).then(opts => {
      if (editing !== me) return;
      if (!opts.listLike) { fillOptions(opts.values); return; }
      td.insertAdjacentHTML("beforeend", '<span class="cell-hint">comma-separated</span>');
      const complete = () => fillOptions(listOptions(input.value, opts.values));
      input.oninput = complete;
      complete();
    }).catch(fail);
  }
  const close = () => {
    if (editing !== me || me.busy) return;
    td.innerHTML = me.html;
    editing = null;
    if (dirty) refreshRows();
  };
  // Send the typed text as one EditCells action (Enter and leaving share this). A refusal, before or from the
  // server, keeps the editor open with the text and the focus back in it.
  const apply = async () => {
    if (me.busy) return;
    let action;
    try { action = cellAction({ table: TBL.name, ids: [id], column, raw: input.value, current: cellSample(current, col.kind) }); }
    catch (err) { me.refused = input.value; toast(err.message); input.focus(); return; }
    me.busy = true; input.disabled = true;
    try {
      await dispatch(action);
      if (editing === me) editing = null; // the edit bumps the version: rows reload with the new value and warnings
      refreshRows();
    } catch (err) {
      me.busy = false; me.refused = input.value; input.disabled = false;
      toast(err.message);
      if (editing === me) input.focus();
    }
  };
  // Leaving the cell (after a grace, unless the input has the focus back: a refusal re-focuses it) applies changed
  // text, or just closes the editor when the text is unchanged or was already refused as it stands.
  const leave = () => {
    if (editing !== me || me.busy || document.activeElement === input) return;
    if (shouldApplyOnLeave(input.value, current) && input.value !== me.refused) apply();
    else close();
  };
  input.onclick = e => e.stopPropagation();
  input.oncontextmenu = e => e.stopPropagation(); // the browser's own menu (paste) inside the input, not the row menu
  input.onblur = () => setTimeout(leave, 150);
  input.onkeydown = e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    apply();
  };
  input.focus();
  input.select();
}

// FK navigation: open the referenced table and focus the referenced row (its map feature flies into view). `from`
// is the focus's origin ("table": Inspect comes forward; "panel": the asking panel stays).
export async function jumpTo(table, id, from = "table") {
  if (!document.querySelector(`.tbl-item[data-name="${CSS.escape(table)}"]`)) { toast(`${table} is not in this network`); return; }
  if (TBL.name !== table) await selectTable(table);
  store.set({ focus: { table, id, from } });
}

const pkNumeric = () => TBL.schema.columns.some(c => c.name === TBL.schema.primary_key && c.kind === "num");
const rowFor = id => [...$("tbl-grid").tBodies[0].querySelectorAll("tr.data")]
  .find(tr => coerceId(tr.dataset.pk, pkNumeric()) === id);

export function onFocusChanged() { located = null; revealFocus().catch(fail); }

// Bring the focused row into view: mark it on this page, or locate its page once and load it.
async function revealFocus() {
  const f = store.get().focus;
  for (const tr of $("tbl-grid").querySelectorAll("tr.focus")) tr.classList.remove("focus");
  if (!f || !tableShowing(f.table) || !TBL.schema) return;
  const tr = rowFor(f.id);
  if (tr) { tr.classList.add("focus"); tr.scrollIntoView({ block: "nearest" }); return; }
  const key = `${f.table}:${f.id}`;
  if (located === key) return; // already moved to its page once: it is filtered out of this view
  located = key;
  const { index } = await postJSON(tablePath("locate"), { ...query(), id: f.id });
  if (index == null) { $("tbl-hint").textContent = `${f.table} ${f.id} is not in this view.`; return; }
  TBL.offset = pageOffset(index, TBL.limit);
  await loadRows();
}

export function wireTable() {
  for (const b of document.querySelectorAll("#viewmode button")) b.onclick = () => setViewMode(b.dataset.mode);
  $("tbl-prev").onclick = () => { if (TBL.offset > 0) { TBL.offset = Math.max(0, TBL.offset - TBL.limit); refreshRows(); } };
  $("tbl-next").onclick = () => { if (TBL.offset + TBL.limit < TBL.total) { TBL.offset += TBL.limit; refreshRows(); } };
  $("tbl-scope").onchange = e => store.set({ tableScope: e.target.value });
  $("tbl-hops-wrap").title = HOPS_TIP;
  $("tbl-hops").onchange = e => store.set({ relHops: hopsFromValue(e.target.value) });
  // The filter popover: Escape closes it, Enter in a text or number box applies, a click outside closes it.
  const cf = $("col-filter");
  cf.onkeydown = e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeFilterPopover(); return; }
    if (e.key === "Enter" && e.target.matches("input:not([type=checkbox]):not(.cf-search)")) { e.preventDefault(); applyPopover(); }
  };
  document.addEventListener("pointerdown", e => {
    if (pop && !cf.contains(e.target) && !e.target.closest(".fbtn")) closeFilterPopover({ restore: false });
  }, true);
}

export function syncScopeControls(s) {
  $("tbl-scope").value = s.tableScope;
  const hops = hopsView(s.tableScope, s.relHops);
  $("tbl-hops-wrap").hidden = hops.hidden;
  $("tbl-hops").value = hops.value;
}

// At startup (Inspect is showing): Inspect's own saved view.
export function restoreViewMode() {
  let legacy = null;
  try { legacy = localStorage.getItem(LEGACY_VIEW_KEY); } catch (e) { /* storage unavailable */ }
  const m = restoredView(savedViews(), legacy);
  if (m) setViewMode(m);
}
