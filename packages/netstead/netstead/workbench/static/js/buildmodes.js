// The build form's network types (import-free: unit-tested under node). A build keeps a way if any chosen type allows
// it; "all" is every highway, so it stands alone (choosing it clears the others, and choosing another clears it).

export const MODES = [
  { id: "drive", label: "Drive" },
  { id: "walk", label: "Walk" },
  { id: "bike", label: "Bike" },
  { id: "all", label: "All highways" },
];
const KNOWN = MODES.map(m => m.id);

// `selected` with `mode` switched on or off, in MODES order.
export function toggleMode(selected, mode, on) {
  const next = new Set(on ? selected : selected.filter(m => m !== mode));
  if (on) {
    if (mode === "all") next.clear();
    else next.delete("all");
    next.add(mode);
  }
  return KNOWN.filter(m => next.has(m));
}

// The modes to tick for a settings or recorded value: one name, or a list; unknown names are dropped.
export function normalizeModes(value) {
  const list = Array.isArray(value) ? value : [value];
  const known = KNOWN.filter(m => list.includes(m));
  return known.includes("all") ? ["all"] : known.length ? known : ["drive"];
}

// "" when the selection can be built, else why not.
export const modesProblem = selected => (selected.length ? "" : "Choose at least one network type.");
