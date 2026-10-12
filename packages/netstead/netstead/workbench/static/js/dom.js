// Small DOM helpers shared by the workbench modules.
export const $ = id => document.getElementById(id);

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
