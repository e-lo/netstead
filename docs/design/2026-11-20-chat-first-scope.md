# Chat-first Workbench: scope

Status: **proposed** · Date: 2026-11-20 · Owner: Elizabeth Sall

- **Kind:** scope. It changes the priorities of the [Workbench design](2026-10-02-netstead-workbench-design.md) and the
  [layout and editing design](2026-10-26-workbench-layout-and-editing-design.md). It reuses what exists rather than
  replacing it.
- **Goal:** chat is the primary way to work in the Workbench. It must be fast, work with local models, and spend few
  API tokens. Edits made in chat show on the map and are stored as standard data: GMNS, ProjectCards and scenarios.
  The Workbench raises likely problems itself, in both data encoding and data values.

## What already fits

The architecture needs no rewrite:

- **One Action bus.** UI, Python and the LLM change state only through `Session.dispatch`.
- **Model output is checked.** The model never supplies ids. Its tool calls are validated against the Action schemas, and the conversation carries only the values-free echo.
- **Selections resolve offline.** Grammar-first selection (`select.grammar_first`) and the offline resolver turn names (street, from, to) into ids without a model.
- **Edits become standard records.** Edits are `NetworkChange`s, staged, and turned into ProjectCards by the cards plugin.
- **Fixes and projects are distinguished.** The Fix / Project split (layout design 2g) decides whether a change goes into a card or the fix log.
- **An MCP server exists.** `netstead.mcp`, with a `state=` seam for a live session.

The changes are about **where the work happens** (deterministic code first, the model as a translator) and **who
starts the conversation** (the Workbench raises findings itself).

## 1. Layout: chat is the left panel, the rail stays

The reference is Pillar's layout: an icon rail on the far left, a chat panel next to it, and the map filling the rest.
The chat panel's empty state has a greeting that names the data, a row of templates, and the input box at the bottom.

- **The left panel shows chat by default.** The rail stays. Its entries (Changes, Validate, Cards, plugin panels) swap
  the panel's content, and a chat entry at the top of the rail brings chat back.
- **The chat input stays pinned** to the bottom of the left panel in every pane, so typing is always one click away.
  It also has an attach button and a model and route chip (section 2).
- **Find merges into chat.** A search is a chat message. The Find rail entry is removed.
- **Empty state:** "Ask about or edit ‹network› · 12,340 links · 8,102 nodes", the open findings summary (section 4),
  and **templates**. A template is a starter prompt or a guided workflow, for example: Check network health, Make a
  road diet, Build a network for an area, Make a 2030 scenario. Plugins can add templates.
- **Map and Details are unchanged:** the map fills the right side, and Details opens as the right panel when
  something is selected.
- **Chat messages carry receipts.** Each staged change appears as a compact card in the transcript, with a link to
  show it on the map and Undo. Findings appear as messages with buttons.

The [UI style guide](workbench-ui-style.md) gets these rules when this scope is accepted.

## 2. Model routing by task complexity, not by local vs cloud

Some larger local models handle more than the smallest cloud ones, so routing is by **complexity level**. Each level
has an ordered list of models, and the first one that is **available** gets the request. A model is available when
its endpoint answers, a key exists, and the cost guard allows the request.

| Level | What | Example | Default route |
|---|---|---|---|
| `grammar` | The command grammar and the verb lexicon read the request with no model | "widen Pine St from Central to Price to 2 lanes" | always on, no model |
| `intent` | One change or selection, filled into the intent schema (section 3) | "make Pine a bit bigger near the school" | a local model, then a small cloud model |
| `plan` | Several steps, conditions, "for each", or questions about the data | "every arterial without a signal at a collector: suggest control" | a capable model |
| `code` | `pycode:netstead` generation (section 6) or open analysis | "split every link longer than 2 km at its midpoint" | a capable model; local only if the user opts in |

```toml
[assistant.routing]
intent = ["ollama/qwen3:14b", "anthropic/claude-haiku-5-5"]
plan   = ["ollama/qwen3:32b", "anthropic/claude-sonnet-5-5"]
code   = ["anthropic/claude-sonnet-5-5"]
```

- **Picking the level** happens before any model call. If the grammar reads the request, it is `grammar`. One verb
  with one facility is `intent`. Several clauses, conditions or quantifiers make it `plan`. An unsupported change
  type, or an explicit "write code", makes it `code`.
