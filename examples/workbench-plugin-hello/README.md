# netstead-hello

A minimal netstead Workbench plugin. Install it next to netstead, and `netstead app` loads it:

```bash
uv pip install -e examples/workbench-plugin-hello
```

Settings → Plugins lists it, with an on/off switch that applies on restart.

The plugin adds the following:

- the `hello.greet` Action
- the `[plugins.hello] prefix` setting
- state under `plugins.hello`
- `GET /api/plugins/hello/count`
- a front-end module at `/plugins/hello/main.js`, which the Workbench loads at startup: a **Hello** panel in the
  Inspect dock (a form generated from the Action's schema, with a greeting count on its tab), "Say hello" in the
  command palette (Ctrl+K), "Say hello to this record" on a right-clicked link, node or table row, and "Say hello to
  the selection" under **Selection actions**

To turn it off without uninstalling it, set `[app] disabled_plugins = ["hello"]`.

Plugins are full-trust Python running inside the Workbench process. See the
[Write a Workbench plugin](../../packages/netstead/docs/cookbook/workbench-plugins.md) cookbook page.
