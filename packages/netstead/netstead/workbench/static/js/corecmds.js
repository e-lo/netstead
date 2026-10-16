// Core's palette commands: the header's and the map's buttons, reachable from the keyboard.
import { addCommandTo } from "./commands.js";
import { fitLinks, fitNetwork } from "./map.js";
import { openSettings } from "./settings.js";
import { CORE, slots } from "./slots.js";
import { store } from "./store.js";
import { setViewMode } from "./table.js";
import { openWizard } from "./wizard.js";

export function registerCoreCommands() {
  const add = spec => addCommandTo(slots, store, CORE, { contexts: ["palette"], ...spec });
  add({ id: "open", title: "Open / Import…", run: () => openWizard() });
  add({ id: "settings", title: "Settings…", run: () => openSettings() });
  add({ id: "zoom_network", title: "Zoom to full network", when: c => Boolean(c.network), run: () => fitNetwork() });
  add({ id: "zoom_selection", title: "Zoom to selection", when: c => c.selectionCount > 0, run: c => fitLinks(c.selection.link_ids) });
  add({ id: "highlight_mode", title: "Highlight links (on / off)", when: c => Boolean(c.network),
    run: () => store.set({ highlightMode: !store.get().highlightMode }) });
  add({ id: "clear_highlights", title: "Clear highlights", when: c => c.highlights.length > 0, run: () => store.set({ highlights: new Set() }) });
  for (const [mode, title] of [["map", "Map"], ["split", "Split"], ["table", "Table"]]) {
    add({ id: `view_${mode}`, group: "View", title, run: () => setViewMode(mode) });
  }
}