- **Escalation:** a schema failure after the existing repair re-prompt moves to the next model in the list, then up
  one level. Every escalation shows in the chat ("Read with qwen3:14b → retried with Haiku").
- **The user can force a level** from the route chip, or with `/plan` and `/code`.
- **Cost guard:**
  - Before a remote call, netstead estimates its cost: prompt tokens counted locally, plus the output cap, times a
    pricing table kept in a data file. That data file is updated alongside the catalog.
  - `assistant.approve_over_usd` (default `1.00`) holds any single request above it. The chat then offers *Send
    (~$1.30) · Use ‹local model› · Cancel*.
  - `assistant.session_budget_usd` (optional) does the same for the session total.
  - A model with no known price always asks.
  - The existing usage meter (`usage_key`) records what was spent.

## 3. Intent schema and verb lexicon

**Intent schema.** The `intent` and `plan` levels fill **a trimmed ProjectCard change**: a facility selection in
query form (name, from, to, or an attribute filter) plus `property_changes`, or an addition or deletion. The words
you type, the staged change and the card are then the same object. The model never sees ids or the street list,
because names resolve offline afterward. That keeps prompts small enough for local models. One small tool replaces
the full list of Action tools for edits. The read-only Actions (style, filter, navigate) keep their tools.

**Verb lexicon.** A YAML data file maps domain verbs to changes:

```yaml
widen:
  synonyms: [add lanes, expand]
  kind: project            # "actually has", "is really" → fix
  property: lanes
  direction: increase      # a precondition: the new value must be higher
  forms:
    - "to {n} lanes"            # set: n
    - "by {n} lanes"            # change: +n
    - "from {a} to {b} lanes"   # existing: a, set: b
```

- **Location vs. values in one phrase.** A number with a unit ("from 2 to 3 lanes") is read as values, and names
  ("from Central to Price") as a location, so "widen Pine St from 2 to 3 lanes" and "widen Pine St from Central to
  Price to 3 lanes" are both read without a question. "from {a} to {b}" becomes ProjectCard's `existing: a, set: b`,
  so the stated current value is checked when the change is applied.
- **Learning:**
  - When a word is unknown or ambiguous, the chat asks once with buttons: "'widen': did you mean add lanes?". The
    answer is saved as a lexicon entry.
  - When a model reads a phrase the lexicon didn't know and the user keeps the staged change, the chat offers
    "Remember 'beef up' = widen?".
  - Either way the grammar handles that phrase next time with no model. This is how the token cost falls over time.
- **Layers:** the lexicon shipped with netstead, then a project lexicon (`.netstead/lexicon.yaml`, which can be
  committed), then a user lexicon. Settings lists the learned entries so they can be edited or removed.

## 4. Proactive findings

- **When validation runs:** on load, then after every staged change on the affected ids only. The run is a
  change-set check (layout design 2d), so a problem a later change resolves is not reported.
- **Messages come from templates, with no model:** *"4 intersections have no control. [Show] [Suggest] [Ignore
  rule]"*. After an edit, only what the edit changed is reported: *"That widening created a lane drop at Price St."*
- **Suggest** comes from each rule's `suggest(row)` (layout design Phase 4, pulled forward). For example, two
  arterials meeting suggest a signal; two local streets suggest none or a stop. The suggested changes arrive staged,
  and each can be undone.
- **A plausibility rule pack** sits beside the existing encoding rules (schema, enums, foreign keys). It covers
  uncontrolled major intersections, lane drops, speed jumps along a named street, lanes vs. capacity, and
  connectivity traps (dead ends, one-way sinks). The quality backlog items #5 and #7 join it.
- **Against nagging:**
  - at most N findings messages on load, with the rest behind "N more";
  - snooze or ignore per rule, saved with the project;
  - the same finding is never announced twice.

## 5. Stage at once, confirm only when risky

- Chat edits are **staged immediately** and can be undone. The receipt in the chat reads *"Staged: lanes 1→2 on 6
  links, Pine St Central→Price · Undo"*, and the map shows the change as an overlay.
- **A confirmation is asked only when:**
  - a name matches more than one place;
  - the edit deletes something;
  - it touches more than `assistant.confirm_over_links` links;
  - a lexicon precondition fails ("Pine St already has 3 lanes");
  - it is `code`-level.
- Saving and making a card stay the deliberate steps.
- **The original sentence goes into the card.** Each staged change keeps the sentence that produced it, and the card
  dialog puts it in `notes`. The selection is kept in query form so the card can be applied to other network versions.

