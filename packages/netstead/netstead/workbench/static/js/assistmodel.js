// The assistant's view rules: step rows, the pending draft, token use, the command bar's keys, list toggles.
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py). The DOM side sets every string
// these return as text (or through esc()): summaries and results can carry model text and network values.

const LABELS = { done: "Done", failed: "Failed", skipped: "Skipped", drafted: "Awaiting approval",
  rejected: "Rejected", superseded: "Replaced by a newer question", stale: "Out of date: the network changed",
  started: "Started" };

export function stepRows(turn) {
  return ((turn && turn.steps) || []).map(s => ({ summary: s.summary || "", python: s.python || "",
    status: s.status, label: LABELS[s.status] || String(s.status), result: s.result || "" }));
}

// The draft the panel shows Approve / Reject for, or null.
export function pendingDraft(payload) {
  const d = payload && payload.draft;
  return d && d.status === "pending" ? { id: d.id, steps: (d.actions || []).length } : null;
}

// The Assistant tab's badge: "1" while a draft waits for approval.
export const assistBadge = payload => (pendingDraft(payload) ? "1" : null);

const count = n => Number(n || 0).toLocaleString("en-US");

// The key tokens are counted under (core.usage_key): "provider/model", or the provider alone without a model.
export const usageKey = (provider, model) => (model ? `${provider}/${model}` : provider);

// A local model's use is shown per model; a remote one's against the session's budget, which every remote provider
// and model share (`remote_used`).
export function budgetText(status) {
  if (status.local) {
    const used = (status.used || {})[usageKey(status.provider, status.model)] || 0;
    return `${count(used)} tokens used (local model: no limit)`;
  }
  return `${count(status.remote_used)} of ${count(status.budget)} tokens used this session (all remote models)`;
}

// The header box: Enter selects (unchanged); Ctrl+Enter or ⌘Enter asks the assistant (either modifier, on any
// platform: Ctrl+Enter is not a system shortcut on a Mac either).
export function barRoute(e) {
  if (e.key !== "Enter") return null;
  return e.ctrlKey || e.metaKey ? "ask" : "select";
}

export function toggleList(list, id, on) {
  const has = list.includes(id);
  if (on) return has ? [...list] : [...list, id];
  return list.filter(x => x !== id);
}
