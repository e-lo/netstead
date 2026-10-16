// Loading plugin front ends and reporting plugin failures, with every effect injected (the module import, the wb
// factory, the store and the toast). DOM-free: unit-tested under node (tests/test_workbench_slots_js.py); plugins.js
// passes the real parts.
import { CORE } from "./slots.js";

// A failure in plugin (or core) front-end code: logged, toasted, and kept in the store's pluginErrors for Settings →
// Plugins (core's only toasts). A passive failure (a badge, a layer, a command's `when`) would otherwise repeat on
// every redraw, so each distinct one is reported once; `{repeat: true}` (a command the user ran) logs and toasts every
// time. The stored list never holds the same failure twice.
export function createErrorReporter({ store, toast, log }) {
  const seen = new Set();
  return function report(owner, phase, error, { repeat = false } = {}) {
    const message = String((error && error.message) || error);
    const key = `${owner}|${phase}|${message}`;
    const fresh = !seen.has(key);
    if (!fresh && !repeat) return;
    seen.add(key);
    log(`[${owner}] ${phase}:`, error);
    toast(owner === CORE ? message : `Plugin ${owner}: ${message}`);
    if (owner === CORE || !fresh) return;
    const all = store.get().pluginErrors;
    store.set({ pluginErrors: { ...all, [owner]: [...(all[owner] || []), { phase, message }] } });
  };
}

// The palette's and context menus' two reporters: a `when` that throws is passive; a `run` the user asked for.
export const commandReporters = report => ({
  whenError: (command, e) => report(command.owner, `command ${command.id}`, e),
  runError: (command, e) => report(command.owner, `command ${command.id}`, e, { repeat: true }),
});

const DONE = Symbol("done");
const TIMED_OUT = Symbol("timed out");

// Import one plugin's module and call its activate(wb), the two timed together. A failure (a module that won't import,
// no activate export, activate throwing, or the pair not finishing within `timeoutMs`) rolls back what it registered
// and is reported; resolves to whether it activated. Never rejects.
// deps: {importModule(status), createWb(pluginId) -> {wb, rollback}, timeoutMs, report(owner, phase, error)}
export async function activateOne(status, { importModule, createWb, timeoutMs, report }) {
  const { wb, rollback } = createWb(status.id);
  const work = (async () => {
    const mod = await importModule(status);
    if (!mod || typeof mod.activate !== "function") throw new Error(`${status.frontend} exports no activate(wb)`);
    await mod.activate(wb);
  })();
  let timer;
  const limit = new Promise(resolve => { timer = setTimeout(resolve, timeoutMs, TIMED_OUT); });
  const outcome = await Promise.race([work.then(() => DONE, e => e), limit]);
  clearTimeout(timer);
  if (outcome === DONE) return true;
  rollback();
  if (outcome === TIMED_OUT) {
    report(status.id, "activate", new Error(`did not load and activate within ${timeoutMs / 1000} s`));
    // One that fails later (say, registering into its rolled-back wb) is reported too, not an unhandled rejection.
    work.catch(e => report(status.id, "activate", e));
  } else {
    report(status.id, "activate", outcome);
  }
  return false;
}

// Activate every loaded plugin that ships a front end, all at once: hung plugins cost one time limit between them,
// not one each. Resolves when every one has activated, failed or timed out.
export async function activatePlugins(statuses, deps) {
  const runs = [];
  for (const status of statuses) {
    // "core" is reserved (spec.problems refuses it too): its owner tag would skip the id prefix check, and a
    // rollback's removeOwner("core") would remove core's own registrations.
    if (status.id === CORE) { deps.report(status.id, "activate", 'the plugin id "core" is reserved'); continue; }
    if (status.state === "loaded" && status.frontend) runs.push(activateOne(status, deps));
  }
  await Promise.allSettled(runs);
}
