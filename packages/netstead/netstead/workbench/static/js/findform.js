// The Find tab's guided form for the offline pattern parser (findmodel.js): a pattern and its blanks, with street names
// from the open network and cross streets narrowed to the streets that meet the chosen one. While a language model is
// chosen the free-text box shows instead. Both feed the same Select action with one sentence, so history and replay
// are unchanged. Street lists are read-only fetches, kept per network version.
import { getJSON, netPath } from "./api.js";
import { barRoute } from "./assistmodel.js";
import { $ } from "./dom.js";
import { BLANK_LABELS, PATTERNS, crossChoices, missingMessage, patternSentence, useForm } from "./findmodel.js";
import { store } from "./store.js";

const BLANKS = ["street", "from", "to", "type", "ids"];
const ASK_TIP_OFFLINE = "Ask: offline, the assistant understands only a few fixed requests. Choose a language model for open questions.";
const LIMIT = 5000;
let formOn = false, wired = false, pending = null;
let lists = null;          // {key, streets, types} for the network version it was read under
let crossing = null;       // {key, street, streets} for the street the cross list was narrowed to
let listSeq = 0;

const input = blank => $(`find-${blank}`);
const fields = () => Object.fromEntries(BLANKS.map(b => [b, input(b).value]));
const pattern = () => $("find-pattern").value;
const netKey = () => store.get().netKey;

function fillOptions(listId, values) {
  const list = $(listId);
  list.replaceChildren(...values.map(v => Object.assign(document.createElement("option"), { value: v })));
}

// Show only the blanks the chosen pattern has; the rest stay in the DOM so a switch back keeps what was typed.
function showBlanks() {
  const blanks = PATTERNS.find(p => p.id === pattern()).blanks;
  for (const row of $("find-form").querySelectorAll("[data-blank]")) row.hidden = !blanks.includes(row.dataset.blank);
}

// The sentence the form makes; the free-text box mirrors it, so Ask and a switch to a language model see it too.
function refresh() {
  showBlanks();
  const sentence = patternSentence(pattern(), fields());
  $("find-sentence").textContent = sentence ? `Select: “${sentence}”` : "";
  $("find-missing").textContent = sentence ? "" : missingMessage(pattern(), fields());
  if (formOn && sentence) $("utterance").value = sentence;
}

async function loadLists() {
  const key = netKey(), id = store.get().server && store.get().server.active;
  if (!id || !key || (lists && lists.key === key)) return;
  const seq = ++listSeq;
  const read = col => getJSON(netPath(id, `table/link/distinct?col=${col}&limit=${LIMIT}`)).then(j => j.values).catch(() => []);
  const [streets, types] = await Promise.all([read("name"), read("facility_type")]);
  if (seq !== listSeq) return;
  lists = { key, streets, types };
  fillOptions("find-streets", streets);
  fillOptions("find-types", types);
  await narrowCross();
}

// After a street is chosen, offer only the streets that meet it (GET .../streets/crossing); until then, every street.
async function narrowCross() {
  const street = input("street").value.trim(), key = netKey(), id = store.get().server && store.get().server.active;
  const known = lists && lists.streets.find(n => n.toLowerCase() === street.toLowerCase());
  if (!known || !id) { crossing = null; fillOptions("find-cross", crossChoices(lists ? lists.streets : [], null, street)); return; }
  if (!crossing || crossing.key !== key || crossing.street !== known) {
    const seq = ++listSeq;
    const j = await getJSON(netPath(id, `streets/crossing?name=${encodeURIComponent(known)}`)).catch(() => null);
    if (seq !== listSeq || input("street").value.trim().toLowerCase() !== known.toLowerCase()) return;
    crossing = j ? { key, street: known, streets: j.streets } : null;
  }
  fillOptions("find-cross", crossChoices(lists.streets, crossing && crossing.streets, known));
}

// Show the form while the offline parser is chosen, else the free-text box (prefilled with the last sentence).
export function setFindProvider(provider) {
  if (!wired) { pending = provider; return; }
  const on = useForm(provider);
  if (on === formOn) return;
  const sentence = patternSentence(pattern(), fields());
  formOn = on;
  $("find-form").hidden = !on;
  $("utterance").hidden = on;
  if (!on && sentence) $("utterance").value = sentence;
  $("ask").title = on ? ASK_TIP_OFFLINE : "Ask the assistant (Ctrl+Enter, ⌘Enter on a Mac)";
  if (on) { refresh(); loadLists().catch(() => {}); }
}

// What Select sends: the form's sentence (or "" with the missing blanks named and the first one focused), else the
// free-text box.
export function findText() {
  if (!formOn) return $("utterance").value.trim();
  const sentence = patternSentence(pattern(), fields());
  if (sentence) return sentence;
  const first = BLANKS.find(b => !input(b).closest("[data-blank]").hidden && !input(b).value.trim());
  $("find-missing").textContent = missingMessage(pattern(), fields());
  (first ? input(first) : $("find-pattern")).focus();
  return "";
}

export function wireFindForm(onRoute) {
  wired = true;
  $("find-pattern").innerHTML = PATTERNS.map(p => `<option value="${p.id}">${p.label}</option>`).join("");
  for (const b of BLANKS) $(`find-${b}`).closest("label").querySelector("span").textContent = BLANK_LABELS[b];
  $("find-form").addEventListener("input", e => { refresh(); if (e.target === input("street")) narrowCross().catch(() => {}); });
  $("find-form").addEventListener("change", () => refresh());
  $("find-form").addEventListener("keydown", e => {
    const route = e.isComposing ? null : barRoute(e);
    if (route) { e.preventDefault(); onRoute(route); }
  });
  store.subscribe(["netKey"], () => { lists = null; crossing = null; if (formOn) loadLists().catch(() => {}); });
  refresh();
  if (pending != null) setFindProvider(pending);
}
