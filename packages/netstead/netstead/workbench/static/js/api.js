// Fetch + SSE client for the workbench API. Every state change goes through dispatch().
async function readJSON(r) {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    // FastAPI's own 422 `detail` is an array of error objects, not a string;
    // stringify it so the toast shows something readable instead of "[object Object]".
    const detail = typeof j.detail === "string" ? j.detail : j.detail != null ? JSON.stringify(j.detail) : undefined;
    // Our own 422s are {error, detail: [pydantic errors]}: say which field failed, not just "invalid build".
    const why = j.error && Array.isArray(j.detail)
      ? j.detail.map(d => (d.loc && d.loc.length ? `${d.loc.join(".")}: ` : "") + d.msg).join("; ") : "";
    throw Object.assign(new Error(j.error ? (why ? `${j.error}: ${why}` : j.error) : detail || r.statusText), { status: r.status });
  }
  return j;
}

export const getJSON = path => fetch(path).then(readJSON);

export async function postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return readJSON(r);
}

export async function getBuffer(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.arrayBuffer();
}

// Resolves to the action's result. A job action (open/build) resolves at once to {job_id};
// its outcome arrives later as `job` + `history` + `state` SSE events.
export async function dispatch(action) {
  return (await postJSON("/api/actions", action)).result;
}

export const netPath = (netId, rest) => `/api/n/${encodeURIComponent(netId)}/roadway/${rest}`;

export function subscribe(handlers) {
  const source = new EventSource("/api/events");
  for (const [type, fn] of Object.entries(handlers)) source.addEventListener(type, e => fn(JSON.parse(e.data)));
  return source;
}

// PUT/DELETE/POST for non-action routes (e.g. /api/llm). Extra headers carry X-Netstead-Secrets.
export async function sendJSON(method, path, body, headers = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return readJSON(await fetch(path, init));
}
