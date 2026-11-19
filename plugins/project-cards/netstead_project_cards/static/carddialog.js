// "Make a card" (design 2e): a modal the Changes pane's "Make a card…" opens with the ticked changes (Host API 1.11's
// wb.registerCardMaker). 1. Changes: the ticked ones, each with its facility form and id system (untick to leave one
// out). 2. About the card: project, year (the tag year:2030), tags, dependencies, notes, file name. 3. Check: the live
// edit warnings, the card's schema problems and its YAML (POST /from-changes, read again as the form changes).
// 4. Where: the open catalog, another folder (new or existing), download the .yml, or copy the YAML. The card is made
// by the project_cards.card_from_changes Action; its changes then read "In a card" in the Changes pane. The rules
// live in cardmodel.js (tested under node); this module only draws them. Enter or Cmd/Ctrl+Enter makes the card,
// Escape closes.
import {
  CARD_DESTINATIONS, FACILITY_FORMS, ID_SYSTEMS, aboutFields, cardDestination, cardFromChangesAction, changesCheck,
  deliveredLine, destinationReason, optionReason,
} from "./cardmodel.js";
import { h } from "./dom.js";

const FIELDS = [
  ["project", "Project name", "Main Street road diet"],
  ["year", "Year", "2030"],
  ["tags", "Tags", "comma separated"],
  ["prerequisites", "Prerequisites", "projects, comma separated"],
  ["corequisites", "Corequisites", "projects, comma separated"],
  ["conflicts", "Conflicts", "projects, comma separated"],
];
const TIPS = {
  changes: "Each ticked change goes in the card. A change made in the Workbench names its rows by id, so it is a "
    + "Link list; write its ids in GMNS or OpenStreetMap ids.",
  about: "Year is written as the tag year:2030. Dependencies name other projects.",
  check: "The card is checked against the ProjectCard schema. Live edit warnings need your confirmation.",
  where: "A folder becomes the open catalog. A bundled sample catalog is read-only.",
};

let dialog = null;

// An icon button with a tooltip (core's `iconbtn`, the core tooltip layer); its text where the host draws no icons.
function iconButton(wb, name, tip, label = tip) {
  const b = h("button", { type: "button", class: "iconbtn", "data-tip": tip, "aria-label": label });
  let svg = "";
  try { svg = typeof wb.icon === "function" ? wb.icon(name) : ""; } catch (e) { svg = ""; }
  if (svg) b.innerHTML = svg; else b.textContent = tip;
  return b;
}

const infoTip = (wb, tip, label) => {
  const b = iconButton(wb, "info", tip, label);
  b.tabIndex = 0;
  return b;
};

const heading = (wb, text, tip) => h("div", { class: "label", style: "display:flex;gap:4px;align-items:center;margin:12px 0 4px" },
  h("span", { text }), infoTip(wb, tip, `About ${text}`));

// Open the dialog for `picked` ({net_id, seqs}).
export async function openCardDialog(wb, picked) {
  if (!dialog) {
    dialog = h("dialog", { "aria-labelledby": "pc-cd-title", id: "pc-card-dialog" });
    dialog.style.cssText = "background:var(--panel);color:var(--ink);border:1px solid var(--edge);border-radius:12px;"
      + "padding:16px;width:min(760px, calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto";
    document.body.append(dialog);
  }
  const seqs = [...picked.seqs];
  let view;
  try {
    view = await wb.api.post("/from-changes", { net_id: picked.net_id, seqs });
  } catch (e) {
    wb.toast(e.message);
    return;
  }
  draw(wb, dialog, picked.net_id, view);
  if (!dialog.open) dialog.showModal();
  const first = dialog.querySelector("#pc-cd-project");
  if (first) first.focus();
}

