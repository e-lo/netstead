// The Issues and Edits dock panels (registered like a plugin's, in every workspace), and the active network's pending
// edits and live edit warnings (GET /edits), kept in `store.edits`. Task 17 adds the Edits panel's rendering.
import { getJSON, netPath } from "./api.js";
import { toast } from "./dom.js";
import { editsBadge } from "./editmodel.js";
import { issuesBadge } from "./issuelist.js";
import { ALL_WORKSPACES, CORE } from "./slots.js";
import { store } from "./store.js";
import { addPanel } from "./workspaces.js";

let seq = 0, feedKey = null;
const activeId = () => { const s = store.get().server; return s && s.active; };

// Badges read the server state (ctx.state), so the dock refreshes them on every state event.
export function registerEditPanels() {
  addPanel(CORE, { id: "issues", title: "Issues", workspace: ALL_WORKSPACES, order: 10, badge: c => issuesBadge(c.state) });
  addPanel(CORE, { id: "edits", title: "Edits", workspace: ALL_WORKSPACES, order: 20, badge: c => editsBadge(c.state) });
}

export async function loadEdits() {
  const id = activeId(), mine = ++seq;
  if (!id) { store.set({ edits: null }); return; }
  const j = await getJSON(netPath(id, "edits"));
  if (mine === seq && activeId() === id) store.set({ edits: j });
}

// Server state arrived: reload when the active network, its version, or its edit summary moved.
export function onEditsState(server) {
  const id = server.active, h = server.networks.find(n => n.id === id);
  const key = JSON.stringify([id, h && h.version, server.edits && server.edits[id]]);
  if (key === feedKey) return;
  feedKey = key;
  loadEdits().catch(e => toast(e.message));
}
