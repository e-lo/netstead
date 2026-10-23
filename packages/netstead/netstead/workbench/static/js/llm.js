// Language models: the header provider/model picker and the "Language models" panel.
// Keys are write-only. The browser sends a key once (PUT) and only ever reads back status:
// {provider, configured, source}. Key text never enters the store, browser storage, or a lasting DOM node.
// The panel is a section of the Settings dialog (settings.js); "Models…" opens Settings there.
import { dispatch, getJSON, sendJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { openJobsPanel } from "./jobs.js";
import { llmStatus } from "./llmstatus.js";
import { openSettings } from "./settings.js";

const SECRETS = { "X-Netstead-Secrets": "1" };
// The published setup guide (docs/cookbook/local-llm-ollama.md) and Ollama's own download page.
const OLLAMA_GUIDE = "https://e-lo.github.io/netstead/netstead/cookbook/local-llm-ollama/";
const OLLAMA_DOWNLOAD = "https://ollama.com/download";
const STUB = { provider: "stub", label: "Offline (pattern)", usable: true, local: true, sends: [] };
const TRI = [["auto", "auto (on for local models only)"], ["on", "on"], ["off", "off"]];
// llm.quality settings shown in the panel: [key, label, kind, options or bounds]. Bounds mirror config.py.
const QUALITY = [
  ["assistant_context", "Send the GMNS assistant guide", "bool"],
  ["assistant_context_max_chars", "…at most this many characters", "int", [0, 100000]],
  ["project_context", "Send project notes (NETSTEAD.md, or the ## netstead section of AGENTS.md / CLAUDE.md)", "choice", TRI],
  ["project_context_max_chars", "…at most this many characters", "int", [0, 50000]],
  ["grounding", "Send street names and route numbers from the network", "choice", TRI],
  ["grounding_max_names", "…at most this many names", "int", [1, 2000]],
  ["few_shot", "Show the model this session's earlier selections", "bool"],
  ["few_shot_max", "…at most this many", "int", [1, 10]],
  ["match_retry", "If nothing matches, retry once with only the closest real names", "choice", TRI],
  ["match_candidates", "…this many names", "int", [1, 20]],
  ["max_repairs", "Re-prompts after an invalid reply", "int", [0, 5]],
  ["temperature", "Temperature (blank = default 0.0)", "float", [0, 2]],
];

let llm = null;          // last /api/llm/providers snapshot: status only, never key values
let settings = null;     // last /api/settings payload: values, sources, paths
let savedDefault = null; // the non-session pair: seen before any session override, or what "Make default" saved
let baseSources = null;  // where that pair came from ({provider, model}: "user" | "project" | "env" | "default")
let refreshSeq = 0;      // drop out-of-order refresh responses
let modelsSeq = 0;
let modelMissing = false; // the chosen local model is not installed (set by renderModels, read by the robot status)
const pulling = new Set(); // Ollama models this tab asked to pull, until that model's own pull job ends
// "Pull {model} (Ollama)" (see routes/llm.py:_pull_label); used to recover the model id from a job event.
const PULL_JOB_LABEL = /^Pull (.+) \(Ollama\)$/;

const providerRow = name => (name === "stub" ? STUB : llm ? llm.providers.find(p => p.provider === name) : null);
const panelOpen = () => !$("settings").hidden && !$("llm-panel").hidden;
const report = e => toast(e.message);

// The model a provider uses when none is chosen: the server's resolved default (for Ollama, an installed
// tool-capable model when the catalog default isn't installed), else the catalog default.
const defaultModel = p => p.model || p.default_model;

// The model a pair really uses: none for the stub, else the explicit one or the provider's default.
function effectiveModel(provider, model) {
  if (provider === "stub") return null;
  const p = providerRow(provider);
  return model || (p ? defaultModel(p) : null);
}

export function dotClass(p) {
  if (!p) return "off";
  if (p.usable) return p.local ? "ok local" : "ok";
  return p.configured ? "warn" : "off";
}

// What a selection sends to the chosen provider, in words: built from the server's disclosure list,
// which follows the quality settings.
export function privacyNote(p) {
  if (!p || p.provider === "stub") return "Offline pattern parser: nothing leaves this machine.";
  const where = p.local ? `${p.label} runs on this machine, so nothing leaves it.` : `${p.label} is a remote service.`;
  return `${where} Each selection sends: ${p.sends.join("; ")}. Never your network tables or files.`;
}

export async function refreshLLM() {
  const seq = ++refreshSeq;
  const [nextLLM, nextSettings] = await Promise.all([getJSON("/api/llm/providers"), getJSON("/api/settings")]);
  if (seq !== refreshSeq) return; // a newer refresh started meanwhile
  llm = nextLLM;
  settings = nextSettings;
  const src = settings.sources;
  if (src["select.provider"] !== "session" && src["select.model"] !== "session") {
    savedDefault = { ...settings.values.select };
    baseSources = { provider: src["select.provider"], model: src["select.model"] };
  }
  await renderAll();
}

async function renderAll() {
  await renderPicker();
  if (panelOpen()) await renderLLMPanel();
}

// Status-only snapshot (SSE `llm` event, or a key route's reply). It supersedes any refresh in flight.
export function onLLMEvent(ev) {
  const { type, ...snapshot } = ev;
  refreshSeq++;
  llm = snapshot;
  renderAll().catch(report);
}

// `job` events (shared SSE channel with jobs.js/wizard.js): clear a model's "pulling" state only
// when that model's OWN pull job ends. Wiping the whole set on every `llm` snapshot would also
// re-enable the Pull button for a second model whose pull is still running.
export function onLLMJob(job) {
  if (job.kind !== "ollama_pull" || job.status === "running") return;
  const model = PULL_JOB_LABEL.exec(job.label)?.[1];
  if (model) pulling.delete(model);
  if (model && panelOpen()) renderLLMPanel().catch(report);
}

// A project file or NETSTEAD_SELECT__* env var outranks the user file, so a saved default would not take effect.
function shadowingLayers() {
  if (!baseSources) return [];
  const layers = new Set(Object.values(baseSources).filter(s => s === "project" || s === "env"));
  return [...layers].map(s => (s === "project" ? "this project's config" : "a NETSTEAD_SELECT__* env var"));
}

// A set_setting from Python or another tab may change the provider, model, endpoints or quality.
export function onHistoryEntry(entry) {
  const a = entry.action;
  if (entry.ok && a.type === "set_setting" && /^(select|llm)(\.|$)/.test(a.key)) refreshLLM().catch(report);
}

// ------------------------------------------------------------------ header picker

function canMakeDefault() {
  if (!llm || !settings) return false;
  const src = settings.sources;
  if (src["select.provider"] !== "session" && src["select.model"] !== "session") return false;
  if (!savedDefault) return true; // overridden before this page loaded: the saved pair is unknown here
  const { provider, model } = llm.selected;
  return (
    provider !== savedDefault.provider ||
    effectiveModel(provider, model) !== effectiveModel(savedDefault.provider, savedDefault.model)
  );
}

async function renderPicker() {
  if (!llm) return;
  const { provider, model } = llm.selected;
  const options = [STUB, ...llm.providers.filter(p => p.usable)];
  const current = options.find(p => p.provider === provider);
  const html = options.map(
    p => `<option value="${esc(p.provider)}"${p.provider === provider ? " selected" : ""}>${esc(p.label)}</option>`,
  );
  if (!current) {
    const row = providerRow(provider);
    html.push(`<option value="${esc(provider)}" selected disabled>${esc(row ? row.label : provider)} (not set up)</option>`);
  }
  $("nl-provider").innerHTML = html.join("");
  $("nl-wrap").title = current ? privacyNote(current) : `${provider} is not set up: open Models… to add a key or start it.`;
  $("nl-default").disabled = !canMakeDefault();
  const shadow = shadowingLayers();
  $("nl-default").title = shadow.length
    ? `Save this provider and model to your user config (${shadow.join(" and ")} overrides it)`
    : "Save this provider and model as your default";
  if (panelOpen()) $("llm-privacy").textContent = privacyNote(providerRow(provider));
  await renderModels(provider, model);
  syncButtonTip(current, provider);
}

// The header robot shows whether the chosen provider and model can answer: its color (green ready, yellow
// offline pattern parser only, red not available), a dot whose shape says the same, and a tooltip in words that
// also carries the privacy note. The text is the button's accessible name too.
function syncButtonTip(current, provider) {
  const row = provider === "stub" ? null : current || providerRow(provider);
  const status = llmStatus({ provider, row, model: $("nl-model").value, modelMissing, privacy: row ? privacyNote(row) : "Nothing leaves this machine." });
  const btn = $("nl-btn");
  btn.dataset.tip = status.text;
  btn.setAttribute("aria-label", status.text);
  btn.className = `iconbtn llm-${status.state}`;
  $("nl-dot").className = `dot llm-${status.state}`;
}

function pickerIsOpen() { return !$("nl-picker").hidden; }

function setPicker(open, { refocus = false } = {}) {
  $("nl-picker").hidden = !open;
  $("nl-btn").setAttribute("aria-expanded", String(open));
  if (open) $("nl-provider").focus();
  else if (refocus) $("nl-btn").focus();
}

async function fetchModels(provider) {
  return (await getJSON(`/api/llm/models?provider=${encodeURIComponent(provider)}`)).models;
}

async function renderModels(provider, model) {
  const seq = ++modelsSeq;
  const box = $("nl-model");
  const p = providerRow(provider);
  modelMissing = false;
  box.hidden = provider === "stub";
  if (provider === "stub" || !p || !p.usable) {
    box.innerHTML = "";
    box.disabled = true;
    return;
  }
  let models = [], listed = false;
  try {
    models = await fetchModels(provider);
    listed = true;
  } catch (e) {
    report(e);
  }
  if (seq !== modelsSeq) return;
  const chosen = model || defaultModel(p);
  const label = m => [m.label, m.tier, m.tools === false ? "JSON mode" : null].filter(Boolean).join(" · ");
  const opts = models.map(m => `<option value="${esc(m.id)}"${m.id === chosen ? " selected" : ""}>${esc(label(m))}</option>`);
  if (chosen && !models.some(m => m.id === chosen)) {
    modelMissing = listed && p.local;
    opts.unshift(`<option value="${esc(chosen)}" selected>${esc(chosen)}${p.local ? " (not installed)" : ""}</option>`);
  }
  box.innerHTML = opts.join("");
  box.disabled = !opts.length;
  syncModelTitle(!model && chosen !== p.default_model ? `Using ${chosen}: the default, ${p.default_model}, is not installed` : "");
}

// The model box is narrow, so its tooltip (and data-full, for tests) carries the chosen option's full label, with
// `note` after it when there is one.
function syncModelTitle(note = "") {
  const box = $("nl-model"), chosen = box.selectedOptions[0];
  const full = chosen ? chosen.textContent : "";
  box.dataset.full = full;
  box.title = [full, note].filter(Boolean).join(" — ");
  if (llm) syncButtonTip(providerRow(llm.selected.provider), llm.selected.provider);
}

// The picker changes this session only; "Make default" saves the current choice to the user file.
async function setSetting(key, value, scope) {
  const result = await dispatch({ type: "set_setting", key, value, scope });
  if (settings) settings.sources = { ...settings.sources, [key]: result.source };
}

// A new provider starts on its default; for Ollama the server already picked an installed stand-in if needed.
function startingModel(provider) {
  const p = providerRow(provider);
  return p ? defaultModel(p) : null;
}

// Model first, then provider; if the provider write fails the model goes back, so the pair never mismatches.
// (The stub has no model: its switch is a single write.)
async function onProviderChange(provider) {
  const before = llm.selected;
  const sessionModel = settings && settings.sources["select.model"] === "session";
  let modelWritten = false;
  try {
    if (provider !== "stub") {
      const model = startingModel(provider);
      await setSetting("select.model", model, "session"); // explicit, so history replays the same model
      modelWritten = true;
      await setSetting("select.provider", provider, "session");
      llm.selected = { provider, model };
    } else {
      await setSetting("select.provider", provider, "session");
      llm.selected = { ...before, provider };
    }
  } catch (e) {
    report(e);
    if (modelWritten) {
      // null drops the session override, restoring whatever the lower layers say
      await setSetting("select.model", sessionModel ? before.model : null, "session").catch(report);
    }
  }
  await renderPicker();
}

async function onModelChange(model) {
  try {
    await setSetting("select.model", model, "session");
    llm.selected = { ...llm.selected, model };
  } catch (e) {
    report(e);
  }
  await renderPicker();
}

// One write of the whole [select] table, so the saved pair is atomic. The model is saved as chosen:
// null (never chosen, or the stub) leaves it out, so the catalog default keeps applying.
async function makeDefault() {
  const { provider, model } = llm.selected;
  const pair = { provider, model: provider === "stub" ? null : model ?? null };
  try {
    await dispatch({ type: "set_setting", key: "select", value: pair, scope: "user" });
    savedDefault = pair;
    const label = (providerRow(provider) || { label: provider }).label;
    const what = `${label}${pair.model ? ` · ${pair.model}` : ""}`;
    const shadow = shadowingLayers();
    toast(
      shadow.length
        ? `Saved ${what} to your user config, but ${shadow.join(" and ")} overrides it.`
        : `Saved ${what} as your default.`,
    );
    await refreshLLM();
  } catch (e) {
    report(e);
  }
}

// ------------------------------------------------------------------ "Language models" panel

function storageNote() {
  if (!llm.key_writes) {
    return "Key changes are disabled because the Workbench is exposed to the network; use `netstead llm set-key` in a terminal.";
  }
  return llm.keyring
    ? "Keys are stored in your OS keychain (keyring), or read from environment variables. They are never shown again."
    : "This machine has no OS keychain, so keys can't be stored here: set each provider's environment variable " +
        "(shown below) in the shell that starts `netstead app`, then restart it.";
}

function statusText(p) {
  if (p.kind === "local") return p.usable ? `running · ${p.models} model(s)` : p.error || "not reachable";
  if (p.error) return p.error;
  if (p.configured) return `key set · ${p.source}`;
  return llm.keyring && llm.key_writes ? "no key" : `no key · set ${p.key_env.join(" or ")}`;
}

function actionsHTML(p) {
  if (!llm.key_writes) return "";
  if (p.kind === "local") return '<button class="mini ghost" data-act="test">Test</button>';
  const manage = llm.keyring && p.source !== "env"; // env keys are managed in the shell, not here
  return [
    manage ? `<button class="mini" data-act="set">${p.configured ? "Replace" : "Set key"}</button>` : "",
    manage && p.configured ? '<button class="mini ghost" data-act="remove">Remove</button>' : "",
    p.configured ? '<button class="mini ghost" data-act="test">Test</button>' : "",
  ].join("");
}

function rowHTML(p) {
  // A remote-kind provider pointed at a loopback base_url runs here too: say so (Ollama's label already does).
  const tag = p.local && p.kind !== "local" ? ' <span class="tag">local</span>' : "";
  const help = p.kind === "local" ? ollamaHelpHTML(p) : "";
  return (
    `<tr data-provider="${esc(p.provider)}" title="${esc(privacyNote(p))}"><td><span class="dot ${dotClass(p)}"></span>${esc(p.label)}${tag}</td>` +
    `<td class="st">${esc(statusText(p))}</td><td class="acts">${actionsHTML(p)}</td></tr>` +
    '<tr class="keyrow" hidden><td colspan="3"></td></tr>' +
    (help ? `<tr class="helprow"><td colspan="3"><div class="ollama-help">${help}</div></td></tr>` : "")
  );
}

// ------------------------------------------------------------------ Ollama: setup help and Pull

const gb = size => (size ? `about ${size} GB` : "several GB");

function pullChoices(p) {
  const choices = (llm.ollama_pull && llm.ollama_pull.choices) || [];
  return choices.length ? choices : [{ id: p.default_model, label: p.default_model, size_gb: null }];
}

// The Pull control: a catalog model select (default first) and a button naming the chosen model.
function pullHTML(p, hidden) {
  if (!llm.ollama_pull || !llm.ollama_pull.allowed) {
    return `<p>Pull a model from a terminal: <code>netstead llm pull ${esc(p.default_model)}</code> (or <code>ollama pull ${esc(p.default_model)}</code>).</p>`;
  }
  const choices = pullChoices(p);
  const first = choices.find(c => c.id === p.default_model) || choices[0];
  const options = choices.map(
    c => `<option value="${esc(c.id)}"${c === first ? " selected" : ""}>${esc(c.id)} · ${esc(gb(c.size_gb))}</option>`,
  );
  const busy = pulling.has(first.id) ? " disabled" : "";
  return (
    `<div class="row pullrow"${hidden ? " hidden" : ""}><select class="pull-model" aria-label="Model to pull">${options.join("")}</select>` +
    `<button class="mini" data-act="pull"${busy}>Pull ${esc(first.id)}</button></div>`
  );
}

function ollamaHelpHTML(p) {
  if (!p.configured) {
    // Not answering: not installed, or not started.
    return (
      `<p>Ollama isn't answering at <code>${esc(p.base_url)}</code>. To run models on this machine:</p><ul>` +
      `<li><b>macOS / Windows:</b> install the app from <a href="${OLLAMA_DOWNLOAD}" target="_blank" rel="noopener">ollama.com/download</a>, then open it (it runs the server in the background).</li>` +
      "<li><b>Linux:</b> <code>curl -fsSL https://ollama.com/install.sh | sh</code> (read the script first), " +
      "then <code>ollama serve</code> if it isn't already running as a service.</li></ul>" +
      '<div class="row"><button class="mini" data-act="recheck">Check again</button>' +
      `<a href="${OLLAMA_GUIDE}" target="_blank" rel="noopener">Setup guide</a></div>`
    );
  }
  if (!p.local) return "<p>This Ollama runs on another machine: pull models there with <code>ollama pull</code>.</p>";
  if (!p.models) return `<p>Ollama is running but has no models yet. Pull one to use it here (one-time download).</p>${pullHTML(p, false)}`;
  const another = llm.ollama_pull && llm.ollama_pull.allowed
    ? '<button class="mini ghost" data-act="pull-show">Pull another model</button>' : "";
  return `${another}${pullHTML(p, true)}`;
}

// "Check again" re-probes now (the status list may reuse a probe from a few seconds ago).
async function recheckOllama() {
  try {
    if (llm.key_writes) await sendJSON("POST", "/api/llm/test", { provider: "ollama", model: null }, SECRETS);
    await refreshLLM();
  } catch (e) {
    report(e);
  }
}

async function pullModel(help) {
  const model = help.querySelector(".pull-model").value;
  const choice = pullChoices(providerRow("ollama")).find(c => c.id === model);
  const size = gb(choice && choice.size_gb);
  if (!confirm(`Download ${model} (${size}) into Ollama on this machine? Progress shows under Jobs.`)) return;
  const button = help.querySelector('button[data-act="pull"]');
  button.disabled = true;
  pulling.add(model);
  try {
    await sendJSON("POST", "/api/llm/ollama/pull", { model }, SECRETS);
    openJobsPanel();
  } catch (e) {
    pulling.delete(model);
    button.disabled = false;
    report(e);
  }
}

function onHelpClick(button) {
  const help = button.closest(".ollama-help");
  const act = button.dataset.act;
  if (act === "recheck") return recheckOllama();
  if (act === "pull") return pullModel(help);
  if (act === "pull-show") {
    help.querySelector(".pullrow").hidden = false;
    button.hidden = true;
  }
  return null;
}

function qualityInput(id, key, kind, opts, v) {
  if (kind === "bool") return `<input type="checkbox" id="${id}" data-q="${esc(key)}" data-kind="bool"${v ? " checked" : ""}>`;
  if (kind === "choice") {
    const options = opts.map(([value, text]) => `<option value="${esc(value)}"${value === v ? " selected" : ""}>${esc(text)}</option>`);
    return `<select id="${id}" data-q="${esc(key)}" data-kind="choice">${options.join("")}</select>`;
  }
  const step = kind === "float" ? "0.1" : "1";
  return (
    `<input type="number" id="${id}" data-q="${esc(key)}" data-kind="${kind}" min="${opts[0]}" max="${opts[1]}" ` +
    `step="${step}" value="${esc(v ?? "")}" placeholder="default">`
  );
}

function qualityHTML(values, sources) {
  return QUALITY.map(([key, label, kind, opts]) => {
    const id = `q-${key}`;
    const src = sources[`llm.quality.${key}`];
    const from = src && src !== "default" && src !== "user" ? ` <span class="muted">(${esc(src)})</span>` : "";
    return `<div class="row"><label class="lbl" for="${id}">${esc(label)}${from}</label>${qualityInput(id, key, kind, opts, values[key])}</div>`;
  }).join("");
}

// While a key form is open, only patch status cells: a rebuild would drop the field mid-typing.
function renderProviders() {
  const table = $("llm-providers");
  const formOpen = [...table.querySelectorAll("tr.keyrow")].some(r => !r.hidden);
  const rows = new Map([...table.querySelectorAll("tr[data-provider]")].map(tr => [tr.dataset.provider, tr]));
  if (!formOpen || llm.providers.some(p => !rows.has(p.provider))) {
    table.innerHTML = llm.providers.map(rowHTML).join("");
    return;
  }
  for (const p of llm.providers) {
    const tr = rows.get(p.provider);
    tr.title = privacyNote(p);
    tr.querySelector(".dot").className = `dot ${dotClass(p)}`;
    const cell = tr.querySelector(".st");
    cell.textContent = statusText(p);
    cell.className = "st";
  }
}

export async function renderLLMPanel() {
  if (!llm || !settings) return;
  $("llm-privacy").textContent = privacyNote(providerRow(llm.selected.provider));
  $("llm-storage").textContent = storageNote();
  renderProviders();
  const ollama = providerRow("ollama");
  if (ollama && document.activeElement !== $("llm-ollama-url")) $("llm-ollama-url").value = ollama.base_url || "";
  if (!$("llm-quality").contains(document.activeElement)) {
    $("llm-quality").innerHTML = qualityHTML(settings.values.llm.quality, settings.sources);
  }
  const select = $("llm-catalog-provider");
  const keep = select.value;
  select.innerHTML = llm.providers
    .map(p => `<option value="${esc(p.provider)}"${p.provider === keep ? " selected" : ""}>${esc(p.label)}</option>`)
    .join("");
  $("llm-catalog-hint").textContent =
    `Add or relabel models in ${settings.paths.user.replace(/config\.toml$/, "llm_models.toml")} (same shape as models.toml).`;
  await renderCatalog(select.value);
}

async function renderCatalog(provider) {
  const table = $("llm-catalog");
  if (!provider) return;
  try {
    const models = await fetchModels(provider);
    const tools = m => (m.tools == null ? "?" : m.tools ? "yes" : "no");
    table.innerHTML =
      "<tr><th>Model id</th><th>Label</th><th>Tier</th><th>Tools</th></tr>" +
      models
        .map(m => `<tr><td><code>${esc(m.id)}</code></td><td>${esc(m.label)}</td><td>${esc(m.tier || "—")}</td><td>${tools(m)}</td></tr>`)
        .join("");
  } catch (e) {
    table.innerHTML = `<tr><td class="st fail">${esc(e.message)}</td></tr>`;
  }
}

// Blank number fields send null, which removes the user setting (back to the default).
function qualityValue(el) {
  const kind = el.dataset.kind;
  if (kind === "bool") return el.checked;
  if (kind === "choice") return el.value;
  if (el.value === "") return null;
  return kind === "int" ? Number.parseInt(el.value, 10) : Number.parseFloat(el.value);
}

async function onQualityChange(el) {
  try {
    await setSetting(`llm.quality.${el.dataset.q}`, qualityValue(el), "user");
    el.blur();
    await refreshLLM(); // the privacy note follows these settings
  } catch (e) {
    report(e);
    await refreshLLM().catch(report); // put the control back to the real value
  }
}

function openKeyForm(tr) {
  const provider = tr.dataset.provider;
  const row = tr.nextElementSibling;
  const cell = row.firstElementChild;
  cell.innerHTML =
    // Password-manager opt-outs: an API key is not a site login and must not be saved or autofilled.
    '<div class="row"><input type="password" class="kval grow" autocomplete="new-password" spellcheck="false" ' +
    'data-1p-ignore data-lpignore="true" data-bwignore ' +
    `placeholder="Paste API key" aria-label="API key for ${esc(provider)}">` +
    '<button class="mini ksave">Save</button><button class="mini ghost kcancel">Cancel</button></div>';
  row.hidden = false;
  const input = cell.querySelector(".kval");
  const close = () => {
    input.value = "";
    cell.innerHTML = "";
    row.hidden = true;
  };
  const save = async () => {
    const key = input.value.trim();
    close(); // clear and drop the field before the request is even sent
    if (!key) return;
    try {
      onLLMEvent(await sendJSON("PUT", `/api/llm/keys/${encodeURIComponent(provider)}`, { key }, SECRETS));
    } catch (e) {
      report(e);
    }
  };
  cell.querySelector(".ksave").onclick = save;
  cell.querySelector(".kcancel").onclick = close;
  input.onkeydown = e => {
    if (e.key === "Enter") save();
    else if (e.key === "Escape") { e.stopPropagation(); close(); } // close the key form, not the Settings dialog
  };
  input.focus();
}

async function removeKey(tr) {
  const p = providerRow(tr.dataset.provider);
  if (!confirm(`Remove the ${p.label} key from the keyring?`)) return;
  try {
    onLLMEvent(await sendJSON("DELETE", `/api/llm/keys/${encodeURIComponent(p.provider)}`, undefined, SECRETS));
  } catch (e) {
    report(e);
  }
}

async function testProvider(tr) {
  const provider = tr.dataset.provider;
  const cell = tr.querySelector(".st");
  cell.textContent = "testing…";
  cell.className = "st";
  const model = llm.selected.provider === provider ? llm.selected.model : null;
  try {
    const r = await sendJSON("POST", "/api/llm/test", { provider, model }, SECRETS);
    const missing = r.catalog_missing && r.catalog_missing.length ? ` Not served here: ${r.catalog_missing.join(", ")}.` : "";
    cell.textContent = r.message + missing;
    cell.className = `st ${r.ok ? "ok" : "fail"}`;
    if (!r.ok) tr.querySelector(".dot").className = "dot warn";
  } catch (e) {
    cell.textContent = e.message;
    cell.className = "st fail";
  }
}

// The header wraps to more rows on narrower windows; floating panels read --header-h to sit below it.
function trackHeaderHeight() {
  const header = document.querySelector("header");
  const set = () => document.documentElement.style.setProperty("--header-h", `${Math.ceil(header.getBoundingClientRect().height)}px`);
  set();
  if (typeof ResizeObserver === "function") new ResizeObserver(set).observe(header);
  else window.addEventListener("resize", set);
}

export function wireLLM() {
  trackHeaderHeight();
  $("nl-btn").onclick = () => setPicker(!pickerIsOpen());
  $("nl-wrap").onkeydown = e => { if (e.key === "Escape" && pickerIsOpen()) { e.preventDefault(); setPicker(false, { refocus: true }); } };
  document.addEventListener("pointerdown", e => { if (pickerIsOpen() && !e.target.closest("#nl-wrap")) setPicker(false); });
  $("nl-provider").onchange = e => onProviderChange(e.target.value);
  $("nl-model").onchange = e => { syncModelTitle(); onModelChange(e.target.value); };
  $("nl-default").onclick = () => makeDefault();
  $("nl-manage").onclick = () => { setPicker(false); openSettings("llm").catch(report); };
  $("llm-providers").onclick = e => {
    const button = e.target.closest("button[data-act]");
    if (!button) return;
    if (button.closest(".ollama-help")) {
      onHelpClick(button);
      return;
    }
    const tr = button.closest("tr");
    ({ set: openKeyForm, remove: removeKey, test: testProvider })[button.dataset.act](tr);
  };
  $("llm-providers").onchange = e => {
    if (!e.target.classList.contains("pull-model")) return;
    const button = e.target.parentElement.querySelector('button[data-act="pull"]');
    button.textContent = `Pull ${e.target.value}`;
    button.disabled = pulling.has(e.target.value);
  };
  $("llm-quality").onchange = e => {
    if (e.target.dataset.q) onQualityChange(e.target);
  };
  $("llm-ollama-save").onclick = async () => {
    const value = $("llm-ollama-url").value.trim() || null; // empty = back to the default
    try {
      await setSetting("llm.ollama.base_url", value, "user");
      $("llm-ollama-url").blur();
      await refreshLLM();
    } catch (e) {
      report(e);
    }
  };
  $("llm-catalog-provider").onchange = e => renderCatalog(e.target.value);
}
