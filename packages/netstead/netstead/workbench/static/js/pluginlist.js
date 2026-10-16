// Settings → Plugins, as rows: what started (GET /api/plugins), what the browser could not load, and what
// app.disabled_plugins says now. DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).
import { esc } from "./schemaform.js";

const STATE_TEXT = { loaded: "Loaded", disabled: "Disabled", incompatible: "Incompatible", error: "Failed to load" };

function compatText(requiresApi, compatible, hostApi) {
  if (compatible === null) return "—";
  return compatible ? `${requiresApi} (compatible)` : `${requiresApi} (this netstead provides ${hostApi})`;
}

// One row per installed plugin, plus one per id in app.disabled_plugins that isn't installed (so it can be cleared).
// `enabled` is the saved setting; `restart` is true when it differs from how this run started.
// `locked`: a higher layer sets the list (see pluginsLock), so no switch can change it.
export function pluginRows({ statuses, hostApi, disabled, browserErrors = {}, locked = false }) {
  const rows = statuses.map(s => {
    const compatible = s.requires_api == null ? null : s.state !== "incompatible";
    const enabled = !disabled.includes(s.id);
    return {
      id: s.id, name: s.name || s.id, version: s.version || "—", compat: compatText(s.requires_api, compatible, hostApi),
      state: s.state, stateText: STATE_TEXT[s.state] || s.state,
      errors: [s.error, ...(browserErrors[s.id] || []).map(e => `${e.phase}: ${e.message}`)].filter(Boolean),
      enabled, restart: enabled !== (s.state !== "disabled"), installed: true, locked,
    };
  });
  for (const id of disabled) {
    if (statuses.some(s => s.id === id)) continue;
    rows.push({ id, name: id, version: "—", compat: "—", state: "missing", stateText: "Not installed", errors: [],
      enabled: false, restart: false, installed: false, locked });
  }
  return rows;
}

// app.disabled_plugins after switching `id` on or off (order kept, no duplicates).
export function withPluginEnabled(disabled, id, enabled) {
  const rest = disabled.filter(x => x !== id);
  return enabled ? rest : [...rest, id];
}

// The switches save app.disabled_plugins at user scope. While a project file, a NETSTEAD_* variable or the session
// sets it, that outranks the user value, so saving would change nothing (and could copy the higher layer's ids into
// the user file): the switches are disabled and this note (null when they work) says where to change it.
export function pluginsLock(source, paths = {}) {
  const by = {
    project: `the project file ${paths.project} (app.disabled_plugins)`,
    env: "the NETSTEAD_APP__DISABLED_PLUGINS environment variable",
  }[source];
  if (by) return `Which plugins load is set by ${by}: change it there. The switches are off while it is set.`;
  if (source === "session") {
    return "Which plugins load is set for this session only, which outranks the saved value; it ends when " +
      "netstead app stops. The switches are off while it is set.";
  }
  return null;
}

// One table row. Plugin strings are escaped; a locked switch is disabled and points at the note.
export function pluginRowHTML(r) {
  return `<tr><td><b>${esc(r.name)}</b><div class="muted">${esc(r.id)}</div></td><td>${esc(r.version)}</td>` +
    `<td>${esc(r.compat)}</td><td>${esc(r.stateText)}${r.restart ? ' <span class="tag">restart to apply</span>' : ""}` +
    r.errors.map(e => `<div class="err">${esc(e)}</div>`).join("") + "</td>" +
    `<td><label class="sw"><input type="checkbox" data-plugin="${esc(r.id)}"${r.enabled ? " checked" : ""}` +
    `${r.locked ? ' disabled aria-describedby="plugins-note"' : ""} ` +
    `aria-label="Load ${esc(r.name)} at startup"><span></span></label></td></tr>`;
}
