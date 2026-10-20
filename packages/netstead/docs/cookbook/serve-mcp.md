---
title: Wire the MCP server to Claude Code / Claude Desktop
audience: users
kind: howto
summary: Run netstead as an MCP server over stdio so an AI agent can call its tools — install, configure, list of bundled tools, sample prompts.
---

# Wire the MCP server to Claude Code / Claude Desktop

## When to use this

You want an AI agent (Claude Desktop, Claude Code, any MCP-aware host) to call `netstead` directly via tool calls — describe a package, run validation, scope a subgraph, run the quality pack — instead of you copying CLI output into the chat. The MCP server speaks the Model Context Protocol so the agent treats `netstead` as a first-class tool surface.

## Quick example

Add this entry to your MCP host config, restart the host, and ask the agent "describe the package at /tmp/leavenworth/csv". The agent picks `describe_package`, calls it with `{"source": "/tmp/leavenworth/csv"}`, and surfaces the package name, engine, table count and each table's row count and columns back in chat:

```json
{
  "mcpServers": {
    "netstead": {
      "command": "netstead",
      "args": ["mcp", "serve"]
    }
  }
}
```

Once connected, Claude lists the seven netstead tools and calls them with the `source` path you name; the tool name, its
arguments and the JSON reply appear inline in the chat.

## Step-by-step

### 1. Install

The `[mcp]` extra brings in `mcp` (the official Python SDK, v1 API). Add `[graph]` too: `connected_components` and `quality_check` need scipy, and without it those two tools fail at call time:

```bash
pip install 'netstead[mcp,graph]'
```

### 2. Test the server runs

You don't normally launch the server yourself — the MCP host does it. But `--help` confirms the entry point resolves and the SDK is importable:

```bash
netstead mcp serve --help
```

Expected:

```text
Usage: netstead mcp serve [OPTIONS]

 Start the netstead MCP server on stdio (for Claude Desktop / Claude Code).
 ...
╭─ Options ─────────────────────────────────────────────────────────╮
│ --name          TEXT  MCP server display name. [default: netstead] │
│ --help  -h            Show this message and exit.                  │
╰───────────────────────────────────────────────────────────────────╯
```

### 3. Configure the MCP host

**Claude Desktop (macOS):** edit `~/Library/Application Support/Claude/claude_desktop_config.json`. On Linux it's `~/.config/Claude/claude_desktop_config.json`; on Windows, `%APPDATA%\Claude\claude_desktop_config.json`. Restart Claude Desktop after editing; the tools show up under a hammer-icon in the chat input:

```json
{
  "mcpServers": {
    "netstead": {
      "command": "netstead",
      "args": ["mcp", "serve"]
    }
  }
}
```

**Claude Code:** register the server from the terminal — everything after `--` is the command Claude Code launches:

```bash
claude mcp add netstead -- netstead mcp serve
```

`--scope project` writes it to a `.mcp.json` at the project root that teammates pick up on session start (the default scope is local to you); see `claude mcp add --help`. The equivalent hand-written `.mcp.json` is the same JSON as above. To use the version installed in a project venv, replace `netstead` with the absolute path to that venv's `netstead`.

### 4. The tools shipped today

The server exposes seven stateless tools. Every tool takes a `source` — any path or URL that `Network.from_source` accepts — and returns JSON; only `scope_from_nodes` takes anything more. Full input and output shapes are in the [MCP tools reference](../ai/mcp-tools.md).

| Name | Summary | Inputs | Output |
|---|---|---|---|
| `describe_package` | Generic package overview: name, engine, table count, per-table rows + columns. | `source` | `{source, name, engine, table_count, tables: [{name, rows, columns}]}` |
| `validate_package` | Generic Frictionless validation (no GMNS spec). For GMNS use `netstead validate --json`. | `source` | `ValidationReport.to_dict()` |
| `list_tables` | Sorted table names, no row counts. | `source` | `["lane", "link", ...]` |
| `describe_network` | GMNS-aware overview: spec version, link + node counts, table names. | `source` | `{source, name, spec_version, engine, links, nodes, table_count, tables}` |
| `quality_check` | The GMNS data-quality rule pack. Needs `[graph]`. | `source` | `ValidationReport.to_dict()` |
| `connected_components` | Count + sizes of weakly-connected components. Needs `[graph]`. | `source` | `{source, component_count, sizes}` |
| `scope_from_nodes` | Node-seeded scope; returns the resulting node + link id sets. | `source`, `node_ids: [int]`, `path_between: bool = true` | `{source, seed_node_ids, path_between, result_node_count, result_link_count, node_ids, link_ids}` |