function draw(wb, box, netId, view) {
  const ticked = new Set(view.rows.filter(r => !r.problem).map(r => r.seq));
  const choices = {};
  const note = h("p", { role: "status", id: "pc-cd-note" });

  // 1. Changes
  const rowNodes = view.rows.map(row => {
    const pick = h("input", { type: "checkbox", "aria-label": `Put #${row.seq} in the card`,
      checked: ticked.has(row.seq), disabled: Boolean(row.problem) });
    pick.onchange = () => { if (pick.checked) ticked.add(row.seq); else ticked.delete(row.seq); refresh(); };
    const sub = [];
    if (row.options) {
      choices[row.seq] = { form: row.options.form, id_system: row.options.id_system };
      const select = (kind, list, key, label) => {
        const el = h("select", { "aria-label": `${label} of #${row.seq}` });
        for (const o of list) {
          const why = optionReason(row.options, kind, o.key);
          el.append(h("option", { value: o.key, text: why ? `${o.label} (unavailable)` : o.label, disabled: Boolean(why),
            ...(why ? { title: why } : {}) }));
        }
        el.value = choices[row.seq][key];
        el.onchange = () => { choices[row.seq][key] = el.value; refresh(); };
        return el;
      };
      const reasons = [...FACILITY_FORMS.map(f => optionReason(row.options, "forms", f.key)),
        ...ID_SYSTEMS.map(s => optionReason(row.options, "id_systems", s.key))].filter(Boolean);
      sub.push(select("forms", FACILITY_FORMS, "form", "Facility form"),
        select("id_systems", ID_SYSTEMS, "id_system", "ID system"));
      if (reasons.length) sub.push(infoTip(wb, reasons.join(". "), `Why some choices are unavailable for #${row.seq}`));
    }
    const what = row.problem || row.changes.join("; ");
    return h("li", { "data-seq": String(row.seq), style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:2px 0" },
      pick, h("span", { style: "flex:1;min-width:200px" }, h("span", { text: `#${row.seq} ${row.note}` }),
        h("span", { class: "diag", style: "display:block", text: what })), ...sub);
  });

  // 2. About the card
  const inputs = {};
  const about = FIELDS.map(([key, label, placeholder]) => {
    inputs[key] = h("input", { type: "text", id: `pc-cd-${key}`, placeholder, ...(key === "year" ? { inputmode: "numeric" } : {}) });
    return h("div", { class: "sf-field" }, h("label", { for: `pc-cd-${key}`, text: label }), inputs[key]);
  });
  inputs.notes = h("textarea", { id: "pc-cd-notes", rows: 2 });
  inputs.file = h("input", { type: "text", id: "pc-cd-file", placeholder: view.file });
  about.push(h("div", { class: "sf-field" }, h("label", { for: "pc-cd-notes", text: "Notes" }), inputs.notes),
    h("div", { class: "sf-field" }, h("label", { for: "pc-cd-file", text: "File name" }), inputs.file));

  // 3. Check
  const checkLine = h("p", { id: "pc-cd-check" });
  const warnings = h("ul", { id: "pc-cd-warnings" });
  const confirm = h("input", { type: "checkbox", id: "pc-cd-confirm" });
  const confirmRow = h("p", {}, confirm, " ", h("label", { for: "pc-cd-confirm", text: "Make the card anyway" }));
  const yaml = h("pre", { id: "pc-cd-yaml", style: "max-height:30vh;overflow:auto;background:var(--bg)" });
  const yamlBox = h("details", {}, h("summary", { text: "Card YAML" }), yaml);

  // 4. Where
  const state = wb.store.get();
  let where = destinationReason("catalog", state) ? "folder" : "catalog";
  const folder = h("input", { type: "text", id: "pc-cd-folder", placeholder: "cards/2030" });
  const replace = h("input", { type: "checkbox", id: "pc-cd-replace" });
  const folderRow = h("div", { class: "sf-field" }, h("label", { for: "pc-cd-folder", text: "Folder" }), folder);
  const replaceRow = h("p", {}, replace, " ", h("label", { for: "pc-cd-replace", text: "Replace a file with the same name" }));
  const whereButtons = CARD_DESTINATIONS.map(d => {
    const why = destinationReason(d.key, state);
    const b = iconButton(wb, d.icon, why ? `${d.tip}: ${why}` : d.tip, d.tip);
    b.dataset.where = d.key;
    b.disabled = Boolean(why);
    b.onclick = () => { where = d.key; drawWhere(); };
    return b;
  });
  const drawWhere = () => {
    for (const b of whereButtons) b.setAttribute("aria-pressed", String(b.dataset.where === where));
    folderRow.hidden = where !== "folder";
    replaceRow.hidden = where !== "folder" && where !== "catalog";
  };
  drawWhere();

  const make = iconButton(wb, "check-circle", "Make the card (Enter or Cmd/Ctrl+Enter)", "Make the card");
  make.id = "pc-cd-make";
  const close = iconButton(wb, "x", "Close (Escape)", "Close");
  close.onclick = () => box.close();

  let check = null, seq = 0, timer = null;
  const form = () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value]));
  const sync = () => {
    const v = changesCheck(check, ticked.size);
    checkLine.textContent = !check ? "Checking…" : v.problems ? `Not a valid card yet: ${v.problems}`
      : v.lines.length ? `${v.lines.length} live edit warning(s):` : "Ready: no live edit warnings.";
    warnings.replaceChildren(...v.lines.map(line => h("li", { text: line })));
    confirmRow.hidden = !v.needsConfirm;
    yaml.textContent = check ? check.yaml : "";
    make.disabled = !check || v.blocked || (v.needsConfirm && !confirm.checked);
  };
  async function refresh() {
    const mine = ++seq;
    const { fields } = aboutFields(form(), { draft: true });
    try {
      const got = await wb.api.post("/from-changes", { net_id: netId, seqs: view.rows.map(r => r.seq),
        picked: [...ticked], choices, ...(fields || { project: "" }) });
      if (mine !== seq) return;
      check = got;
      inputs.file.placeholder = got.file;
    } catch (e) {
      if (mine !== seq) return;
      check = { warnings: [], problems: [e.message], yaml: "" };
    }
    sync();
  }
  const later = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
  for (const el of Object.values(inputs)) el.oninput = later;
  confirm.onchange = sync;

  async function makeCard() {
    if (make.disabled) return;
    const { fields, error } = aboutFields(form());
    const target = cardDestination(where, { state: wb.store.get(), folder: folder.value });
    if (error || target.error) { note.textContent = error || target.error; return; }
    if (target.destination.kind === "folder" && replace.checked) target.destination.overwrite = true;
    const action = cardFromChangesAction({ netId, seqs: [...ticked], choices, fields, destination: target.destination,
      confirm: confirm.checked });
    make.disabled = true;
    try {
      // The clipboard takes the checked YAML first, while the click still counts as the user's: a refusal stops
      // here, before the changes are marked In a card.
      const copied = where === "copy" ? check.yaml : null;
      if (copied !== null) await copy(copied);
      const result = await wb.api.dispatch(action);
      if (where === "download") download(result.file, result.yaml);
      if (copied !== null && result.yaml !== copied) await copy(result.yaml);
      box.close();
      wb.toast(deliveredLine(result, where));
    } catch (e) {
      note.textContent = e.message;
      sync();
    }
  }
  make.onclick = makeCard;
  box.onkeydown = e => {
    if (e.key !== "Enter" || e.isComposing || e.shiftKey || e.altKey) return;
    const multiline = e.target && e.target.tagName === "TEXTAREA";
    if (e.metaKey || e.ctrlKey || (!multiline && e.target && e.target.tagName === "INPUT" && e.target.type === "text")) {
      e.preventDefault();
      makeCard();
    }
  };

  box.replaceChildren(
    h("div", { style: "display:flex;gap:8px;align-items:center" },
      h("h3", { id: "pc-cd-title", style: "flex:1;margin:0", text: "Make a card" }), close),
    heading(wb, "1. Changes", TIPS.changes), h("ul", { id: "pc-cd-changes", style: "list-style:none;padding:0;margin:0" }, ...rowNodes),
    heading(wb, "2. About the card", TIPS.about), ...about,
    heading(wb, "3. Check", TIPS.check), checkLine, warnings, confirmRow, yamlBox,
    heading(wb, "4. Where", TIPS.where), h("div", { role: "group", "aria-label": "Where the card goes",
      style: "display:flex;gap:6px;align-items:center" }, ...whereButtons), folderRow, replaceRow,
    note,
    h("div", { style: "display:flex;gap:8px;justify-content:flex-end;margin-top:8px" }, make),
  );
  sync();
  refresh();
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/x-yaml" }));
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    throw new Error(`The clipboard refused the YAML (${e.message}): download the card instead`);
  }
}
