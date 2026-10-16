// Named events for plugins: core's ("core.history", "core.job") and each plugin's own ("hello.greeted", sent by
// host.publish). main.js feeds it from the SSE stream; wb.on listens. Import-free and DOM-free.
export function createHub() {
  const listeners = new Map();
  return {
    on(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
      return () => listeners.get(name).delete(fn);
    },
    emit(name, payload) {
      for (const fn of [...(listeners.get(name) || [])]) {
        try { fn(payload); } catch (e) { console.error(`event ${name}:`, e); }
      }
    },
  };
}

// The page's one hub.
export const hub = createHub();
