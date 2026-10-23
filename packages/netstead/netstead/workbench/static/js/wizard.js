// Open / Import wizard: source → (browse | URL | area → options → estimate) → exactly one dispatched action.
// Browse, URL check, place search, and estimate are read-only queries; only Open / Build are recorded actions.
import { dispatch, getJSON, postJSON } from "./api.js";
import { modesProblem, normalizeModes, toggleMode } from "./buildmodes.js";
import { areaChoice, resetAreaPicker, showAreaMap, wireAreaPicker } from "./areapicker.js";
import { $, esc, toast } from "./dom.js";
import { createFileBrowser } from "./filebrowser.js";
import { openJobsPanel } from "./jobs.js";
import { store } from "./store.js";

const FLOWS = { local: ["source", "local"], url: ["source", "url"],
  osm: ["source", "area", "options", "run"], overture: ["source", "area", "options", "run"] };
const GMNS_KINDS = ["gmns", "zip", "duckdb", "datapackage"];
const NAME_RE = /^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?$/; // BuildNetwork.name: no trailing dot
const NAME_MAX = 100;
const OUTPUT_SUFFIXES = ["zip", "duckdb", "csv", "parquet"]; // a name ending in one must match output_format
const NEXT_LABEL = { local: "Open", url: "Open", area: "Next", options: "Estimate" };

const wz = { src: null, step: "source", local: null, urlOk: false, outDir: null, estimate: null, prefilled: false,
  retry: null, autoName: null };
let localFb = null, outFb = null, estimateSeq = 0;
// The last build dispatched without approval, so an ApprovalRequired failure (the job re-estimates and may
// land over the threshold) can reopen the Run step. `early` keeps job events that beat the 202 response.
let pending = null;

