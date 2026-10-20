---
title: Install and troubleshoot
audience: users
kind: howto
summary: Python version, the four install tools, every optional extra, netstead doctor, and the first-run errors with their fixes — one page.
---

# Install and troubleshoot

## When to use this

You are installing `netstead` for the first time, choosing which extras you need, or something failed on the
first run (`zsh: no matches found`, an `ImportError` naming an extra, a port in use, an empty map). This page
owns all of that; the quickstarts link here instead of repeating it.

## Quick example

`netstead` needs Python 3.11 or newer. The bare package reads and validates networks; the extras add the
Workbench, editing, the graph engine and the rest. The one-liner below installs everything, which is the
right choice when you don't yet know what you need.

```bash
uv add 'netstead[all]'
```

Then confirm the install with the built-in check:

```bash
uv run netstead doctor
```

Every row should read `True` in the `ok` column. The next sections say what each row means and what to install
when you want less than `[all]`.

## Step-by-step

### 1. Check your Python

`netstead` requires Python 3.11+. `python --version` (or `uv python list`) tells you what you have; `uv` can
fetch a newer interpreter with `uv python install 3.12`. `netstead doctor` reports the version it is running
under as its first row.

### 2. Pick an install tool

Pick the tool you already use. These all produce the same install; `corral` (PyPI `dbcorral`) comes along
automatically, so you never install both.

=== "uv (recommended)"

    ```bash
    uv add netstead
    ```

    Fastest. Works inside a `uv`-managed project and writes to your `pyproject.toml` + `uv.lock`.

=== "uv pip"

    ```bash
    uv pip install netstead
    ```

    Drop-in `pip` replacement. Use this in a plain `venv` without a project file.

=== "pip"

    ```bash
    pip install netstead
    ```

    Classic. Works anywhere Python does.

=== "pipx"

    ```bash
    pipx install netstead
    ```

    Isolated env for the `netstead` CLI only — your project env stays untouched.

### 3. Choose extras

