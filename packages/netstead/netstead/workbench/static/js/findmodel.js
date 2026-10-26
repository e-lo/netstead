// The Find tab's guided form for the offline pattern parser: the patterns it understands, the sentence a pattern and
// its blanks make, and which blanks are still empty. Import-free and DOM-free: unit-tested under node
// (tests/test_workbench_js.py). The sentences are exactly what netstead.select.parse.StubParser accepts, so the form
// can only build requests the offline parser understands.

// Each pattern: the blanks it has (in the order they are shown), and the sentence they make.
export const PATTERNS = [
  { id: "street", label: "Street", blanks: ["street"] },
  { id: "between", label: "Street between two cross streets", blanks: ["street", "from", "to"] },
  { id: "type", label: "All links of a type", blanks: ["type"] },
  { id: "ids", label: "Link ids", blanks: ["ids"] },
];

export const BLANK_LABELS = { street: "Street", from: "From cross street", to: "To cross street",
  type: "Facility type", ids: "Ids" };

const patternOf = id => PATTERNS.find(p => p.id === id) || PATTERNS[0];
const clean = value => String(value == null ? "" : value).replace(/\s+/g, " ").trim();

// Link ids typed as "59, 60 61": the whole numbers in order, or null when anything else is in the text.
export function parseIds(text) {
  const tokens = clean(text).split(/[\s,]+/).filter(Boolean);
  return tokens.length && tokens.every(t => /^\d+$/.test(t)) ? tokens.map(Number) : null;
}

// The blanks of `pattern` (an id) that cannot be used yet, in display order: empty, or ids that are not numbers.
export function requiredMissing(pattern, fields) {
  return patternOf(pattern).blanks.filter(b => (b === "ids" ? !parseIds(fields.ids) : !clean(fields[b])));
}

// The line that blocks Select while blanks are open ("" when the form is complete).
export function missingMessage(pattern, fields) {
  const open = requiredMissing(pattern, fields);
  if (!open.length) return "";
  if (open.length === 1 && open[0] === "ids" && clean(fields.ids)) return "Ids: type link ids as whole numbers, like 59, 60.";
  return `Fill in: ${open.map(b => BLANK_LABELS[b]).join(", ")}.`;
}

// The sentence the form sends to Select, "" while any blank is open.
export function patternSentence(pattern, fields) {
  if (requiredMissing(pattern, fields).length) return "";
  const f = Object.fromEntries(Object.keys(BLANK_LABELS).map(b => [b, clean(fields[b])]));
  switch (patternOf(pattern).id) {
    case "between": return `${f.street} between ${f.from} and ${f.to}`;
    case "type": return `All ${f.type} links`;
    case "ids": return `Links ${parseIds(f.ids).join(", ")}`;
    default: return f.street;
  }
}

// The cross-street choices: the streets that meet the chosen street once they are known, else every street; the
// street itself is never offered as its own cross street.
export function crossChoices(allStreets, crossing, street) {
  const pool = crossing && crossing.length ? crossing : allStreets;
  const self = clean(street).toLowerCase();
  return pool.filter(n => clean(n).toLowerCase() !== self);
}

// Which input the user sees: the form while the offline parser is chosen, the free-text box for a language model.
export const useForm = provider => provider === "stub";
