// The three drag handles: #split-handle (the map's share of the stage in Split view), #side-handle (the left
// drawer's width) and #details-handle (the right Details panel's width). Sizes are per-browser conveniences kept in localStorage, never recorded actions. The math and
// the storage encoding live in resize.js; this module owns the pointer, keyboard and double-click events.
import { $ } from "./dom.js";
import { resizeSoon } from "./map.js";
import { DEFAULTS, STEPS, STORAGE_KEY, clampSize, parseSizes, serializeSizes } from "./resize.js";

let sizes = { ...DEFAULTS };

function readSizes() {
  try { return parseSizes(localStorage.getItem(STORAGE_KEY)); } catch (e) { return { ...DEFAULTS }; }
}
function writeSizes() {
  try { localStorage.setItem(STORAGE_KEY, serializeSizes(sizes)); } catch (e) { /* storage unavailable: sizes just aren't remembered */ }
}

// The stylesheet reads the sizes from two custom properties, so a mode switch (Map / Split / Table) keeps them.
function apply() {
  $("stage").style.setProperty("--split", `${sizes.split}%`);
  $("app").style.setProperty("--side-w", `${sizes.side}px`);
  $("app").style.setProperty("--details-w", `${sizes.details}px`);
  $("split-handle").setAttribute("aria-valuenow", String(sizes.split));
  $("side-handle").setAttribute("aria-valuenow", String(sizes.side));
  $("details-handle").setAttribute("aria-valuenow", String(sizes.details));
}

function setSize(kind, value) { sizes = { ...sizes, [kind]: clampSize(kind, value) }; apply(); }
// MapLibre re-measures on its own ResizeObserver during the drag; this is the final, settled size.
function commit() { writeSizes(); resizeSoon(); }

// The size under the pointer: the map's share of the stage's height; the drawer from the rail's edge to the pointer.
const MEASURE = {
  split: e => { const r = $("stage").getBoundingClientRect(); return r.height ? ((e.clientY - r.top) / r.height) * 100 : sizes.split; },
  side: e => e.clientX - $("side").getBoundingClientRect().left,
  details: e => $("app").getBoundingClientRect().right - e.clientX,
};
// Arrow keys nudge by one step: down grows the map, right grows the drawer (the way the handle would move).
const KEYS = { split: { ArrowUp: -1, ArrowDown: 1 }, side: { ArrowLeft: -1, ArrowRight: 1 },
  details: { ArrowLeft: 1, ArrowRight: -1 } };

function wireHandle(id, kind) {
  const el = $(id);
  el.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId); // the pointer stays ours over the map canvas and the table
    el.classList.add("dragging"); document.body.classList.add(`resizing-${kind}`);
    const move = ev => setSize(kind, MEASURE[kind](ev));
    const done = () => {
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", done); el.removeEventListener("pointercancel", done);
      el.classList.remove("dragging"); document.body.classList.remove(`resizing-${kind}`);
      commit();
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", done);
    el.addEventListener("pointercancel", done);
  });
  el.addEventListener("dblclick", () => { setSize(kind, DEFAULTS[kind]); commit(); });
  el.addEventListener("keydown", e => {
    const dir = KEYS[kind][e.key];
    if (!dir) return;
    e.preventDefault();
    setSize(kind, sizes[kind] + dir * STEPS[kind]); commit();
  });
}

export function wireHandles() {
  sizes = readSizes();
  apply();
  wireHandle("split-handle", "split");
  wireHandle("side-handle", "side");
  wireHandle("details-handle", "details");
}
