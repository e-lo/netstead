---
title: Page Style Guide
audience: contributors
ai_index: false
summary: How every docs page in this site is structured so both humans and AI agents can find what they need fast.
---

# Page Style Guide

This is **not a public page** — it's the contract every other page in this site follows. Authors (humans + sub-agents writing markdown) read this first.

The site is built for two audiences in parallel:

* **Humans** scanning a page for the answer in 30 seconds.
* **AI agents** (LLM-based assistants, MCP clients, Claude Code Skills) parsing the page to extract context for a downstream task.

Optimising for one without the other produces docs that are either pretty-but-opaque (humans win, agents miss it) or comprehensive-but-tedious (agents win, humans bounce). The rules below thread the needle.

## Where a page lives

Each package has its own docs tree, built as its own site:

* `packages/corral/docs/` — generic Frictionless engine; audience = developers / researchers using any tabular package. Architecture and development docs for the monorepo as a whole live here too.
* `packages/netstead/docs/` — GMNS-specific toolkit; audience = transportation modelers / MPO + DOT staff / GTFS-interop researchers.
* `docs/design/` — design records (PRDs, plans). Not published.

A new page goes in `packages/corral/docs/` if it makes sense without knowing GMNS. In `packages/netstead/docs/` if it references `Network` / `link` / `node`. A design record goes in `docs/design/`.

## Frontmatter — required on every page

Every `.md` page starts with YAML frontmatter. Four required keys, two optional:

```yaml
---
title: Quickstart                      # required — H1-equivalent
audience: users | contributors | both  # required
kind: concept | howto | reference | tutorial  # required — picks the section template below
summary: One sentence (≤120 chars). What this page is for.
ai_index: true                         # optional, default true. Set false to omit from llms.txt nav.
stability: stable | beta | experimental  # optional — public API pages only
---
```

The `summary` is consumed by the `llms.txt` generator (see `docs/llms.txt` at site root) and by humans as the page subtitle. Make it answer "should I be on this page?" — not a teaser.

## Section headers — predictable shape per page kind

Four page kinds. Each has a fixed section order so an agent that learned the template once knows where to look on every page.

### `kind: concept` — "what is X"

```
## What it is
## Why we have it
## Mental model              ← diagrams / analogies / 3-bullet summary
## How it relates to ...     ← cross-link section (single canonical link per target)
## See also
```

### `kind: howto` — "how do I X"

```
## When to use this           ← 1-2 sentences. Triggering conditions.
## Quick example              ← prose-then-code; ≤20 lines of code
## Step-by-step               ← numbered list; one short paragraph each
## Common variations          ← accordions — one collapsible per variation
## Pitfalls                   ← optional — explicit gotchas
## See also
```

### `kind: reference` — "exact details on X"

```
## Summary                    ← 2-3 sentences. What this reference covers.
## API / fields / flags       ← the actual reference content (auto-gen welcome)
## Examples                   ← 1-2 runnable snippets per public symbol
## See also
```

### `kind: tutorial` — "walk through Y end-to-end"

```
## What you'll build
## Prerequisites              ← env + extras required (e.g. `netstead[clean]`)
## Steps                      ← numbered, each ends with what the user should see
## Next steps                 ← link out to relevant concept + howto pages
```

## Prose before code — every time

**Always** lead with one to three plain-English sentences before a code block. Tell the reader what they're about to see and why. Then the code. Then (if non-obvious) the expected output.

✅ Right:

> Load the bundled Leavenworth fixture through the DuckDB engine (via ibis). The result is a lazy `Network` — nothing materialises until you ask.
>
> ```python
> from netstead import Network
> from netstead.fixtures import leavenworth
> net = Network.from_source(leavenworth.csv_dir())
> ```
>
> Expected:
>
> ```text
> 0.97: 339 links, 121 nodes
> ```

❌ Wrong:

> ```python
> from netstead import Network
> net = Network.from_source(leavenworth.csv_dir())
> ```
>
> This loads the bundled Leavenworth fixture …

## Code blocks — language fence + runnable

