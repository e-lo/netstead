// Data-table view: a server-paged/sorted/filtered grid linked both ways to the map. A row click focuses
// the record (never a recorded selection: see linking.js); the scope menu filters to the selection, the
// highlights, or the records related to them; related rows are tinted; FK cells jump to their target.
import { getJSON, netPath, postJSON } from "./api.js";
import { openContextMenu } from "./ctxmenu.js";
import { $, esc, toast } from "./dom.js";
import { clampOffset, coerceId, pageOffset, rowMarks, rowsRequest, scopeHint } from "./linking.js";
import { resizeSoon } from "./map.js";
import { renderRelatedBadges } from "./related.js";
import { activeSelection, store } from "./store.js";
import { rememberView, restoredView } from "./tabs.js";

const TBL = { loaded: false, name: null, schema: null, offset: 0, limit: 100, sort: null, dir: "asc", filters: {},
  total: 0, seq: 0 };
const VIEWS_KEY = "netstead.views";         // workspace id -> its view (map | split | table)
const LEGACY_VIEW_KEY = "netstead.viewmode"; // the one view remembered before workspaces
let filterTimer = null, located = null;
// Row reloads are coalesced (a box-select changes highlights, focus and related in one burst), and skipped
// while the table is hidden: `dirty` makes the next showing reload.
let rowsTimer = null, pendingRestart = false, dirty = false;

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
  $("tbl-rail").innerHTML = ""; $("tbl-grid").innerHTML = ""; $("tbl-name").textContent = "—";
  if (tableVisible()) loadTables().catch(fail);
}

// Anything a page depends on changed (selection, highlights, focus, scope, hops, related): reload from the first page
// when the set of rows may differ, else in place.
export function refreshRows({ restart = false } = {}) {
  if (!TBL.schema) return;
  pendingRestart ||= restart;
  if (!tableVisible()) { dirty = true; return; }
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
  Object.assign(TBL, { name, offset: 0, sort: null, dir: "asc", filters: {} });
  located = null;
  for (const el of document.querySelectorAll(".tbl-item")) el.classList.toggle("on", el.dataset.name === name);
  TBL.schema = await getJSON(netPath(activeId(), `table/${encodeURIComponent(name)}/schema`));
  $("tbl-name").textContent = name;
  buildGridHeader();
  await loadRows();
}

const gridColumns = () => TBL.schema.columns.filter(c => c.kind !== "geom");

function buildGridHeader() {
  const arrow = c => (TBL.sort === c ? `<span class="ar">${TBL.dir === "asc" ? "▲" : "▼"}</span>` : "");
  const th = gridColumns().map(c => `<th><span class="cn" data-col="${esc(c.name)}">${esc(c.name)}${arrow(c.name)}</span>` +
    `<input data-fcol="${esc(c.name)}" placeholder="filter" value="${esc(TBL.filters[c.name] || "")}"></th>`).join("");
  $("tbl-grid").innerHTML = `<thead><tr>${th}</tr></thead><tbody></tbody>`;
  for (const el of document.querySelectorAll("#tbl-grid .cn")) el.onclick = () => toggleSort(el.dataset.col);
  for (const el of document.querySelectorAll("#tbl-grid input[data-fcol]")) el.oninput = () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      const v = el.value.trim();
      if (v) TBL.filters[el.dataset.fcol] = v; else delete TBL.filters[el.dataset.fcol];
      refreshRows({ restart: true });
    }, 250);
  };
}

function toggleSort(col) {
  if (TBL.sort === col) TBL.dir = TBL.dir === "asc" ? "desc" : "asc"; else { TBL.sort = col; TBL.dir = "asc"; }
  buildGridHeader(); refreshRows({ restart: true });
}

