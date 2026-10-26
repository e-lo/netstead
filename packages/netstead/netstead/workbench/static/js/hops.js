// The table's "Related: 1 step / 2 steps" control (import-free: unit-tested under node).

export const HOPS_TIP = "How far to follow foreign keys from the selected records: 1 step (direct references) " +
  "or 2 steps (references of references)";

// Only the Related scope uses the depth, so the control shows for it alone. `value` is the select's value.
export const hopsView = (scope, hops) => ({ hidden: scope !== "related", value: String(hops === 2 ? 2 : 1) });

// The depth a select value stands for (anything but "2" is one step).
export const hopsFromValue = value => (String(value) === "2" ? 2 : 1);