## 6. Pycode for changes that don't fit

- **Written for netstead.** Code is written against a small, documented **netstead editing API**, not Wrangler's
  pandas objects, and the card is tagged `pycode:netstead`. Wrangler cannot run it, and the tag says so.
- **Declarative first.** Before using pycode, try the declarative change types, writing out values where that is
  needed:
  - "capacity = lanes × 1800" becomes property changes with each link's computed value, with the formula in `notes`;
  - a link split becomes a deletion plus additions.
- **Code plus effect.** When pycode is written, netstead runs it once on a derived copy and records two things in the
  change: the code, and the effect (the records changed, with before and after values).
  - netstead applies the recorded effect, with no code execution, and checks it like any other change.
  - The code is the recipe for applying the change to a different network version.
- **Trust:**
  - Running code from an imported or registry card always needs an explicit Run.
  - Generated code is shown in the chat with its result on the derived copy (records changed, findings added or
    resolved) before it is staged.
  - Python cannot be sandboxed reliably, so the docs say plainly that running pycode means trusting its author.
- The cards plugin drops `pycode` from its unsupported list for the `pycode:netstead` form only.

## 7. Scenarios before Compare

- A minimal `apply_card` and `Scenario(base, cards[])`, written as Wrangler-compatible YAML, move ahead of Compare (P5)
  and transit (P6).
- Chat can name scenarios: "start a 2030 build from base with the Pine and Main cards".
- Compare then mostly becomes "base vs. scenario".

## 8. MCP on the live session

- The stateless MCP server gains a live-session mode through its `state=` seam. The Workbench's Actions and the
  `propose_change` tool are served over MCP against the open session, and the map follows over SSE.
- **Why:** Claude Desktop, Claude Code or any other MCP client can then drive the Workbench on the user's own plan,
  with no API tokens paid for through netstead.
- The in-app chat stays the path for local models.

## 9. Utterance eval

- **What:** a set of about 100 utterances, each with its expected intent and change, kept in `benchmarks/`.
- **What it measures:** accuracy, latency and tokens for the grammar, for several local sizes (4B, 14B, 32B) and for
  cloud models.
- **What it is for:** it sets the routing defaults in section 2 and shows how much really needs pycode. It runs
  before any routing default ships, and when the lexicon changes.

## Speed targets

| Path | Target |
|---|---|
| Grammar and lexicon to staged change and map overlay | under 100 ms |
| Incremental findings after a staged change | under 300 ms on a 50k-link network |
| A local model at `intent` level | under 2 s (model pre-warmed at app start with `keep_alive`) |
| First streamed token from a cloud model | under 1.5 s (static guide prompt-cached) |

## Re-sequencing

Alpha release stays first. After it:

1. **Utterance eval, intent schema, command grammar and verb lexicon** (sections 3 and 9).
2. **Chat layout** (section 1).
3. **Checks with `suggest()` and proactive findings** (section 4): layout design Phase 4, pulled forward and widened.
4. **Routing and the cost guard** (section 2).
5. **Cards from changes** (layout design Phase 3), which chat can also reach, and **Fix / Project** (2g).
6. **Minimal scenarios** (section 7) and **MCP on the live session** (section 8).
7. **Pycode** (section 6).

Moved back: card registries (layout Phase 5), the P4 Python console (MCP covers most of driving the app from outside),
and the Compare UI until scenarios exist.

## Decisions (2026-11-20)

1. Chat is the primary left panel, as in Pillar; the rail stays and swaps the panel's content.
2. Routing is by complexity level, with per-level model lists picked by availability, not split by local vs. cloud.
3. A cost guard holds any request estimated above a set amount (default $1) until it is explicitly approved.
4. The verb lexicon is YAML. It learns from clarifying questions and from accepted model readings, and it reads
   value forms ("from 2 to 3 lanes") without asking.
5. Proactive findings, staging at once with confirmations only when risky, scenarios before Compare, and MCP on the
   live session: accepted as proposed.
6. Pycode is written against a netstead editing API and tagged `pycode:netstead`; declarative change types are tried
   first.

## Open questions

- Should templates be plugin-provided only, or user-saveable from a chat ("save this as a template")?
- Exactly which names and methods make up the netstead editing API for pycode.
- Whether the pricing table ships in the package or is fetched, given that the app is offline-first.
