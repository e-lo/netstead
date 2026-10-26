// Pure helpers for the Layers card: what a color swatch records, and whether the card is open. DOM-free
// (unit-tested under node: tests/test_workbench_js.py).

const COLOR_BY = { links: ["color_by", "Color links by"], nodes: ["node_color_by", "Color nodes by"] };

// The color-by a swatch competes with: links and nodes have one, the selection does not.
const activeColorBy = (kind, style) => {
  const entry = COLOR_BY[kind];
  const value = entry && style[entry[0]];
  return value && value !== "none" ? value : null;
};

// The one Style action a swatch pick records: the new color, and (for links and nodes) "none" for the layer's
// color-by when it is on, since a column coloring would hide the single color.
export function swatchPatch(kind, rgb, style) {
  const patch = { colors: { [kind]: rgb } };
  if (activeColorBy(kind, style)) patch[COLOR_BY[kind][0]] = "none";
  return patch;
}

// The swatch's tooltip: says so when picking it will turn a color-by off.
export function swatchTip(kind, style) {
  const by = activeColorBy(kind, style);
  if (by) return `Single color (turns off ${COLOR_BY[kind][1]}: ${by})`;
  return { links: "Link color", nodes: "Node color", selection: "Selection color" }[kind];
}

export const CARD_KEY = "wb.layers.open";

// Whether the Layers card is open, from its stored "true" / "false": open unless it was stored as closed.
export const parseCardOpen = raw => raw !== "false";
