// Panel sizes: the map/table split (the map's share of the stage, in percent), the left drawer's width and the right
// Details panel's width (px).
// Pure: the arithmetic and the localStorage encoding; handles.js owns the pointer events and the DOM.
// A size that is not a finite number, or a store that is not an object, falls back to the default for that
// field; a number outside the range is clamped (a stale store never leaves a panel unusably small or hidden).

const RANGES = { split: [20, 80], side: [240, 720], details: [260, 640] };
export const DEFAULTS = Object.freeze({ split: 55, side: 330, details: 320 });
export const STEPS = Object.freeze({ split: 2, side: 16, details: 16 }); // one keyboard arrow
export const STORAGE_KEY = "wb.sizes";

export function clampSize(kind, value) {
  const [lo, hi] = RANGES[kind];
  const n = typeof value === "number" ? value : NaN;
  if (!Number.isFinite(n)) return DEFAULTS[kind];
  return Math.min(hi, Math.max(lo, Math.round(n)));
}

export function parseSizes(json) {
  let obj = null;
  try { obj = JSON.parse(json); } catch (e) { obj = null; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return { ...DEFAULTS };
  return { split: clampSize("split", obj.split), side: clampSize("side", obj.side), details: clampSize("details", obj.details) };
}

export function serializeSizes(sizes) {
  return JSON.stringify({ split: clampSize("split", sizes.split), side: clampSize("side", sizes.side),
    details: clampSize("details", sizes.details) });
}
