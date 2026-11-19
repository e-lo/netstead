// DOM helpers the cards panel (main.js) and the card dialog (carddialog.js) share. Card and user text is only ever
// set as text, never parsed as markup.

// A DOM element. `text` is its text content, `on<event>` a handler, `disabled` / `checked` properties, the rest
// attributes; string children become text nodes.
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el[key] = value;
    else if (key === "disabled" || key === "checked") el[key] = Boolean(value);
    else el.setAttribute(key, value);
  }
  el.append(...children);
  return el;
}
