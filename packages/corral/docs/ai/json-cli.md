---
title: Drive the CLI from an AI agent loop
audience: users
kind: howto
summary: Every netstead command emits one parseable JSON document on stdout — wire it into a tool-call loop with --json and CORRAL_AUTO_APPROVE.
---

# Drive the CLI from an AI agent loop

## When to use this

You're building an agent loop (Claude Code, a LangChain tool, a one-off shell harness) that needs to call `netstead` and parse the result. You don't want to spin up MCP for a stateless one-shot call.

## Quick example

Wrap `netstead validate --json` as a Python tool the LLM can invoke. The function parses stdout, summarizes the report, and caps the issue list to keep agent context cost predictable:
<!-- doctest: skip -->

```python
import json, subprocess

def validate(source: str) -> dict:
    """Tool function an LLM agent can call."""
    result = subprocess.run(
        ["netstead", "validate", "--json", source],
        capture_output=True, text=True, check=False,
    )
    report = json.loads(result.stdout)   # {"header": "...", "issues": [...]}
    return {
        "ok": result.returncode == 0,
        "error_count": sum(1 for i in report["issues"] if i["severity"] == "error"),
        "header": report["header"],       # one-line label, e.g. "validation report: <source>"
        "issues": report["issues"][:5],   # cap context cost
    }

print(validate("packages/netstead/netstead/fixtures/leavenworth/csv"))
```

## Step-by-step

### 1. Every command supports `--json`

Add `--json` to any `netstead` (or `corral`) CLI command and the output becomes a single machine-readable JSON document on stdout — pipe into `jq`, save to a file, feed to a script or AI agent. Default output is human-readable rich panels:

```bash
netstead info     --json <source>
netstead validate --json <source>
netstead quality  --json <source>
netstead scope from-nodes      --json <source> 1 25 50
netstead clean simplify-geometry --json <source> --dry-run
netstead bench    --json <source>
```

`quality` and `scope` need the `netstead[graph]` extra (scipy); `clean` needs `netstead[clean]`.

The `--json` contract: exactly one parseable JSON document on stdout, no log prefix, no progress bars, no trailing whitespace. `json.loads(proc.stdout)` always works on a successful run.

### 2. Stderr stays separate

Rich output (panels, tables, progress, approval prompts) goes to stderr. With `--json` on stdout the parser stays clean even when an approval prompt fires on stderr — the agent loop can either suppress stderr or surface it as side-channel feedback:
<!-- doctest: skip -->

```python
result = subprocess.run(
    ["netstead", "validate", "--json", source],
    capture_output=True, text=True,
)
data = json.loads(result.stdout)        # always parseable
diagnostics = result.stderr             # human-readable, optional to display
```

### 3. Pre-approve gated operations

Operations the cost model estimates as long-running prompt for confirmation by default. In an agent loop, set the env var once instead of passing `--yes` per call. The env var is preferable for agents because it scopes to the process tree and survives `subprocess` calls from inside the loop. `clean` is a command group — name the op (`simplify-geometry`, `merge-close-nodes`, `remove-orphans`, `recompute-lengths`), and use `--dry-run` to preview without writing:
<!-- doctest: skip -->

```python
import os, subprocess
env = {**os.environ, "CORRAL_AUTO_APPROVE": "1"}
subprocess.run(
    ["netstead", "clean", "simplify-geometry", "--json", source, "--dry-run"],
    env=env, check=False,
)
```

`--yes` on the command line works too, on the commands that expose it (e.g. `convert`); the `clean` ops do not.

### 4. Schema stability

The JSON shapes are stable inside a major version. Three types your loop is most likely to parse:

| Type | Shape (abridged) | Returned by |
|---|---|---|
| `ValidationReport` | `{header, issues: [{severity, category, code, message, table, column, row, fix_hint, extra}]}` — `header` is a one-line string label; `severity` ∈ `error` / `warning` / `info`; `category` ∈ `schema` / `structural` / `foreign_key` / `sync_state` / `data_quality` | `validate`, `quality` |
| `EditResult` | `{edit: {op, table, payload}, diff, rollback_data, applied_at, session_id}` | `clean`, edit ops |
| Command summary | per-command `{source, ...}` dict, documented in API ref | `info`, `bench`, `scope` |

Full details: [API reference](https://e-lo.github.io/netstead/netstead/reference/api/).

### 5. Exit codes

Check `returncode` *and* parse the JSON in agent loops — `returncode != 0` plus an `issues` array tells the model what went wrong:

| Command | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `validate` | no ERROR-severity issues | ≥1 ERROR issue | CLI usage error |
| `quality` | always (issues are WARNING/INFO) | — | CLI usage error |
| `clean` / edit | success or pure dry-run | failed precondition | CLI usage error |
| others | success | unhandled error | CLI usage error |

## Common variations

???+ note "Default — `subprocess.run` + `json.loads`"
    The simplest pattern: one shell out, one JSON parse, hand the dict back to the model.
<!-- doctest: skip -->

    ```python
    import json, subprocess
    out = subprocess.check_output(["netstead", "info", "--json", src])
    report = json.loads(out)
    ```

??? note "Quick shell filter via `jq`"
    Useful inside ad-hoc agent shells where you want only ERROR issues.

    ```bash
    netstead validate --json src | jq '.issues[] | select(.severity=="error")'
    ```

??? note "Stateful flows (sessions, history)"
    The CLI is stateless. For multi-step agent flows that need to inspect intermediate state, use MCP instead.

    See [Wire the MCP server](https://e-lo.github.io/netstead/netstead/cookbook/serve-mcp/).

??? note "Streaming output"
    Not supported — commands emit one document on completion. For long-running operations, poll a sidecar log or use the HTTP server.

## Pitfalls

* **Don't parse stderr.** It's intentionally human-formatted (rich panels, color, prompts). Mixing it into your parser breaks on the next release.
* **Don't mix `--json` and non-`--json` calls in the same loop.** Pick one and stick to it — the model gets confused when half the tool outputs are JSON and half are panels.
* **Auto-approve makes destructive ops silent.** `CORRAL_AUTO_APPROVE=1` skips every confirmation including ones the user might *want* to be asked about. Scope the env var to the agent subprocess; don't export it shell-wide.

## See also

* [MCP tools reference](https://e-lo.github.io/netstead/netstead/ai/mcp-tools/) — richer, stateful surface for agents that can speak MCP.
* [AI surface](index.md) — how `--json` fits with `llms.txt`, `api-index.json`, and the Claude Code Skills.
