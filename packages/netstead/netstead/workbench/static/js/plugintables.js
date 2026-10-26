// Plugin tables in the table view (Host API 1.8, `host.register_table`): read-only tables a plugin publishes, listed
// after the network's own under a "Plugins" heading. They are never edited, never scoped to the selection and never
// linked to the map; a row click is handed to the plugin (`core.table_row`). Import-free and DOM-free: unit-tested
// under node (tests/test_workbench_js.py).

// The `/tables` listing split into the network's tables and the plugins' (`plugin` names the owner).
export const splitTables = tables => ({
  core: (tables || []).filter(t => !t.plugin),
  plugin: (tables || []).filter(t => t.plugin),
});

// A plugin table's rail tooltip.
export const pluginTableTip = t =>
  `${t.title || t.name}: read-only, from the ${t.plugin} plugin${t.error ? ` (it failed: ${t.error})` : ""}`;

// One page of a plugin table: its own sort and column filters only (no selection scope, no related records).
export const pluginRowsQuery = ({ offset, limit, sort, dir, conditions }) => ({
  offset, limit, sort, dir, filter: conditions && conditions.length ? conditions : null,
});

// The line under the grid's title for a plugin table.
export const pluginHint = (t, total) =>
  `Read-only, from the ${t.plugin} plugin${total ? ": click a row to show it there." : "."}`;

// The `core.table_row` event a row click sends ({plugin, table, id}).
export const tableRowEvent = (t, id) => ({ plugin: t.plugin, table: t.name, id });

// Whether a `core.table_changed` event ({plugin, tables}) concerns the table `name`.
export const tableChanged = (payload, name) => Boolean(payload && (payload.tables || []).includes(name));
