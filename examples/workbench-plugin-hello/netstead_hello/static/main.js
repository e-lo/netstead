// The hello plugin's front end. The Workbench imports this module once at startup and calls activate(wb) (see the
// cookbook page "Write a Workbench plugin"). Everything goes through `wb`: no imports, no reaching into core's DOM.
export function activate(wb) {
  let greeted = 0;

  // A dock panel in the Inspect workspace: a form generated from the Action's JSON Schema, and a count badge.
  const panel = wb.registerPanel({
    workspace: "inspect",
    id: "hello.panel",
    title: "Hello",
    order: 50,
    badge: () => greeted || null, // a count on the tab; return {dirty: true, title: "…"} for unsaved work
    render(el) {
      el.innerHTML =
        '<p class="muted">Greet someone. Each greeting is a recorded Action: see the history strip.</p>' +
        '<div></div><button class="mini">Greet</button> <span class="muted" role="status"></span>';
      const [, slot, button, status] = el.children;
      const form = wb.schemaForm(slot, wb.actionSchema("hello.greet"), { name: "world" });
      button.onclick = async () => {
        const errors = form.errors();
        if (errors.length) {
          status.textContent = errors.join("; ");
          return;
        }
        try {
          status.textContent = await wb.api.dispatch({ ...form.value(), type: "hello.greet" });
        } catch (e) {
          status.textContent = e.message;
        }
      };
    },
  });

  // The count comes from the plugin's own route; host.publish("greeted") says when to re-read it.
  const refresh = async () => {
    greeted = (await wb.api.get("/count")).greeted;
    panel.refreshBadge();
  };
  wb.on("greeted", () => refresh());
  refresh().catch(e => wb.toast(e.message));

  // Commands: one in the palette, one on a right-clicked map feature or table row, one for the selection.
  wb.registerCommand({
    id: "hello.greet",
    title: "Say hello",
    contexts: ["palette"],
    run: () => wb.api.dispatch({ type: "hello.greet", name: "world" }),
  });
  wb.registerCommand({
    id: "hello.greet_record",
    title: "Say hello to this record",
    contexts: ["feature", "row"],
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.target.table} ${ctx.target.id}` }),
  });
  wb.registerCommand({
    id: "hello.greet_selection",
    title: "Say hello to the selection",
    contexts: ["selection"],
    when: ctx => ctx.selectionCount > 0,
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.selectionCount} selected links` }),
  });
}