const fmtSeconds = s => (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
const fmtBytes = b => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB`
  : b >= 1e3 ? `${Math.round(b / 1e3)} kB` : `${Math.round(b)} B`);

// "" when the output name is usable, else why not (mirrors BuildNetwork's validation).
function nameProblem() {
  const name = $("opt-name").value.trim();
  if (!name) return "Give the output a name.";
  if (name.length > NAME_MAX || !NAME_RE.test(name)) {
    return "Use letters, digits, '_', '-', '.'; start with a letter or digit; don't end with '.'.";
  }
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  if (OUTPUT_SUFFIXES.includes(ext) && ext !== $("opt-format").value) return `The name ends in .${ext} but the format is ${$("opt-format").value}.`;
  return "";
}

const modeBoxes = () => [...document.querySelectorAll("#opt-modes input[data-mode]")];
const chosenModes = () => modeBoxes().filter(b => b.checked).map(b => b.dataset.mode);
function setModes(modes) { for (const b of modeBoxes()) b.checked = modes.includes(b.dataset.mode); }

function showOutDir() {
  const el = $("opt-outdir");
  el.textContent = wz.outDir || "not chosen yet";
  el.classList.toggle("unset", !wz.outDir);
}

function canAdvance() {
  switch (wz.step) {
    case "local": return Boolean(wz.local);
    case "url": return wz.urlOk;
    case "area": return Boolean(areaChoice());
    case "options": return Boolean(wz.outDir) && !nameProblem() && !modesProblem(chosenModes());
    case "run": return Boolean(wz.estimate);
    default: return false;
  }
}

function runLabel() {
  const r = wz.estimate;
  if (!r) return "Build";
  if (r.estimate.seconds == null) return "Run anyway";
  return r.needs_approval ? `Run (~${fmtSeconds(r.estimate.seconds)})` : "Build";
}

function updateFoot() {
  if (wz.step === "options") {
    $("opt-name-err").textContent = ($("opt-name").value.trim() && nameProblem()) || modesProblem(chosenModes());
  }
  $("wz-back").disabled = wz.step === "source";
  $("wz-next").hidden = wz.step === "source";
  $("wz-next").textContent = wz.step === "run" ? runLabel() : NEXT_LABEL[wz.step] || "Next";
  $("wz-next").disabled = !canAdvance();
}

function showStep(step) {
  wz.step = step;
  for (const s of document.querySelectorAll(".wz-step")) s.hidden = s.dataset.step !== step;
  for (const el of document.querySelectorAll(".ovt-only")) el.hidden = wz.src !== "overture";
}

function go(step) {
  wz.retry = null;
  estimateSeq++; // leaving (or re-entering) the Run step drops any estimate still in flight
  showStep(step);
  if (step === "area") showAreaMap(store.get().basemap);
  if (step === "options") prefillOptions();
  if (step === "run") runEstimate();
  updateFoot();
}

function buildAction() {
  const tags = $("opt-extra-tags").value.split(",").map(t => t.trim()).filter(Boolean);
  const action = {
    type: "build_network", source: wz.src, ...areaChoice(),
    output_dir: wz.outDir, output_format: $("opt-format").value, name: $("opt-name").value.trim(),
    network_types: chosenModes(), spec_version: $("opt-spec").value.trim() || null,
  };
  if (tags.length) action.extra_tags = tags;
  if (wz.src === "overture" && $("opt-release").value.trim()) action.overture_release = $("opt-release").value.trim();
  return action;
}

// Settings defaults, once per page load; before the Area step so the point buffer is filled in too.
async function prefillSettings() {
  if (wz.prefilled) return;
  try {
    const v = (await getJSON("/api/settings")).values;
    setModes(normalizeModes(v.build.network_type));
    $("opt-extra-tags").value = v.build.extra_tags.join(", ");
    $("opt-spec").value = v.io.spec_version;
    $("opt-format").value = v.io.default_format;
    $("opt-release").placeholder = v.overture.release || "pinned default";
    $("ap-buffer").value = v.build.buffer_m;
    wz.prefilled = true;
  } catch (e) { toast(e.message); }
}

function prefillOptions() {
  outFb.reset(wz.outDir);
  showOutDir();
  const choice = areaChoice();
  const base = choice && choice.area && choice.area.kind === "place" ? choice.area.name.split(",")[0] : "network";
  // Fill the name in, and keep it current after Back + a new source or place, until the user types their own.
  const current = $("opt-name").value;
  if (!current || current === wz.autoName) {
    wz.autoName = `${base}-${wz.src}`.toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^[^a-z0-9]+/, "");
    $("opt-name").value = wz.autoName;
  }
}

function renderEstimate(r) {
  const e = r.estimate;
  $("wz-estimate").innerHTML = e.seconds == null
    ? `<b>No estimate.</b> <span class="muted">${esc(e.basis)}</span><br>You can still run it, but it may take a long time.`
    : `<b>About ${fmtSeconds(e.seconds)}</b>${e.out_bytes == null ? "" : `, ~${fmtBytes(e.out_bytes)} on disk`}.` +
      `<br><span class="muted">${esc(e.basis)}</span>` +
      (r.needs_approval ? `<br>That is over the ${fmtSeconds(r.threshold_s)} approval threshold (<code>app.approve_above_s</code>).` : "");
}

async function runEstimate() {
  const seq = ++estimateSeq; // after Back + Estimate again, only the newest answer may land
  wz.estimate = null;
  $("wz-estimate").textContent = "Estimating…";
  updateFoot();
  try {
    const r = await postJSON("/api/estimate", buildAction());
    if (seq !== estimateSeq) return;
    wz.estimate = r;
    renderEstimate(r);
  } catch (e) {
    if (seq !== estimateSeq) return;
    $("wz-estimate").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
  updateFoot();
}

async function checkUrl() {
  wz.urlOk = false;
  updateFoot();
  const url = $("wz-url").value.trim();
  if (!url) return;
  $("wz-url-result").textContent = "Checking…";
  try {
    const r = await postJSON("/api/check-url", { url });
    wz.urlOk = r.reachable && $("wz-url").value.trim() === url; // ignore a result for a URL since edited
    const creds = ` Credentials from: <b>${esc(r.credential_source)}</b>.`;
    $("wz-url-result").innerHTML = r.reachable
      ? `Reachable (${esc(r.kind)}).${creds}${r.tables.length ? ` Tables: ${r.tables.map(esc).join(", ")}.` : ""}`
      : `<span class="err">Not reachable: ${esc(r.error || "unknown error")}</span>${creds}`;
  } catch (e) {
    $("wz-url-result").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
  updateFoot();
}

// A build job ended ApprovalRequired: reopen the Run step with the job's own estimate; Run re-dispatches approved.
function offerApproval(job, action) {
  const p = job.payload || {};
  if (!p.estimate) return;
  estimateSeq++;
  wz.estimate = { estimate: p.estimate, needs_approval: true, threshold_s: p.threshold_s };
  $("wizard").hidden = false;
  showStep("run");
  wz.retry = action;
  renderEstimate(wz.estimate);
  updateFoot();
}

// Fed every SSE `job` event (main.js).
export function onWizardJob(job) {
  if (!pending || job.status === "running") return;
  if (pending.jobId == null) { pending.early.set(job.id, job); return; }
  if (job.id !== pending.jobId) return;
  const { action } = pending;
  pending = null;
  if (job.status === "failed" && job.error_type === "ApprovalRequired") offerApproval(job, action);
}

async function submit(action) {
  $("wz-next").disabled = true;
  const track = action.type === "build_network" && !action.approved;
  if (track) pending = { action, jobId: null, early: new Map() };
  try {
    const result = await dispatch(action);
    closeWizard();
    openJobsPanel();
    if (track && pending && pending.action === action) {
      pending.jobId = result.job_id;
      const early = pending.early.get(result.job_id);
      pending.early.clear();
      if (early) onWizardJob(early);
    }
  } catch (e) {
    if (track) pending = null;
    toast(e.message);
    updateFoot();
  }
}

function next() {
  if (wz.step === "local") submit({ type: "open_network", source: wz.local.target });
  else if (wz.step === "url") submit({ type: "open_network", source: $("wz-url").value.trim() });
  else if (wz.step === "run") submit({ ...(wz.retry || buildAction()), approved: Boolean(wz.estimate.needs_approval) });
  else go(FLOWS[wz.src][FLOWS[wz.src].indexOf(wz.step) + 1]);
}

function back() {
  const flow = FLOWS[wz.src] || ["source"];
  go(flow[Math.max(flow.indexOf(wz.step) - 1, 0)]);
}

function choose(src) {
  wz.src = src;
  if (src === "local") { wz.local = null; localFb.reset(); }
  if (src === "url") { wz.urlOk = false; $("wz-url-result").textContent = ""; }
  if (src === "osm" || src === "overture") resetAreaPicker(src);
  go(FLOWS[src][1]);
}

export function openWizard() {
  Object.assign(wz, { src: null, local: null, urlOk: false, estimate: null, retry: null, autoName: null });
  $("opt-name").value = "";
  $("wizard").hidden = false;
  go("source");
  document.querySelector(".wz-choice").focus();
  prefillSettings();
}

export function closeWizard() { $("wizard").hidden = true; }

export function wireWizard() {
  localFb = createFileBrowser($("wz-local-fb"), { kinds: GMNS_KINDS, onPick: e => { wz.local = e; updateFoot(); } });
  outFb = createFileBrowser($("opt-out-fb"), {
    kinds: [], pickFolder: true, onPick: e => { wz.outDir = e.path; showOutDir(); updateFoot(); },
  });
  wireAreaPicker(updateFoot);
  for (const b of document.querySelectorAll(".wz-choice")) b.onclick = () => choose(b.dataset.src);
  $("wz-next").onclick = next;
  $("wz-back").onclick = back;
  $("wz-close").onclick = closeWizard;
  $("wz-check").onclick = checkUrl;
  $("wz-url").oninput = () => { wz.urlOk = false; updateFoot(); };
  $("wz-url").onkeydown = e => { if (e.key === "Enter") checkUrl(); };
  $("opt-name").oninput = updateFoot;
  $("opt-modes").onchange = e => { setModes(toggleMode(chosenModes(), e.target.dataset.mode, e.target.checked)); updateFoot(); };
  $("opt-format").onchange = updateFoot;
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("wizard").hidden) closeWizard(); });
}
