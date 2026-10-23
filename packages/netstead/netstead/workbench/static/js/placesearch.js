// Pure rendering of the Area step's place-search result (import-free: unit-tested under node).

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

// The message shown in place of the candidate list when the search itself failed. A 501 means the server lacks
// the [osm] extra; anything else (a 502 from the geocoder, a network error) is a failed search.
export function searchErrorText(error) {
  const message = (error && error.message) || String(error);
  if (error && error.status === 501) return `Place search needs the [osm] extra: ${message}`;
  return `Place search failed: ${message.replace(/^place search failed:\s*/i, "")}`;
}

// `result` is {candidates: [...]} or {error: Error}. "No matches." only for a real empty answer.
export function candidatesHTML(result) {
  if (result.error) return `<span class="empty ap-error" role="alert">${esc(searchErrorText(result.error))}</span>`;
  const found = result.candidates || [];
  if (!found.length) return '<span class="empty">No matches.</span>';
  return found.map((c, i) => `<div class="cand" data-i="${i}">${esc(c.display_name)} <span class="fb-kind">${esc(c.type)}</span></div>`).join("");
}
