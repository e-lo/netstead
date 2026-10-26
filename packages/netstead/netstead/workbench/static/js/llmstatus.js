// The header robot's status: ready (green), offline pattern parser only (yellow), or not available (red).
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_js.py). The color says whether the chosen
// provider and model can answer; whether data leaves this machine is the tooltip's job (privacy), never the color.

export const LLM_STATES = ["ready", "offline", "unavailable"];

// Why a provider cannot answer, in a short clause: the server's own reason when it gave one, else the likely cause.
function whyUnavailable(row, provider) {
  if (!row) return `${provider} is not set up`;
  if (row.error) return String(row.error).replace(/\.+$/, "");
  if (row.kind === "local") return `${row.label} is not running`;
  return row.configured ? `${row.label} is not available` : `no ${row.label} API key is set`;
}

// {state, name, phrase, text}: `row` is the provider's /api/llm/providers row (null for the stub or an unknown
// provider), `model` the model id shown in the picker, `modelMissing` true when a local provider does not have
// that model installed, and `privacy` the sentence on what leaves this machine (appended to the tooltip).
export function llmStatus({ provider, row = null, model = "", modelMissing = false, privacy = "" }) {
  if (provider === "stub") {
    const name = "Offline (pattern)";
    return finish("offline", name, "offline pattern parser only", privacy);
  }
  const name = `${row ? row.label : provider}${model ? ` · ${model}` : ""}`;
  if (!row || !row.usable) return finish("unavailable", name, `not available: ${whyUnavailable(row, provider)}`, privacy);
  if (modelMissing) {
    const pull = row.kind === "local" && model ? `; run: ollama pull ${model}` : "";
    return finish("unavailable", name, `not available: ${model || "the model"} is not installed${pull}`, privacy);
  }
  return finish("ready", name, "ready", privacy);
}

function finish(state, name, phrase, privacy) {
  const text = `Language model: ${name} — ${phrase}.${privacy ? ` ${privacy}` : ""}`;
  return { state, name, phrase, text };
}
