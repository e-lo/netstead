// History strip: the last action as replayable Python, plus the whole session as a script.
import { getJSON } from "./api.js";
import { $, toast } from "./dom.js";

async function copy(text) {
  try { await navigator.clipboard.writeText(text); } catch (e) { toast(`copy failed: ${e.message}`); }
}

export function showEntry(entry) {
  // How the Action arrived is shown beside its number only: the Python (and "Session as Python") stays the same.
  const viaAssistant = entry.via === "assistant";
  $("hist-seq").textContent = viaAssistant ? `#${entry.seq} · assistant` : `#${entry.seq}`;
  $("hist-seq").title = viaAssistant ? "Run by the assistant" : "";
  const py = $("hist-py");
  const python = entry.parent_seq != null ? `# via #${entry.parent_seq}: ${entry.python}` : entry.python;
  py.textContent = entry.ok ? python : `${python}  # failed: ${entry.error}`;
  py.classList.toggle("fail", !entry.ok);
}

// Takes the /api/history payload. The server builds `imports` from each entry's own import line
// (core Actions share the netstead.workbench line; a plugin's Actions live in its own package).
// An Action a plugin dispatched from inside another (`parent_seq` set) is a comment: replaying its
// parent runs it again.
export function sessionScript({ entries, imports }) {
  const lines = [...imports, "", "app = Session()  # or reuse a live session"];
  const bySeq = new Map(entries.map((e) => [e.seq, e]));
  for (const e of entries) {
    if (e.parent_seq != null) {
      const failed = e.ok ? "" : `  # failed: ${e.error}`;
      lines.push(`# via ${bySeq.get(e.parent_seq)?.action.type ?? `#${e.parent_seq}`}: ${e.python}${failed}`);
    } else {
      lines.push(e.ok ? e.python : `# failed: ${e.python}  # ${e.error}`);
    }
  }
  return lines.join("\n");
}

export function wireHistory() {
  $("hist-copy").onclick = () => copy($("hist-py").textContent);
  $("hist-all").onclick = async () => {
    if (!$("hist-panel").classList.toggle("open")) return;
    try { $("hist-script").textContent = sessionScript(await getJSON("/api/history")); } catch (e) { toast(e.message); }
  };
  $("hist-copy-all").onclick = () => copy($("hist-script").textContent);
}
