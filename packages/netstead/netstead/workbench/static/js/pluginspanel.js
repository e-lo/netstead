// Settings → Plugins (UX principle 8): each plugin's id, version, required API and whether it fits, its load errors,
// and an on/off switch. The switch saves app.disabled_plugins at user scope. It is read at launch, so a change
// applies the next time `netstead app` starts; the row says "restart to apply" until then.
import { dispatch, getJSON } from "./api.js";
import { $, toast } from "./dom.js";
import { pluginRowHTML, pluginRows, pluginsLock, withPluginEnabled } from "./pluginlist.js";
import { store } from "./store.js";

const GUIDE = "https://e-lo.github.io/netstead/netstead/cookbook/workbench-plugins/"; // mkdocs site_url + page

export async function renderPluginsPanel() {
  const settings = await getJSON("/api/settings");
  const disabled = settings.values.app.disabled_plugins || [];
  const s = store.get();
  // A project file or NETSTEAD_* variable that sets the list wins over the user value the switches save: they are
  // disabled, and the note says where to change it. Otherwise the list shown is the user value itself.
  const note = pluginsLock(settings.sources["app.disabled_plugins"] || "default", settings.paths);
  const rows = pluginRows({ statuses: s.pluginStatus, hostApi: s.hostApi, disabled, browserErrors: s.pluginErrors,
    locked: Boolean(note) });
  $("plugins-host").textContent = `This netstead provides plugin API ${s.hostApi || "?"}. Switching a plugin on or off ` +
    "applies the next time netstead app starts.";
  $("plugins-note").textContent = note || "";
  $("plugins-note").hidden = !note;
  $("plugins-list").innerHTML = rows.length
    ? "<table><thead><tr><th>Plugin</th><th>Version</th><th>Plugin API</th><th>Status</th><th>Load</th></tr></thead>" +
      `<tbody>${rows.map(pluginRowHTML).join("")}</tbody></table>`
    : `<p class="empty">No plugins installed. See <a href="${GUIDE}" target="_blank" rel="noopener">Write a Workbench plugin</a>.</p>`;
}

export function wirePluginsPanel() {
  // A plugin that fails while the section is open (a badge, a command) shows up at once.
  store.subscribe(["pluginErrors", "pluginStatus"], () => {
    if (!$("settings").hidden && !$("plugins-panel").hidden) renderPluginsPanel().catch(err => toast(err.message));
  });
  $("plugins-list").onchange = async e => {
    const el = e.target.closest("input[data-plugin]");
    if (!el) return;
    el.disabled = true;
    try {
      const settings = await getJSON("/api/settings");
      const value = withPluginEnabled(settings.values.app.disabled_plugins || [], el.dataset.plugin, el.checked);
      await dispatch({ type: "set_setting", key: "app.disabled_plugins", value, scope: "user" });
    } catch (err) {
      toast(err.message);
    }
    await renderPluginsPanel().catch(err => toast(err.message));
    // The list was redrawn: keep keyboard focus on the switch that was used.
    const again = $("plugins-list").querySelector(`input[data-plugin="${CSS.escape(el.dataset.plugin)}"]`);
    if (again) again.focus();
  };
}
