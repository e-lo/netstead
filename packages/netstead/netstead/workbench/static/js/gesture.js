// A right-click menu must not open at the end of a right-drag (MapLibre rotates the map on one). Browsers fire
// `contextmenu` at different moments: Windows and Linux on mouseup, macOS on mousedown (and on Ctrl+click). The guard
// is fed the pointer and says when to open: at once when the button is already up and the pointer didn't move,
// deferred to mouseup while it is still down, and never after a drag. Import-free and DOM-free: unit-tested under
// node (tests/test_workbench_slots_js.py).
export const DRAG_PX = 4;

export function createClickGuard(threshold = DRAG_PX) {
  let start = null, down = false, moved = false, pending = null;
  const move = (x, y) => {
    if (down && start && Math.hypot(x - start.x, y - start.y) > threshold) moved = true;
  };
  return {
    down(x, y) { start = { x, y }; down = true; moved = false; pending = null; },
    move,
    // mouseup: the menu deferred at mousedown, to open now (null when there is none, or the pointer moved).
    up(x, y) {
      move(x, y);
      down = false;
      const open = pending;
      pending = null;
      return open && !moved ? open : null;
    },
    // contextmenu: `open` to run now, or null (deferred until mouseup, or the end of a drag).
    menu(open) {
      if (down) { pending = open; return null; }
      return moved ? null : open;
    },
  };
}