Extras are named in square brackets after the package name, comma-separated, and quoted (see
[zsh: quote the brackets](#zsh-quote-the-brackets) for why). The same syntax works with every tool above;
`uv` is shown.

```bash
uv add 'netstead[clean,server]'       # combine with commas
uv add 'netstead[all]'                # everything
```

| Extra | Pulls in | When you need it |
|---|---|---|
| `osm` | `requests` + `pyyaml` | `netstead build --source osm` — build a network from OpenStreetMap |
| `overture` | `pyyaml` | `netstead build --source overture` — build a network from Overture Maps (place-name areas also need `[osm]` for geocoding) |
| `clean` | `shapely` + `geopandas` + `pyproj` | Network editing (`netstead.clean`: simplify, merge, orphans, lengths) |
| `graph` | `scipy` + `numpy` + `pandas` | Scope from nodes, connected components, `netstead quality` / `run_quality` |
| `server` | `fastapi` + `uvicorn` + `pydantic-settings` + `httpx` | Self-hostable HTTP server and the browser Workbench (`netstead app`) |
| `mcp` | `mcp>=1.0,<2` | Run `netstead mcp serve` for Claude Desktop / Code |
| `nl` | `jsonschema` + `httpx` + `keyring` | Natural-language selection (`netstead select`) and the LLM providers (Anthropic / OpenAI / Gemini / Ollama) |
| `bench` | `psutil` | `netstead bench` / `bench-suite` process-memory metrics |
| `notebook` | `ipywidgets` + `anywidget` | Reserved for future notebook widgets *(the `_repr_html_` cards for `Network` / `ValidationReport` / `EditResult` work without this)* |
| `reports` | `openpyxl` + `jinja2` + `pyyaml` | The HTML map viewer (`netstead.map`) and XLSX findings export |
| `all` | every extra above | Single-flag full install |

Two combinations cover the quickstarts: `[server,graph,nl]` for the
[Workbench quickstart](quickstart-workbench.md) and `[clean,graph]` for the
[Python quickstart](quickstart.md).

### 4. Run `netstead doctor`

`doctor` checks the install from the inside: the interpreter, which optional extras import, the vendored
spec versions, and that the bundled fixture loads. It prints a table (or a JSON array with `--json`) and
exits non-zero if any check fails.

```bash
uv run netstead doctor
```

What the rows mean:

- `python_version` — the running interpreter and whether it meets `>=3.11`.
- `extra:<name>[<module>]` — whether that extra's key module imports (`importable`, or
  `not installed (ModuleNotFoundError); install with ...`). An optional extra that is absent is **not** a
  failure, so these rows are always `ok`; in `--json` the `installed` field carries the true/false answer.
  `doctor` probes `clean`, `server`, `mcp` and `notebook`; for `graph` or `nl`, try the import directly
  (`uv run python -c "import netstead.graph"`), which prints the message described under
  [ImportError: requires the [graph] extra](#importerror-requires-the-graph-or-clean-extra) if it's missing.
  The install hint names `uv sync --extra <name>`, which is the command inside a checkout of the `netstead`
  repository; in your own project use `uv add 'netstead[<name>]'`.
- `spec:0.95`, `spec:0.96`, `spec:0.97` — the vendored GMNS versions each load with their 25 resources.
- `fixture:leavenworth` — the bundled network loads (`loaded 339 links from csv fixture`). If this row fails,
  the install itself is broken: reinstall.
- `env:CORRAL_AUTO_APPROVE` — whether that variable is set. Unset means destructive operations ask first.

## Common variations

???+ note "Where settings and keys live"
    Settings are layered: built-in defaults, then `~/.config/netstead/config.toml`, then `./netstead.toml` in
    the project, then `NETSTEAD_<SECTION>__<FIELD>` environment variables, then command-line flags. Every
    section, key, type and default is in the [Settings reference](reference/settings.md).

    API keys for the language-model providers are not settings. They are read from an environment variable
    (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `NETSTEAD_<PROVIDER>_API_KEY`) or the OS
    keyring, set with `netstead llm set-key <provider>` or from the Workbench's Language models panel, and
    never written to a config file or shown in the browser. See
    [Keys are write-only](cookbook/workbench.md#keys-are-write-only).

??? note "Install from a checkout of the repository"
    Inside the `netstead` monorepo, `uv sync --all-extras` installs every package and extra in one go, and
    `uv run --all-extras netstead ...` runs the CLI from that environment. `uv sync --extra clean` adds one
    extra; it is the command `netstead doctor` names in its hints.

??? note "Expose the Workbench or the HTTP server to other machines"
    `netstead app` binds `127.0.0.1` by default. `--host 0.0.0.0` exposes it to the network with no
    authentication in front of it (and, with `io.allowed_roots` empty, exposes your home folder through the
    file browser), so don't do that on a shared network; see
    [Explore networks in the Netstead Workbench](cookbook/workbench.md#quick-start) for the details and
    [Self-host the HTTP server](cookbook/serve-http.md) for the bearer-token server built for that job.

## Pitfalls

### zsh: quote the brackets

On **zsh** (the default shell on macOS), `[` and `]` are glob characters. Running `uv add netstead[all]`
unquoted gives `zsh: no matches found: netstead[all]`. Always wrap the extras in quotes (`'netstead[all]'` or
`"netstead[all]"`), or run `setopt no_nomatch` once per session to disable the check. bash users don't hit
this.

### ImportError: requires the [graph] (or [clean]) extra

`netstead.graph` and `netstead.clean` guard their optional dependencies at import time, so the error names
the extra to install rather than a missing third-party module:

```text
ImportError: netstead.graph requires the [graph] extra: pip install 'netstead[graph]'
ImportError: netstead.clean requires the [clean] extra: pip install 'netstead[clean]'
```

Install the named extra (`uv add 'netstead[graph]'`, or `pip install 'netstead[graph]'`) into the **same**
environment you run from. The CLI prints the same message in red (`netstead.server requires the [server]
extra: ...`) and exits with code 1 when a command needs an extra you don't have: `netstead app` needs
`[server]`, `netstead quality` and `netstead scope` need `[graph]`, `netstead clean` needs `[clean]`,
`netstead mcp serve` needs `[mcp]`.

### `No such command 'app'`

Typer's `No such command` means the `netstead` on your `PATH` doesn't have that command at all, not that an
extra is missing (a missing extra gives the `requires the [...] extra` message above). Usually a different,
older install is first on the `PATH`: `which netstead` shows which one, and `uv run netstead app` runs the one
in your project environment. `netstead viz` and `netstead select-serve` are aliases of `netstead app`.

### Workbench: the port is already in use

`netstead app` listens on port 8850. If another Workbench (or anything else) holds it, uvicorn stops with
`error while attempting to bind on address ('127.0.0.1', 8850): address already in use`. Pick another port
and open that one instead:

```bash
uv run netstead app ./my-network --port 8900
```

The default lives in `[app] port` of your settings file if you want to change it for good.

### Workbench: nothing on the map

The header shows the link and node counts, but the map is blank or shows the wrong continent: the view hasn't
moved to the network yet. Press the map's **Zoom to full network** button (the second of the small buttons on
the map, or the same command in the palette, Ctrl+K / ⌘K). If the counts are missing too, the network didn't
open: check the terminal for the error, and that the path is a GMNS folder, `.zip`, `.duckdb` or
`datapackage.json`.

### Workbench: a folder is "outside the allowed folders"

Opening or saving under a folder outside your home directory is refused with
`... is outside the allowed folders (...); add a parent folder to io.allowed_roots`. Paths given on the
`netstead app` command line are allowed for that session; anything else must sit under `io.allowed_roots`
(your home folder when the list is empty). See
[Which folders the app can read and write](cookbook/workbench.md#which-folders-the-app-can-read-and-write).

## See also

* [Quickstart: the Workbench](quickstart-workbench.md) — ten minutes in the browser, no code.
* [Quickstart: Python](quickstart.md) — load, validate, quality-check and scope from Python.
* [Settings reference](reference/settings.md) — every section, key and default.
* [Explore networks in the Netstead Workbench](cookbook/workbench.md) — the Workbench in full, including the
  bind-host warning and the write-only key routes.
