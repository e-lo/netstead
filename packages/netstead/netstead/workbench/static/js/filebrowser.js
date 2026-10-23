// Server-side file browser over GET /api/fs/list (only io.allowed_roots). Files are opened in place, never uploaded.
import { getJSON } from "./api.js";
import { esc, toast } from "./dom.js";

const KIND_LABEL = { gmns: "GMNS", zip: "zip", duckdb: "DuckDB", datapackage: "datapackage", osm: "OSM XML",
  json: "JSON", overture: "Overture" };

// el: container element. opts.kinds: entry kinds that can be picked. opts.pickFolder: offer "Use this folder" and a "Use" button on each folder row.
// opts.onPick(entry): called with the picked entry ({path, target, kind, is_dir}).
export function createFileBrowser(el, opts) {
  let listing = null, picked = null, seq = 0;

  function render() {
    const up = listing.path ? '<button class="mini ghost" data-up>&#8593; Up</button>' : "";
    const here = listing.path ? `<code class="fb-path">${esc(listing.path)}</code>` : '<span class="fb-path">Allowed folders</span>';
    const use = opts.pickFolder && listing.path ? '<button class="mini" data-here>Use this folder</button>' : "";
    const rows = listing.entries.map((e, i) => {
      const ok = opts.kinds.includes(e.kind), on = picked && picked.path === e.path;
      return `<div class="fb-row${ok ? " ok" : ""}${on ? " on" : ""}" data-i="${i}">` +
        `<span class="fb-ico">${e.is_dir ? "&#128193;" : "&#128196;"}</span><span class="fb-name">${esc(e.name)}</span>` +
        (opts.pickFolder && e.is_dir ? '<button class="mini fb-use" data-use>Use</button>' : "") +
        (e.kind ? `<span class="fb-kind">${esc(KIND_LABEL[e.kind] || e.kind)}</span>` : "") + "</div>";
    });
    const more = listing.truncated ? '<div class="empty">Showing the first 2000 entries.</div>' : "";
    el.innerHTML = `<div class="fb-bar">${up}${here}${use}</div><div class="fb-list">${rows.join("") || '<span class="empty">Empty folder.</span>'}${more}</div>`;
  }

  async function show(path) {
    const mine = ++seq; // a slow listing must not replace the folder the user has since moved to
    try {
      const next = await getJSON(`/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`);
      if (mine !== seq) return;
      listing = next;
      render();
    } catch (e) { if (mine === seq) toast(e.message); }
  }

  function pick(entry) { picked = entry; render(); opts.onPick(entry); }

  el.addEventListener("click", ev => {
    const use = ev.target.closest("[data-use]");
    if (use && listing) { // "Use" beside a folder row picks that folder without opening it
      const row = listing.entries[Number(use.closest("[data-i]").dataset.i)];
      pick({ path: row.path, target: row.path, kind: null, is_dir: true });
      return;
    }
    const t = ev.target.closest("[data-up],[data-here],[data-i]");
    if (!t || !listing) return;
    if (t.hasAttribute("data-up")) { show(listing.parent); return; }
    if (t.hasAttribute("data-here")) { pick({ path: listing.path, target: listing.path, kind: null, is_dir: true }); return; }
    const e = listing.entries[Number(t.dataset.i)];
    if (opts.kinds.includes(e.kind)) pick(e);
    else if (e.is_dir) show(e.path);
  });
  el.addEventListener("dblclick", ev => {
    const t = ev.target.closest("[data-i]");
    const e = t && listing && listing.entries[Number(t.dataset.i)];
    if (e && e.is_dir) show(e.path);
  });

  return {
    show,
    reset(path = null) { picked = null; return show(path); },
    configure(changes) { Object.assign(opts, changes); }, // e.g. {kinds} when the wizard's source changes
  };
}
