// The one tooltip: a fixed element (#tip, last child of <body>) shown for any [data-tip] element on pointer hover or
// keyboard focus after a short delay, placed by tipmodel.js from the target's bounding box so it is never clipped by
// the map, a drawer or the window's edge. It hides on pointer leave, blur, Escape, scroll and press. While shown, the
// target is described by it (aria-describedby="tip"). Plugins' data-tip buttons get it for free.
import { $ } from "./dom.js";
import { placeTip } from "./tipmodel.js";

const DELAY_MS = 300;
let timer = null, target = null, previous = null;

const tipTarget = node => (node && node.closest ? node.closest("[data-tip]") : null);

function place() {
  const tip = $("tip");
  if (!target || !target.isConnected || !target.dataset.tip) { hide(); return; }
  tip.textContent = target.dataset.tip;
  tip.hidden = false;
  tip.style.left = "0px"; tip.style.top = "0px"; // measure at a known origin, then place
  const r = target.getBoundingClientRect(), t = tip.getBoundingClientRect();
  const where = placeTip(r, { width: t.width, height: t.height },
    { width: document.documentElement.clientWidth, height: document.documentElement.clientHeight },
    { prefer: target.dataset.tipSide || "bottom" });
  tip.style.left = `${where.left}px`;
  tip.style.top = `${where.top}px`;
  tip.dataset.side = where.side;
}

function release() {
  if (!target) return;
  if (previous === null) target.removeAttribute("aria-describedby"); else target.setAttribute("aria-describedby", previous);
  target = null; previous = null;
}

function show(el) {
  if (target && target !== el) release();
  if (target !== el) { target = el; previous = el.getAttribute("aria-describedby"); }
  el.setAttribute("aria-describedby", "tip");
  place();
}

export function hide() {
  clearTimeout(timer); timer = null;
  release();
  const tip = $("tip");
  if (tip) tip.hidden = true;
}

function arm(el) {
  if (el === target) return;
  clearTimeout(timer);
  timer = setTimeout(() => { timer = null; if (el.isConnected) show(el); }, DELAY_MS);
}

// Show `el`'s tip now (and again with new text): the "Copied" confirmation after a click.
export function showTipNow(el) { clearTimeout(timer); timer = null; if (el && el.dataset.tip) show(el); }
// Redraw the tip if it is up for `el`, after its data-tip changed.
export function refreshTip(el) { if (target === el) place(); }

export function wireTooltip() {
  const on = (type, fn, opts) => document.addEventListener(type, fn, opts);
  on("pointerover", e => { const el = tipTarget(e.target); if (el && e.pointerType !== "touch") arm(el); });
  on("pointerout", e => {
    const el = tipTarget(e.target);
    if (el && !(e.relatedTarget && el.contains(e.relatedTarget))) hide();
  });
  on("focusin", e => { const el = tipTarget(e.target); if (el) arm(el); });
  on("focusout", e => { if (tipTarget(e.target)) hide(); });
  on("pointerdown", () => hide(), true);
  on("keydown", e => { if (e.key === "Escape") hide(); }, true); // not stopped: the key still reaches what it was for
  on("scroll", () => hide(), true);
  window.addEventListener("resize", () => hide());
}
