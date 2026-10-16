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
  server: null,               // {networks, active, selection, style}
  basemap: null,              // MapLibre style URL from /api/config (shared with the wizard's area map)
  netKey: null,               // "<id>@<version>" of the decoded active network
  net: null, attrs: null, properties: [], prop: null,
  highlightMode: false, highlights: new Set(), marker: null,
  focus: null,                // {table, id, from: "map" | "table"}: the one record last clicked (per tab, never recorded)
  related: null,              // last /related answer for focus + highlights (related.js)
  tableScope: "all",          // all | selection | highlighted | related
  relHops: 1,                 // related-records depth ("Expand a hop" toggles 1 <-> 2)
  hiddenLayers: new Set(),    // titled overlay layers switched off in Layers → Overlays (per tab, never recorded)
  layerSeq: 0,                // bumped when a layer registers or goes, so Overlays and the map redraw
  workspace: "inspect",       // the active workspace tab (per tab; remembered in localStorage)
  dockPanel: {},              // workspace id -> the dock panel showing in it
  commandSeq: 0,              // bumped when a command registers or goes, so menus re-check what applies
  pluginStatus: [],           // GET /api/plugins: each plugin's startup outcome
  hostApi: null,              // the plugin API this netstead provides ("1.1")
  pluginErrors: {},           // plugin id -> [{phase, message}] from the browser (activate, panel, command, layer…)
});

export function activeSelection(s) {
  const sel = s.server && s.server.selection;
  return sel && sel.net_id === s.server.active ? sel : null;
}