### 5. Sample agent prompts

Each of these exercises a different tool — paste them into a chat with the server configured to see the agent pick the right call:

* **"What's in the GMNS network at `packages/netstead/netstead/fixtures/leavenworth/csv`?"** — agent calls `describe_network`, returns spec version `0.97`, 339 links, 121 nodes and the nine table names.
* **"Run the quality rules on that network and summarise the warnings by code."** — agent calls `quality_check`, groups the `issues` list by `code`, and reports the counts.
* **"Scope the network to the shortest paths between nodes 1, 25 and 50 and tell me how many links that is."** — agent calls `scope_from_nodes` with `node_ids=[1, 25, 50]` and `path_between=true` (the default). See the known issue on this tool in the [MCP tools reference](../ai/mcp-tools.md#scope_from_nodessource-node_ids-path_between).

## Common variations

???+ note "Default — stdio transport, system netstead"
    The simplest config: relies on `netstead` being on the MCP host's `PATH`.

    ```json
    {
      "mcpServers": {
        "netstead": { "command": "netstead", "args": ["mcp", "serve"] }
      }
    }
    ```

??? note "Rename the server in the host UI"
    `--name` becomes the label under the tool list in Claude.

    ```json
    "args": ["mcp", "serve", "--name", "gmns-leavenworth"]
    ```

??? note "Run inside a project venv"
    Use the absolute path to that venv's `netstead` binary; avoids version skew with the system install.

    ```json
    {
      "mcpServers": {
        "netstead": {
          "command": "/path/to/.venv/bin/netstead",
          "args": ["mcp", "serve"]
        }
      }
    }
    ```

??? note "Pin the server to a working directory"
    Adds `cwd` so all relative-path tool calls resolve from a known root.

    ```json
    {
      "mcpServers": {
        "netstead": {
          "command": "netstead",
          "args": ["mcp", "serve"],
          "cwd": "/abs/path/to/data"
        }
      }
    }
    ```

??? note "Pass env vars (cloud creds)"
    Use `env` to inject credentials into the subprocess.

    ```json
    {
      "mcpServers": {
        "netstead": {
          "command": "netstead",
          "args": ["mcp", "serve"],
          "env": { "AWS_PROFILE": "ds-readonly" }
        }
      }
    }
    ```

## Pitfalls

* **Stdio transport only today.** The HTTP MCP transport is a follow-up — track the [open issues](https://github.com/e-lo/netstead/issues?q=mcp) for progress. For now, every agent that wants to call netstead needs to launch its own subprocess.
* **Tools are stateless.** There's no persistent `edit_session` tool yet — every call is a one-shot load + read. The agent can't mutate a network and inspect intermediate state across calls. See the [issue tracker](https://github.com/e-lo/netstead/issues?q=mcp) for the design discussion.
* **JSON-only return values.** Tool outputs are summaries (counts, names, id lists, reports), never table rows. For the data itself, the agent should call the CLI or the Python API through a shell tool.
* **Config file path is case-sensitive on macOS.** `Claude` vs `claude` matters. If the server doesn't show up after a restart, check `~/Library/Logs/Claude/mcp*.log`.

## See also

* [Architecture](https://e-lo.github.io/netstead/corral/architecture/) — MCP server position in the v1.0 surface layering.
* [Drive the CLI from an AI agent loop](https://e-lo.github.io/netstead/corral/ai/json-cli/) — when you want an agent to drive `netstead` via shell instead of MCP.
* [API reference](../reference/api.md) — the Python entry points the MCP tools wrap.