* **Always tag the language**: ` ```python `, ` ```bash `, ` ```json `, ` ```yaml `, ` ```toml `, ` ```shell-session `. Plain ` ```text ` only for non-language output where syntax color is meaningless.
* **Runnable**: every Python block in `cookbook/`, `howto/`, `tutorial/`, and `reference/` examples must run as-is against the bundled Leavenworth fixture or a clearly-marked synthetic. A contract test (`packages/netstead/tests/test_documented_python_contract.py`) executes every ```python fence in both docs trees and the READMEs — column-0 and indented (inside accordions / tabs) alike — in a per-page namespace, and fails if one raises. When a block is followed by `Expected:` and a ```text / ```json fence, the block's printed output is compared with it: trailing whitespace is ignored, a documented line ending in `...` is a prefix match, and the documented block may stop early (show only the first lines). Write `...` where a value varies (timings, temp paths). Put `<!-- doctest: skip -->` on the line before a fence that needs credentials, network, Ollama, a running server, or files that don't exist; the marker may be indented and may carry a reason (`<!-- doctest: skip — needs S3 credentials -->`). Blockquoted fences and the migration guide are never executed. The CLI contract test (`test_documented_cli_contract.py`) checks every documented flag exists and, for a ```bash fence holding a single `netstead …` / `corral …` command with `--json` followed by `Expected:` and a ```json fence, runs the command and compares the parsed JSON — write `"..."` for a value that varies. There is no `>>>`-style doctest.
* **Copy button** is automatic via Material's `content.code.copy` feature. Don't worry about it — just write clean code.
* **Annotations** (numbered markers expanding to inline notes) are great for "why is this line written this way" without breaking the prose flow:
<!-- doctest: skip -->

  ```python
  net = Network.from_source(path)  # (1)!
  ```

  1. Tables stay lazy in DuckDB. Call `.to_pandas()` (or `.to_polars()`) on a table when downstream code needs a DataFrame.

## Common variations — use accordions, not tables

The previous version of this guide said "use a table". That was wrong — readers don't scan tables of 8 variations. Use **collapsible admonitions** (`???`), one per variation:

```markdown
???+ note "Default — load from a local CSV directory"
<!-- doctest: skip -->
    ```python
    net = Network.from_source("./my-network/")
    ```

??? note "Read from S3 with credentials"
<!-- doctest: skip -->
    ```python
    net = Network.from_source("s3://bucket/network/")
    ```

??? note "Materialise a table as a pandas DataFrame"
<!-- doctest: skip -->
    ```python
    links_df = net.links.to_pandas()
    ```
```

`???+` is expanded by default; `???` is collapsed. Lead with the most common variation expanded, the rest collapsed.

## Visual proof — screenshots of rendered output

For pages where the output *is* visual (a card-rendered `_repr_html_`, an HTML validation report, a map embed, a before/after geometry diff), include a screenshot. Don't just describe it.

```markdown
![Validation report card for the Leavenworth fixture](assets/screenshots/leavenworth-validation-report.png){ .screenshot }
*Validation report card. No ERRORs; one `fk.unverifiable` WARNING (`node.zone_id`, no `zone` table) and 16 INFO findings for optional tables the fixture doesn't ship.*
```

The `{ .screenshot }` class applies a subtle border + shadow. `mkdocs-glightbox` adds click-to-zoom automatically.

Place PNGs under `docs/assets/screenshots/`. Caption every screenshot.

## Card grids — landing pages + section overviews

Replace any "where to go next" markdown table on landing or section-overview pages with a card grid. More scannable, more visual, includes icons:

```markdown
<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } &nbsp;**Quickstart**

    ---

    Install, load the bundled Leavenworth fixture, and run validation in five minutes.

    [:octicons-arrow-right-24: Get started](intro/quickstart.md)

-   :material-book-open-page-variant:{ .lg .middle } &nbsp;**Cookbook**

    ---

    Task-oriented recipes for the common workflows — read from S3, scope, edit, serve.

    [:octicons-arrow-right-24: Browse recipes](cookbook/index.md)

</div>
```

Requires `attr_list` + `md_in_html` markdown extensions (already wired).

## Tooltips on abbreviations — site-wide glossary, zero per-page work

We maintain `docs/_abbreviations.md`. Every entry there becomes a hover tooltip on every page where the acronym appears:

