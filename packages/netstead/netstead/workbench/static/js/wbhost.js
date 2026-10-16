// The `wb` object a plugin's activate(wb) receives (plugins design, "The wb host object"). It is built from
// injected parts, so it is import-free and DOM-free and unit-tested under node; plugins.js passes the real ones.
// Everything a plugin registers or subscribes through it is tracked, so a failed activation is rolled back whole.

// "/count" -> "/api/plugins/hello/count". A plugin's fetches stay under its own routes: the path is resolved the way
// the browser will resolve it (so "%2e%2e" and "\\" count as ".." and "/"), and must still be under the plugin's base.
export function pluginPath(pluginId, path) {
  const base = `/api/plugins/${pluginId}`;
  const refuse = () => new Error(`${pluginId}: wb.api paths start with "/" and stay under ${base}`);
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//")) throw refuse();
  let url;
  try { url = new URL(base + path, "http://x"); } catch (e) { throw refuse(); }
  if (url.origin !== "http://x" || !(url.pathname === base || url.pathname.startsWith(`${base}/`))) throw refuse();
  return url.pathname + url.search + url.hash;
}

// "greeted" -> "hello.greeted" (the plugin's own event); "catalog.added" and "core.history" stay as they are.
export const eventName = (pluginId, name) => (name.includes(".") ? name : `${pluginId}.${name}`);

const pick = (obj, key) => key.split(".").reduce((v, k) => (v == null ? undefined : v[k]), obj);

// Whether any of `keys` (top-level, or dotted such as "plugins.hello") differs between two server states.
export const keysChanged = (prev, next, keys) => keys.some(k => JSON.stringify(pick(prev, k)) !== JSON.stringify(pick(next, k)));

// deps: {hostApi, store, activeSelection, getJSON, postJSON, fetch, dispatch, events: {on}, addCommand,
//        layers: {register}, dock: {addWorkspace, addPanel, showPanel}, schemaForm, actionTypes, actionSchema,
//        toast, onError(pluginId, phase, error), frozenCopy}
export function createWb(pluginId, deps) {
  const disposers = [];
  let closed = false;
  const track = off => { disposers.push(off); return off; };
  const open = () => {
    if (closed) throw new Error(`${pluginId}: activation was rolled back; nothing more can be registered`);
  };
  // A plugin callback that throws (or rejects) is reported, never passed on to core.
  const safe = (fn, phase) => (...args) => {
    try {
      const out = fn(...args);
      if (out && typeof out.catch === "function") out.catch(e => deps.onError(pluginId, phase, e));
      return out;
    } catch (e) {
      deps.onError(pluginId, phase, e);
      return undefined;
    }
  };
  // The server state as plugin code sees it: a deep-frozen copy (one per server object), never the page's own.
  const serverNow = () => deps.frozenCopy(deps.store.get().server);
  const selectionOf = server => deps.activeSelection({ server }) || null;
  // Listeners are refused after a rollback too: an activate that timed out may resume and subscribe later.
  const watch = (changed, fn, phase) => {
    open();
    let prev = serverNow();
    const cb = safe(fn, phase);
    return track(deps.store.subscribe(["server"], s => {
      const before = prev;
      prev = deps.frozenCopy(s.server);
      if (changed(before, prev)) cb(prev);
    }));
  };

  const wb = {
    id: pluginId,
    hostApi: deps.hostApi,
    api: {
      fetch: (path, init) => deps.fetch(pluginPath(pluginId, path), init),
      get: path => deps.getJSON(pluginPath(pluginId, path)),
      post: (path, body) => deps.postJSON(pluginPath(pluginId, path), body),
      dispatch: action => deps.dispatch(action),
    },
    store: {
      get: serverNow,
      subscribe: (keys, fn) => watch((a, b) => keysChanged(a, b, keys), fn, "store"),
    },
    selection: {
      get: () => selectionOf(serverNow()),
      subscribe: fn => watch((a, b) => JSON.stringify(selectionOf(a)) !== JSON.stringify(selectionOf(b)),
        server => fn(selectionOf(server)), "selection"),
    },
    registerWorkspace(spec) { open(); return track(deps.dock.addWorkspace(pluginId, spec)); },
    registerPanel(spec) { open(); const panel = deps.dock.addPanel(pluginId, spec); track(panel.dispose); return panel; },
    registerCommand(spec) { open(); return track(deps.addCommand(pluginId, spec)); },
    registerLayer(component, id, factory, options) {
      open();
      return track(deps.layers.register(pluginId, component, id, factory, options));
    },
    schemaForm: (el, schema, value, options) => deps.schemaForm(el, schema, value, options),
    actionSchema: type => deps.actionSchema(type),
    hasAction: type => deps.actionTypes.has(type),
    on(name, fn) { open(); return track(deps.events.on(eventName(pluginId, name), safe(fn, `event ${name}`))); },
    showPanel: id => deps.dock.showPanel(id),
    toast: message => deps.toast(`${pluginId}: ${message}`),
  };
  const rollback = () => {
    closed = true;
    for (const off of disposers.splice(0).reverse()) {
      try { off(); } catch (e) { /* already removed */ }
    }
  };
  return { wb, rollback };
}
