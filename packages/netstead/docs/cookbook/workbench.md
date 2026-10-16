---
title: Explore networks in the Netstead Workbench
audience: users
kind: howto
summary: Open one or more GMNS networks in a local browser app. The map and tables are linked, natural-language selection is built in, and every action can be replayed as Python.
---

# Explore networks in the Netstead Workbench

## When to use this

You want to look around a network interactively. That means panning the map, browsing tables, checking
what a selection phrase resolves to, and copying the exact Python that reproduces what you did.
For a single self-contained HTML file to send someone, see [View a network on a map](view-your-network.md).

## Quick start

```bash
uv run netstead app ./my-network
```

Open <http://127.0.0.1:8850>. Open more networks with **Open / Import…** in the header (see below) or
the **Recent** list, and switch between them with the network picker. `netstead viz` and `netstead select-serve`
are aliases of `netstead app` (the old standalone apps they used to start are gone).

```bash
uv run netstead app ./base ./build --port 8900 --basemap esri --provider anthropic
```

The server binds `127.0.0.1` by default. While bound to a local host (`127.0.0.1`, `localhost`, or `[::1]`),
it only accepts requests whose `Host` header names that same local address, and it rejects any cross-origin
write (a POST from a page with a different origin, or flagged `Sec-Fetch-Site: cross-site`). `--host 0.0.0.0`
exposes it to the rest of the network — there's no authentication in front of it, so don't do that on a
shared network.

> **Known issue.** Running with `--host 0.0.0.0` while `io.allowed_roots` is empty (the default) lets anyone
> on your network browse your home folder through the server-side file browser described below, with no
> authentication. Set `io.allowed_roots` to the specific folders you want exposed before binding to
> `0.0.0.0`, or don't use `--host 0.0.0.0` on a shared network. This is a known limitation, not something
> a future release has silently fixed — check the current docs before relying on it.

> **Plugins.** Installed [Workbench plugins](workbench-plugins.md) are full-trust Python running inside
> this server, and an exposed bind exposes their routes and Actions too. Install only plugins you trust,
> and turn one off with `[app] disabled_plugins = ["<id>"]`.

## Open or import a network

**Open / Import…** opens a wizard with four sources:

- **GMNS on this machine**: a server-side file browser. Entries are tagged (GMNS folder, `.zip`, `.duckdb`,
  `datapackage.json`); pick one and it opens in place. Nothing is uploaded.
- **GMNS at a URL** (`s3://`, `gs://`, `az://`, `https://`): **Check** says whether the URL is reachable, which
  credential source would be used (`env`, `keyring`, `netrc`, or `none`; never the secret), and which tables it found.
- **Build from OpenStreetMap** or **Build from Overture**: choose an area by drawing a rectangle (drag its corners
  to adjust), typing `W,S,E,N` or a point plus a buffer, or searching for a place and picking one of the
  outlines. You can instead pick a local file: a `.osm` XML file or an Overpass JSON export for OSM, or an
  Overture snapshot folder holding `segment.parquet` + `connector.parquet` (see `netstead.overture.layout`).

A build always writes to the output folder and format you choose (Parquet, CSV, DuckDB, or Zip) and then opens
the result from disk, so what you see is what was saved. It never overwrites an existing output; a leftover
hidden `.partial-*` staging folder from a crashed build is cleaned up automatically once it's more than 24
hours old. The output `name` must be a plain file name — no path separators, no trailing dot — and if it ends
in a format suffix (`.zip`, `.duckdb`, `.csv`, `.parquet`), that suffix must match the format you chose.

Before a build runs, the wizard shows an estimate of its time and size, from a quick count (Overpass
`out count`, or a DuckDB `COUNT(*)` over Overture) and a simple cost model. When the estimate is over
`app.approve_above_s` (90 s by default), or when the count fails, the button changes to **Run (~N min)** or
**Run anyway**, and the build only starts when you click it. The cost model's numbers are in
`netstead/workbench/data/build_cost.toml`; they are rough, so treat the estimate as a guide.

Opens and builds run as background jobs. The **Jobs** button in the header shows their stage and progress and
lets you cancel one; a cancel takes effect when the job reaches its next stage.

### Which folders the app can read and write

The file browser, opening a local path, and build input and output folders are all limited to
`io.allowed_roots`. When that list is empty (the default) it means your home folder. Paths given to
`netstead app` on the command line are always allowed for that session.