```markdown
# in docs/_abbreviations.md
*[TOD]: Time Of Day — per-period overrides on GMNS link / lane / segment / movement attributes
*[MCP]: Model Context Protocol
```

Then any page that uses "TOD" or "MCP" gets the tooltip for free. Don't define acronyms in-page if they're in the abbreviations file; just write them and let the tooltip do the work.

Adding a new acronym: edit `docs/_abbreviations.md`, alphabetised, one line each.

## Definition lists for glossary-style entries

For glossary pages, use definition list syntax:

```markdown
GMNS
:   General Modeling Network Specification — the Zephyr Foundation's open standard for routable transportation networks in tabular form.

TOD
:   Time of Day — per-time-period overrides on link / lane / segment attributes, keyed by `time_set_definitions.timeday_id`.
```

Renders more semantically than bold-term + paragraph, parses better for agents (deflist is a known structure).

## Buttons for primary CTAs

For "install now" / "see full reference" / "explore X" moments on landing pages:

```markdown
[Install netstead](#install){ .md-button .md-button--primary }
[Browse the API](reference/api.md){ .md-button }
```

## Status chips per page

Material renders a colored badge in the page header when frontmatter carries:

```yaml
status: stable | beta | experimental
```

Use on public-API reference pages or experimental concept pages. Leave off otherwise.

## Footnotes for asides

Cleaner than parenthetical "(see X)":

```markdown
The default engine is ibis with the DuckDB backend.[^1]

[^1]: See [architecture §6.1](https://e-lo.github.io/netstead/corral/architecture/#61-engine-io) for the design rationale.
```

## Stable anchors

Anchors are the deep-link surface for `ai/api-index.json`, `llms-full.txt`, and external citations.

* Auto-generated heading slugs are fine **as long as you don't rename the heading**.
* For API reference pages: anchors must match the dotted symbol code (e.g. `#netstead.scope.from_nodes`). Use an explicit attr-list anchor if mkdocs's default slug doesn't match — wrap in a raw-Jinja block if you write that literal in a markdown page so the macros plugin doesn't try to parse it.

When you rename a heading, search the repo for the old anchor and update every link in one pass. No silent drift.

## Cross-links — one canonical target per concept

Every concept has **exactly one home page**. Other pages link to it; they don't re-explain it.

* Wrong: three pages all describe "what `Network.scope` is" in their own words.
* Right: one page (netstead/concepts/scope.md, when it exists) defines it; the other three link to it.

This is the most important rule for agent-efficient docs — an agent following links to gather context shouldn't see the same idea three different ways with three different example shapes.

## Voice

* Active, present tense: "The validator emits an Issue", not "An Issue will be emitted".
* Direct: "Call `.to_pandas()` to materialise a table", not "It is possible to materialise a table".
* No "we" / "you" tug-of-war: pick one per page kind. `concept` + `reference` → impersonal. `howto` + `tutorial` → "you".

## Length

* `concept` pages: 200–500 lines.
* `howto` pages: 100–250 lines.
* `reference` pages: as long as the surface; aim for one symbol per ~30 lines.
* `tutorial` pages: 300–800 lines.

If a page is over 800 lines, split it. If under 80, it probably belongs as a section on a larger page.

## AI artifacts that depend on this guide

Three machine-readable artifacts at `docs/` root are built at `mkdocs build` time and depend on the conventions above:

| Artifact | Built by | Depends on |
|---|---|---|
| `llms.txt` | `corral.docgen.llms.generate_llms_txt` | nav + page `summary` |
| `llms-full.txt` | `corral.docgen.llms.generate_llms_full_txt` | full page bodies (excluding `ai_index: false`) |
| `ai/api-index.json` | `corral.docgen.llms.generate_api_index_json` | public-API docstrings + reference page anchors |

When you change a page header or rename a section, those artifacts regenerate next build. CI fails if the generated artifacts can't parse a page.

## See also

* `packages/corral/docs/architecture.md` — single source of truth for the v1.0 design. Pages reference it; they don't duplicate it.
* `skills/README.md` — Claude Code Skills, copied into a project's `.claude/skills/`. Each skill links back into this docs site for its long-form reference.
