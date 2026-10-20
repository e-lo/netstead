// Small DOM helpers shared by the workbench modules.
import { icon } from "./icons.js";

export const $ = id => document.getElementById(id);

// Draw the icon each `[data-icon]` element names (an icon button, or a span before a button's text) into it.
export function mountIcons(root = document) {
  for (const el of root.querySelectorAll("[data-icon]")) el.innerHTML = icon(el.dataset.icon);
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

let toastTimer = null;
export function toast(message, kind = "error") {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("info", kind === "info");
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 5000);
}