A remote source must look like `scheme://host/...` (for example `s3://bucket/net`) and must not contain any
`..` path segments. `file://` and `duckdb://` sources are treated as local paths in disguise and are checked
against `io.allowed_roots` like any other local path.

Credentials never go in the URL: a source with `user:password@` in it is refused. Put them where the
credential cascade looks instead (environment variables, the system keyring, or `~/.netrc`). A presigned URL's
query string (`?X-Amz-Signature=…`, an Azure SAS token) is accepted and kept in the session history, because
the replay script needs it to open the same object; it is stripped from error messages, job labels, the network
summary, and the **Recent** list. Treat a copied **Session as Python** script that opened a signed URL as
holding that (expiring) signature.

```toml
# ./netstead.toml
[io]
allowed_roots = ["~/networks", "/data/gmns"]

[app]
approve_above_s = 120
```

### The same thing from Python

A build is an ordinary action, so the history strip shows it as Python you can replay. The replayed call always
passes `approved=True`, because re-running a build you already approved counts as approval.

<!-- doctest: skip -->
```python
from netstead.workbench import BuildNetwork, Session

app = Session()
app.do(
    BuildNetwork(
        source="osm",
        area={"kind": "bbox", "bbox": (-78.91, 35.98, -78.88, 36.01)},
        output_dir="/home/me/networks",
        output_format="parquet",
        name="durham-core",
        approved=True,
    )
)
```

Without `approved=True`, a build over the threshold raises `netstead.workbench.ApprovalRequired`, and its
`.estimate` holds the estimate.

Two things to know before replaying a session's script:

- **Folders trusted on the command line are not in the history.** `netstead app /data/net` allows `/data/net` for
  that session only, without recording an action. A replay in a new `Session()` only gets your configured
  `io.allowed_roots`, so an open or build under such a folder fails with `PathNotAllowed`. Add the folder to
  `io.allowed_roots` (config file or `NETSTEAD_IO__ALLOWED_ROOTS`), or pass it as an override:
  `Session(overrides={"io.allowed_roots": ["/data/net"]})`.
- **Builds never overwrite.** Replaying a `BuildNetwork` whose output already exists fails with
  `… already exists`. Point the replay at a fresh `output_dir`, or move the earlier output away first.

## Settings

The workbench reads layered settings. From lowest to highest precedence:

1. built-in defaults
2. `~/.config/netstead/config.toml`
3. `./netstead.toml`
4. `NETSTEAD_<SECTION>__<FIELD>` environment variables
5. command-line flags

```toml
# ./netstead.toml
[app]
port = 8900

[viz]
basemap = "esri"

[select]
provider = "anthropic"
```

### The Settings dialog

**Settings…** in the header opens a form built from the same settings model. Each field has a badge showing where
its current value comes from: `default`, `user`, `project`, `env` or `session`.

- **Save to** chooses the layer a change is written to: **User** (your user file, the default), **This project**
  (`./netstead.toml`), or **This session only** (nothing is written to disk). Every change is a `SetSetting`
  action, so it appears in the history and replays from Python.
- **Reset** removes the value from the layer it comes from. Emptying a field does the same in the **Save to** layer.
  If the value comes from another layer, the dialog points you to **Reset** instead. A number the browser can't
  read, such as `1e`, is never saved: the dialog says so and keeps the saved value.
- A value set by a `NETSTEAD_*` environment variable outranks both files. The form says so when a saved
  value would not take effect.
- Keys read only at launch, such as the app server's port, are tagged **applies on next launch**. A session value
  has no effect on them.
- `io.allowed_roots` is read-only here. The folders the app may read and write can only be widened in a
  config file, an environment variable, or on the command line.
- A basemap change applies at once, without reloading the page.
- **Language models** is a section of the same dialog. **Models…** in the header opens it too.
- The dialog is keyboard-friendly: focus starts in it, Tab stays inside it, and Esc closes it and returns focus to
  where you were.

## Linked map and table

The map and the table are linked both ways. Three sets drive what you see:

- the **selection**, which is recorded: a natural-language select, or **Set as selection**;
- the **highlights**, links you click or shift-drag a box over in Highlight mode; these are not recorded;
- the **focus**, the one record you last clicked on the map or in the table. It is not recorded either.

Focus is a view. Clicking never changes the selection.

- **Focus.** Click a link or node on the map: its details show and, when the table shows that table, its row is
  outlined and scrolled into view (on whichever page it sits). Click a row: it is focused, and the map flies to it.
