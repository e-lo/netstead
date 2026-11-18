// Where a tooltip goes. Pure and DOM-free (unit-tested under node: tests/test_workbench_js.py); tooltip.js owns the
// element and the events.

const OPPOSITE = { bottom: "top", top: "bottom", right: "left", left: "right" };
const SIDES = ["bottom", "top", "right", "left"];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Where to put a tip of `tip` {width, height} for a target box `target` {left, top, right, bottom} in a viewport
// {width, height}: on the `prefer`red side, else the opposite side, else a perpendicular one, whichever fits whole.
// Centered on the target along the side, then slid to stay `margin` px inside the viewport. When no side fits (a
// target the size of the window) the preferred side is used, kept on screen. Returns {left, top, side}.
export function placeTip(target, tip, viewport, { prefer = "bottom", gap = 8, margin = 4 } = {}) {
  const first = SIDES.includes(prefer) ? prefer : "bottom";
  const order = [first, OPPOSITE[first], ...SIDES.filter(s => s !== first && s !== OPPOSITE[first])];
  const fits = {
    bottom: target.bottom + gap + tip.height + margin <= viewport.height,
    top: target.top - gap - tip.height >= margin,
    right: target.right + gap + tip.width + margin <= viewport.width,
    left: target.left - gap - tip.width >= margin,
  };
  const side = order.find(s => fits[s]) || first;
  const cx = (target.left + target.right) / 2 - tip.width / 2;
  const cy = (target.top + target.bottom) / 2 - tip.height / 2;
  const at = {
    bottom: [cx, target.bottom + gap], top: [cx, target.top - gap - tip.height],
    right: [target.right + gap, cy], left: [target.left - gap - tip.width, cy],
  }[side];
  return {
    side,
    left: Math.round(clamp(at[0], margin, Math.max(margin, viewport.width - tip.width - margin))),
    top: Math.round(clamp(at[1], margin, Math.max(margin, viewport.height - tip.height - margin))),
  };
}
