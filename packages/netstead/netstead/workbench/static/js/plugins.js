// The browser side of plugins: import each loaded plugin's ES module and call its activate(wb) (plugins design,
// "Front end"), all plugins at once. One plugin's failure (a module that won't import, no activate export, activate
// throwing, or the two not finishing in time) rolls back what it registered, is listed in Settings → Plugins, and
// never stops the others (pluginload.js).
import { openActionForm } from "./actform.js";
import { dispatch, getJSON, postJSON } from "./api.js";
import { actionCommands, addCommandTo, frozenCopy } from "./commands.js";
import { toast } from "./dom.js";
import { schemaForm } from "./formview.js";
import { hub } from "./hub.js";
import { addLayer } from "./map.js";
import { activatePlugins, createErrorReporter } from "./pluginload.js";
import { slots } from "./slots.js";
import { activeSelection, store } from "./store.js";
import { createWb } from "./wbhost.js";
import { addPanel, addWorkspace, restoreWorkspace, showPanel } from "./workspaces.js";

export const ACTIVATE_TIMEOUT_MS = 5000;

// A failure in plugin (or core) front-end code: logged, toasted, and listed in Settings → Plugins (pluginload.js).
export const reportPluginError = createErrorReporter({ store, toast, log: (...args) => console.error(...args) });

// A command registered by anyone; menus and the "Selection actions" button re-check what applies.
export const addCommand = (owner, spec) => addCommandTo(slots, store, owner, spec);

export async function loadPlugins() {
  const [{ host_api: hostApi, plugins }, { actions }] = await Promise.all([getJSON("/api/plugins"), getJSON("/api/actions")]);
  store.set({ pluginStatus: plugins, hostApi });
  // Declarative first: every plugin Action has a form, whether or not its plugin ships JavaScript.
  for (const spec of actionCommands(actions, plugins)) addCommand(spec.owner, { ...spec, run: () => openActionForm(spec.entry) });
  const shared = { hostApi, actionTypes: new Set(actions.map(a => a.type)), schemas: new Map(actions.map(a => [a.type, a.schema])) };
  await activatePlugins(plugins, {
    importModule: status => import(`${status.frontend}?v=${encodeURIComponent(status.version)}`),
    createWb: id => createWb(id, hostDeps(shared)),
    timeoutMs: ACTIVATE_TIMEOUT_MS,
    report: reportPluginError,
  });
  restoreWorkspace();
}

function hostDeps({ hostApi, actionTypes, schemas }) {
  return {
    hostApi, store, activeSelection, getJSON, postJSON, dispatch, fetch: (url, init) => fetch(url, init),
    events: hub, addCommand, layers: { register: addLayer }, dock: { addWorkspace, addPanel, showPanel },
    schemaForm, actionTypes, actionSchema: type => schemas.get(type) || null, toast, onError: reportPluginError,
    frozenCopy,
  };
}
