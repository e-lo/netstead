// Map layers by network component: "roadway" now, "transit" reserved for P6 (accepted, not drawn yet). map.js
// builds its deck.gl layers from this registry on every render, core's own included. DOM-free and deck-free
// (factories close over deck.gl): unit-tested under node (tests/test_workbench_slots_js.py).
import { CORE, checkId } from "./slots.js";

export const COMPONENTS = ["roadway", "transit"];
// Core's layers, bottom to top. A plugin layer defaults to just above related records, under the user's own
// highlights, focus and marker, which stay on top.
export const CORE_ORDER = { base: 100, selection: 200, related: 300, highlighted: 400, focus: 500, marker: 600 };
export const PLUGIN_ORDER = 350;

export function createLayerRegistry() {
  const items = new Map();
  let seq = 0;
  const sorted = () => [...items.values()].sort((a, b) => a.order - b.order || a.seq - b.seq);
  return {
    // factory(ctx) -> a deck.gl layer, an array of them, or nothing. Returns a function that unregisters it (true
    // when it did: a stale disposer never removes a later registration with the same id).
    register(owner, component, id, factory, { order, title = null } = {}) {
      if (!COMPONENTS.includes(component)) {
        throw new Error(`unknown component ${JSON.stringify(component)}: use ${COMPONENTS.join(" or ")}`);
      }
      checkId(owner, id);
      if (typeof factory !== "function") throw new Error(`layer ${id}: factory must be a function`);
      if (items.has(id)) throw new Error(`layer ${JSON.stringify(id)} is already registered`);
      const fallback = owner === CORE ? CORE_ORDER[id] ?? 0 : PLUGIN_ORDER;
      const entry = { owner, component, id, factory, title, order: order ?? fallback, seq: seq++ };
      items.set(id, entry);
      return () => items.get(id) === entry && items.delete(id);
    },
    removeOwner(owner) { for (const [id, l] of items) if (l.owner === owner) items.delete(id); },
    ids: () => sorted().map(l => l.id),
    // Layers with a title: the Layers panel lists them under "Overlays" with a show/hide switch.
    toggleable: () => sorted().filter(l => l.title),
    // Every layer of `component`, in order, skipping `hidden` ids. A factory that throws, returns something that
    // isn't a deck.gl layer (`isLayer`), or a plugin deck layer whose id isn't namespaced ("hello.…"; core's ids such
    // as "links" drive picking) is left out and reported. Plugin factories get `pluginCtx()` (copies they can't use
    // to change the page) when given, else `ctx`.
    build(component, ctx, hidden = new Set(), { isLayer = () => true, pluginCtx = null } = {}) {
      const layers = [], errors = [];
      let forPlugins = null;
      for (const l of sorted()) {
        if (l.component !== component || hidden.has(l.id)) continue;
        let out;
        const c = l.owner === CORE || !pluginCtx ? ctx : (forPlugins = forPlugins || pluginCtx());
        try { out = l.factory(c); } catch (e) {
          errors.push({ owner: l.owner, id: l.id, error: String((e && e.message) || e) });
          continue;
        }
        for (const layer of [].concat(out ?? [])) {
          if (!layer) continue;
          if (!isLayer(layer)) {
            errors.push({ owner: l.owner, id: l.id, error: `layer ${l.id}: the factory returned something that is not a deck.gl layer` });
            continue;
          }
          if (l.owner !== CORE && !(typeof layer.id === "string" && layer.id.startsWith(`${l.owner}.`))) {
            errors.push({ owner: l.owner, id: l.id, error: `deck layer id ${JSON.stringify(layer.id)} must start with "${l.owner}."` });
            continue;
          }
          layers.push(layer);
        }
      }
      return { layers, errors };
    },
  };
}

// The page's one layer registry.
export const layerRegistry = createLayerRegistry();