- **Show** in the table bar picks the rows:
  - **All rows**;
  - **Selection**: the selected links, or the rows of another table that point at them (for example their lanes);
  - **Highlighted**: the same for the highlighted links. A shift-drag box in Highlight mode switches to it.
  - **Related**: the records related to the highlighted links. A focused record only tints rows; it does not
    change this list. So clicking a row here focuses it without changing the list.
- **Foreign keys.** A key column such as `from_node_id` is a link. Click it to open the `node` table at that row; the
  map marks the node and flies to it.
- **Related records.** Rows a foreign key away from what is focused or highlighted are tinted in every table and on
  the map. Examples are the lanes of a link, a link's end nodes, and the links at a node.
  - The keys come from the GMNS spec, not a hard-coded list.
  - The table list shows how many records are related in each table (`N related`; a `+` means the count stopped
    early). Hover a tinted row to see which key relates it, for example `related via lane.link_id → link`.
  - **Expand a hop** follows keys one more step: from a node to its links, then to their lanes.
  - Related records are a read-only view. Nothing is recorded.

## Workspaces, panels and commands

- **Command palette.** Press **Ctrl+K** (**⌘K** on a Mac), or click **Commands…** in the header when it shows,
  to search every command: open a network, Settings, zoom, the view modes. With a record focused it also lists
  that record's commands ("For link 123"), and the selection's ("Selection (3 links)"). Arrow keys pick, Enter
  runs, Escape closes.
- **Right-click** a link or node on the map, or a table row, for its commands. With no command for it (as in a
  Workbench with no plugins), the map keeps its right-drag rotate and the row the browser's own menu.
