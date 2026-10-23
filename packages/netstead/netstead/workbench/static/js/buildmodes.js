// The build form's network types (import-free: unit-tested under node). A build keeps a way if any chosen type allows
// it. "roads" is the drive classes plus busways; "paths" is footways, paths, steps, tracks, cycleways and bridleways.
// Proposed, construction and corridor ways are never built.

export const MODES = [
  { id: "roads", label: "Roads" },
  { id: "paths", label: "Paths" },
];
const KNOWN = MODES.map(m => m.id);

// Retired names (old settings, recorded builds) and what they now mean.
const LEGACY = { drive: ["roads"], walk: ["roads", "paths"], bike: ["roads", "paths"], all: ["roads", "paths"] };

// `selected` with `mode` switched on or off, in MODES order.
export function toggleMode(selected, mode, on) {
  const next = new Set(selected);
  if (on) next.add(mode); else next.delete(mode);
  return KNOWN.filter(m => next.has(m));
}

// The modes to tick for a settings or recorded value: one name, or a list; retired names are mapped, unknown dropped.
export function normalizeModes(value) {
  const list = (Array.isArray(value) ? value : [value]).flatMap(v => LEGACY[v] || [v]);
  const known = KNOWN.filter(m => list.includes(m));
  return known.length ? known : ["roads"];
}

// "" when the selection can be built, else why not.
export const modesProblem = selected => (selected.length ? "" : "Choose at least one network type.");
