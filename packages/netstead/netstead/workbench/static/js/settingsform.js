// Settings form model: /api/settings (JSON schema + values + sources + readonly/restart/notes) -> field
// descriptors. Field kinds and input parsing live in schemaform.js (shared with wb.schemaForm). DOM-free: unit-tested
// under node (tests/test_workbench_js.py).
import { fieldKind, parseControl, parseInput, resolveRef } from "./schemaform.js";

export { fieldKind, parseControl, parseInput }; // their old home: callers and tests import them from here too

// Sections another Settings section owns, hidden from the generated form: the Language models panel
// (llm, select) and the plugins' own tables (free-form; each plugin validates its own).
export const FOLDED = new Set(["llm", "select", "plugins"]);

const TITLES = {
  io: "Input & output", engine: "Engine", osm: "OpenStreetMap", overture: "Overture", build: "Build defaults",
  validation: "Validation", viz: "Map", app: "App server", credentials: "Credentials",
};
// Lowest to highest precedence (config.py: defaults < user < project < env < session).
const RANKS = ["default", "user", "project", "env", "session"];
const rank = source => RANKS.indexOf(source);

// A key's source; for a JSON field (e.g. validation.rules), the highest layer among its nested keys.
export function sourceOf(sources, key) {
  if (sources[key]) return sources[key];
  const nested = Object.entries(sources).filter(([k]) => k.startsWith(`${key}.`)).map(([, s]) => s);
  return nested.length ? nested.reduce((a, b) => (rank(b) > rank(a) ? b : a)) : "default";
}

export function sectionsFrom(payload) {
  const { schema, values, sources, readonly = {}, restart = [], notes = {} } = payload;
  return Object.entries(schema.properties).filter(([name]) => !FOLDED.has(name)).map(([name, ref]) => {
    const def = resolveRef(schema, ref);
    const fields = Object.entries(def.properties || {}).map(([field, prop]) => {
      const key = `${name}.${field}`;
      return { key, label: prop.title || field, ...fieldKind(prop), value: (values[name] || {})[field],
        default: prop.default ?? null, source: sourceOf(sources, key), readonly: readonly[key] || null,
        restart: restart.includes(key) };
    });
    return { name, title: TITLES[name] || name[0].toUpperCase() + name.slice(1), description: def.description || "",
      note: notes[name] || null, fields };
  });
}

// Reset removes the value from the layer it comes from; env and defaults can't be reset from the app.
export function resetScope(field) {
  return ["user", "project", "session"].includes(field.source) ? field.source : null;
}

// Why saving `field` at `scope` would not take effect (null when it would).
export function scopeNote(field, scope) {
  if (field.restart && scope === "session") return "Read at launch: a session value has no effect. Save to User or This project.";
  if (rank(field.source) > rank(scope)) {
    const by = field.source === "env" ? "a NETSTEAD_* environment variable" : `the ${field.source} layer`;
    return `Currently set by ${by}; a ${scope} value is saved but won't take effect while that is set.`;
  }
  return null;
}

// Clearing a field resets it only in the "Save to" layer. When the value comes from another layer that
// Reset can remove, say so instead of saving a no-op (null when clearing is fine).
export function clearHint(field, scope) {
  const layer = resetScope(field);
  if (!layer || layer === scope) return null;
  return `${field.label} comes from the ${layer} layer; clearing it in ${scope} changes nothing. Use Reset to remove it.`;
}