- **Workspaces and the dock.** When a plugin adds a workspace, tabs for each appear in the header; each keeps the
  map / split / table view you left it in. The right drawer is a dock: when it holds more than one panel (core's
  **Details** plus a plugin's), tabs appear at its top.
- **Settings → Plugins** lists the installed [Workbench plugins](workbench-plugins.md): version, the plugin API
  each needs and whether it fits, and any error loading it. Its switch turns a plugin on or off; that applies the
  next time `netstead app` starts.

## Language models (natural-language selection)

The utterance box can be read by:

- an offline pattern parser (`stub`, the default — nothing leaves this machine);
- a local model through [Ollama](https://ollama.com), for example Qwen;
- Anthropic, OpenAI or Gemini, with your own API key.

To set up a local model (install Ollama, pull `qwen3:4b`, troubleshoot), see
[Run language models locally with Ollama](local-llm-ollama.md).

`select.provider` accepts `anthropic`, `openai`, `gemini`, `ollama` or `stub`. `claude` is still
accepted as an alias for `anthropic` (existing config files keep working).

Pick a provider and model with the picker next to the utterance box. The choice is
`SetSetting(scope="session")` — it applies to this session only; **Make default** saves the same
pair with `scope="user"`, and warns first if a project setting or a `NETSTEAD_SELECT__*` /
`NETSTEAD_SELECT__MODEL` environment variable would still override it. **Models…** opens the
Language models panel: set, replace, remove and test keys, point at another Ollama server, pull an
Ollama model, tune quality and context, and browse the model catalog.

The defaults are each provider's small, fast model — enough for one selection at a time — and are
maintained in `netstead/llm/models.toml`:

| Provider | Default model |
|---|---|
| `anthropic` | Haiku 4.5 (`claude-haiku-4-5-20251001`) |
| `openai` | small/fast tier |
| `gemini` | small/fast tier |
| `ollama` | `qwen3:4b`, or an installed tool-capable model such as `qwen2.5:7b` when `qwen3:4b` isn't installed |

Pick a larger model from the picker if parses go wrong.

### Keys are write-only

- A key is looked up in an environment variable first (`NETSTEAD_<PROVIDER>_API_KEY`, or the
  provider's own variable — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), then in the
  OS keyring (macOS Keychain, Windows Credential Manager, or Secret Service on Linux). There is no
  plaintext file fallback.
- Keys are never written to `config.toml`, shown in the browser, recorded in the session history,
  returned by any API response, or logged.
- Set a key with `uv run netstead llm set-key <provider>` (a hidden prompt, or `--stdin` to pipe one
  in — never a command-line argument, so it never reaches shell history or `ps`), or from the
  Workbench's Language models panel.
- **Keys are bound to their endpoint.** If you point a provider at a different `base_url` (for
  example an OpenAI-compatible server), it needs a new key entered for that endpoint; an existing
  key is never sent there.
- The Workbench's key routes (`/api/llm/keys/*`, `/api/llm/test`, `/api/llm/ollama/pull`) are
  loopback-only (refused unless the server is bound to `127.0.0.1`/`localhost`/`[::1]`) and every
  request must also carry an `X-Netstead-Secrets: 1` header, which a cross-origin page cannot add
  without a CORS preflight this server never approves. When the Workbench is bound elsewhere,
  manage keys from a terminal with `netstead llm set-key`/`remove-key` instead, and pull models with
  `netstead llm pull`.
- **Known limitation:** a key written by the CLI (or another process) while the Workbench is
  already running may take up to about 5 seconds to be seen by that running Workbench.

### Errors are explicit

A missing or rejected key, a rate limit or a timeout shows as an error. The Workbench never
quietly switches to another provider.

### What is sent, and quality settings

Every selection sends your utterance and the selection tool's schema (GMNS field names such as
`lanes`), never your network tables or files. The `[llm.quality]` settings add more, and the
privacy note next to the picker always lists exactly what the chosen provider receives. Every part
of the prompt that is network- or project-sourced data (not an instruction) is fenced in its own
tagged block (for example `<network_vocabulary>…</network_vocabulary>`) so a street name or
project note can never be mistaken for a new instruction to the model.

`"auto"` means on for a model running on this machine (Ollama, or any loopback `base_url`) and off
for a remote provider.

| Setting | Default | What it does |
|---|---|---|
| `assistant_context` | `true` | Sends the GMNS assistant guide that ships with netstead (data model, examples) |
| `project_context` | `"auto"` | Sends your project notes — see below — when allowed for the provider in use |
| `grounding` | `"auto"` | Sends the network's most common street names and route numbers, so the model spells them as the network does |
| `grounding_max_names` | `200` | Cap on those names |
| `few_shot` | `false` | Shows the model this session's earlier selections that resolved. Remembered only for this session, on this network — never persisted |
| `match_retry` | `"auto"` | If nothing matches, asks the model once more with the closest real names. `"auto"` is on for a local endpoint and off for a remote one |
| `match_candidates` | `5` | How many close names that retry offers |
| `max_repairs` | `1` | Re-prompts after an invalid reply |
| `temperature` | `0.0` | Sampling temperature; leave it blank for models that only accept their default (the field blank means the `0.0` default, not "unset") |

```toml
# ~/.config/netstead/config.toml: endpoints, choices and quality only, never keys
[select]
provider = "anthropic"
model = "claude-haiku-4-5-20251001"

[llm.ollama]
base_url = "http://localhost:11434"

[llm.quality]
grounding = "on"        # also send street names to remote providers
few_shot = true
```

### Project notes

A project's notes are discovered next to the active network's folder first, then in the project
directory — never the process's current working directory if neither of those applies. netstead
looks, in order:

1. A dedicated `NETSTEAD.md`, sent whole when found.
2. Otherwise, `AGENTS.md` — and failing that, `CLAUDE.md` — but **only** its `## netstead` section
   (any heading level `##`-`####`, matched case-insensitively, up to the next heading at the same
   or a shallower level). A file without that section is treated as if it were absent: the rest of
   an `AGENTS.md`/`CLAUDE.md` holds instructions for a coding agent, not notes for the network
   assistant, and is never read or sent.
3. If neither is found, no project notes are sent.

Every candidate must resolve inside `io.allowed_roots` (symlinks included), so a notes file can't
be used to exfiltrate an arbitrary file on disk.

A `NETSTEAD.md` might read:

```markdown
- "the Beltline" is I 440.
- SR-520 is the Evergreen Point Bridge.
- facility_type 7 means HOV lanes.
```

Or, inside an existing `AGENTS.md`:

```markdown
## netstead
- "the Beltline" is I 440.
- SR-520 is the Evergreen Point Bridge.
```

### From a terminal

Also the way to manage keys when the Workbench is bound to a non-local address:

```bash
uv run netstead llm status
uv run netstead llm set-key anthropic
uv run netstead llm test anthropic
uv run netstead llm models ollama
uv run netstead llm pull qwen3:4b
uv run netstead select "I-40 EB between South Miami Boulevard and Airport Boulevard" ./my-network --provider ollama --model qwen3:4b
```

## Every action is replayable

Everything you do in the browser is a typed action: open, select, style, navigate, change a setting.
The strip at the bottom shows the last action as Python. **Session as Python** shows the whole session as
a script you can copy. Networks added from Python via `Session.add_network` aren't recorded as actions
and so aren't replayed in that script.
