// Minimal pub/sub store. `server` mirrors the session state pushed over SSE;
// every other key is per-tab view state that never reaches the server.
export function createStore(initial) {
  const state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const changed = Object.keys(patch).filter(k => state[k] !== patch[k]);
      if (!changed.length) return;
      Object.assign(state, patch);
      for (const s of subs) if (s.keys.some(k => changed.includes(k))) s.fn(state);
    },
    subscribe(keys, fn) { const s = { keys, fn }; subs.add(s); return () => subs.delete(s); },
  };
}

export const store = createStore({
  server: null,               // {networks, active, selection, style, plugins, edits, issues, saved_selections, filters}
  basemap: null,              // MapLibre style URL from /api/config (shared with the wizard's area map)
  netKey: null,               // "<id>@<version>" of the decoded active network
  net: null, attrs: null, properties: [], prop: null,
  nodeProperties: [],         // the node table's styleable columns (GET /properties?table=node)
  nodeProp: null,             // the `style.node_color_by` column's values (per tab, derived from the server's style)
  widthProp: null,            // the `style.width_by` column's values (per tab, derived from the server's style)
  filterIds: null,            // Set of link ids the active network's recorded Filter matches (per tab, derived), or null
  highlightMode: false, highlights: new Set(), marker: null,
  focus: null,                // {table, id, from: "map" | "table"}: the one record last clicked (per tab, never recorded)
  related: null,              // last /related answer for focus + highlights (related.js)
  tableScope: "all",          // all | selection | highlighted | related | filtered
  relHops: 1,                 // related-records depth ("Expand a hop" toggles 1 <-> 2)
  hiddenLayers: new Set(),    // titled overlay layers switched off in Layers → Overlays (per tab, never recorded)
  layerSeq: 0,                // bumped when a layer registers or goes, so Overlays and the map redraw
  workspace: "inspect",       // the active workspace tab (per tab; remembered in localStorage)
  dockPanel: {},              // workspace id -> the dock panel showing in it
  commandSeq: 0,              // bumped when a command registers or goes, so menus re-check what applies
  pluginStatus: [],           // GET /api/plugins: each plugin's startup outcome
  hostApi: null,              // GET /api/plugins host_api: the plugin API this netstead provides
  pluginErrors: {},           // plugin id -> [{phase, message}] from the browser (activate, panel, command, layer…)
  issues: null,               // /issues/markers for the active network: {net_id, version, markers, records}
  issueFilter: { severity: [], table: "", code: "", located: "", thisRecord: false },
  issueFocus: null,           // the issue number last picked in the list or on the map
  fixTarget: null,            // {table, id, column, note}: what "Fix the picked issue…" edits (an issue or a warning)
  edits: null,                // GET /edits for the active network: pending edits and live edit warnings
  assistant: null,            // GET /api/assistant: the conversation, the pending draft, the status (session-only)
});

export function activeSelection(s) {
  const sel = s.server && s.server.selection;
  return sel && sel.net_id === s.server.active ? sel : null;
}