function query() {
  const s = store.get();
  const filter = Object.entries(TBL.filters).map(([col, val]) => ({ col, op: "contains", val }));
  return { offset: TBL.offset, limit: TBL.limit, sort: TBL.sort, dir: TBL.dir, filter: filter.length ? filter : null,
    ...rowsRequest({ scope: s.tableScope, table: TBL.name, selection: activeSelection(s), highlights: s.highlights,
      focus: s.focus, hops: s.relHops }) };
}

async function loadRows() {
  const seq = ++TBL.seq;
  const j = await postJSON(tablePath("rows"), query());
  if (seq !== TBL.seq) return; // superseded by a newer request
  const offset = clampOffset(TBL.offset, TBL.limit, j.total);
  if (offset !== TBL.offset) { TBL.offset = offset; return loadRows(); } // the set shrank under this page
  TBL.total = j.total;
  renderRows(j.columns, j.rows, j.related || []);
  const to = Math.min(TBL.offset + TBL.limit, j.total);
  $("tbl-total").textContent = `· ${j.total.toLocaleString()} row(s)`;
  $("tbl-range").textContent = j.total ? `${TBL.offset + 1}–${to} of ${j.total.toLocaleString()}` : "0";
  $("tbl-hint").textContent = scopeHint(store.get().tableScope, j.total);
  $("tbl-prev").disabled = TBL.offset <= 0;
  $("tbl-next").disabled = to >= j.total;
  await revealFocus();
}

function cellHTML(value, fk) {
  if (value === null) return '<span class="empty">·</span>';
  if (fk && fk.navigable) {
    return `<a class="fk" href="#" data-ref="${esc(fk.ref_table)}" data-id="${esc(value)}" data-num="${typeof value === "number" ? 1 : ""}" ` +
      `title="Go to ${esc(fk.ref_table)} ${esc(value)}">${esc(value)}</a>`;
  }
  return esc(value);
}

function renderRows(cols, rows, vias) {
  const s = store.get(), pk = TBL.schema.primary_key, pkIdx = cols.indexOf(pk);
  const fks = new Map((TBL.schema.foreign_keys || []).map(f => [f.column, f]));
  const selection = activeSelection(s);
  const body = $("tbl-grid").tBodies[0];
  body.innerHTML = rows.map((r, i) => {
    const pv = pkIdx >= 0 ? r[pkIdx] : null;
    const marks = rowMarks({ table: TBL.name, id: pv, selection, highlights: s.highlights, focus: s.focus, via: vias[i] });
    const title = vias[i] ? ` title="related via ${esc(vias[i])}"` : "";
    const tds = r.map((v, c) => `<td>${cellHTML(v, fks.get(cols[c]))}</td>`).join("");
    return `<tr class="data ${marks.join(" ")}" data-pk="${pv == null ? "" : esc(pv)}"${title}>${tds}</tr>`;
  }).join("");
  for (const tr of body.querySelectorAll("tr.data")) tr.onclick = () => rowClick(tr.dataset.pk);
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

// FK navigation: open the referenced table and focus the referenced row (its map feature flies into view).
export async function jumpTo(table, id) {
  if (!document.querySelector(`.tbl-item[data-name="${CSS.escape(table)}"]`)) { toast(`${table} is not in this network`); return; }
  if (TBL.name !== table) await selectTable(table);
  store.set({ focus: { table, id, from: "table" } });
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
  $("tbl-hops").onclick = () => store.set({ relHops: store.get().relHops === 1 ? 2 : 1 });
}

export function syncScopeControls(s) {
  $("tbl-scope").value = s.tableScope;
  $("tbl-hops").textContent = s.relHops === 1 ? "Expand a hop" : "One hop";
  $("tbl-hops").classList.toggle("on", s.relHops > 1);
}

// At startup (Inspect is showing): Inspect's own saved view.
export function restoreViewMode() {
  let legacy = null;
  try { legacy = localStorage.getItem(LEGACY_VIEW_KEY); } catch (e) { /* storage unavailable */ }
  const m = restoredView(savedViews(), legacy);
  if (m) setViewMode(m);
}
