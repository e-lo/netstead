# Design records — index

This folder is the one place for netstead / corral planning: decisions, product requirements,
feature designs, implementation plans and scoping studies. It is internal (excluded from the
published sites by `exclude_docs` in the root `mkdocs.yml`) but lives in git, so every record is
reviewed in a PR like code.

**What lives where**

| Question | Source of truth |
|---|---|
| How is the software built *today*? | [`packages/corral/docs/architecture.md`](../../packages/corral/docs/architecture.md) |
| Why did we choose X over Y? | an **ADR** here |
| What should a feature do, and for whom? | a **PRD** or **Design** here |
| How do we build it, step by step? | a **Plan** here |
| Is X worth doing, and how big is it? | a **Scope** here |
| What exactly is left to do? | GitHub issues, linked from the record |
| What shipped in a release? | `packages/*/CHANGELOG.md` |

On conflict: architecture.md > accepted ADRs > PRDs / designs > plans > issue bodies > code comments.
When a decision changes, write a new ADR (or amend the old one's status), then update
architecture.md and this index in the same PR.

## Conventions

- **File name:** `YYYY-MM-DD-<slug>-<kind>.md`, e.g. `2026-10-01-geometry-encoding-adr.md`.
  Kinds: `adr`, `prd`, `design`, `plan`, `scope`. A few older records predate this rule; they
  keep their names because other records link to them.
- **Status line** right under the title:
  `Status: **<status>** (<PRs>) · Date: … · Owner: …`. Statuses:
  - `proposed` — open for review
  - `accepted` — agreed, not yet built
  - `implemented` — merged; cite the PRs
  - `superseded` — replaced; link the replacement
  - `abandoned` — not pursued
- **Mark, don't delete.** When a record is done or overtaken, update its status and add a short
  note at the top saying what changed. The reasoning is the value; don't rewrite history in the body.
- **Plans** close out by setting `implemented` in the PR that finishes them.
- Update the table below in the same PR as any status change.

## Records on `main`

### Decisions (ADRs)

| Record | Date | Status | Implemented in | Open follow-ups |
|---|---|---|---|---|
| [Engine strategy: DuckDB-only, stay on ibis](2026-10-01-engine-strategy-reevaluation.md) | 10-01 | implemented | #195, #198 | — |
| [Geometry encoding: WKB in memory, WKT CSV, GeoParquet](2026-10-01-geometry-encoding-adr.md) | 10-01 | implemented | #201, #202 | regenerate committed fixtures with GeoParquet `geo` metadata |
| [Foreign keys: report, don't enforce](2026-10-01-fk-constraints-adr.md) | 10-01 | accepted | — (records current behavior) | opt-in "trusted store" export with native constraints: backlog, no issue |

### Product requirements and feature designs

| Record | Kind | Date | Status | Implemented in | Notes |
|---|---|---|---|---|---|
| [NL selection](2026-09-23-nl-selection-design.md) | design | 09-23 | implemented | #194 | extended by NL providers |
| [Network viewer (`netstead.viz`)](2026-09-30-network-viewer-prd.md) | PRD | 09-30 | accepted; P1–P2 implemented | #194 | P3 (diff) and P4 (multimodal / GTFS) roll up under the Workbench design |
| [Workbench (`netstead app`)](2026-10-02-netstead-workbench-design.md) | design | 10-02 | accepted; P0, P1a, P1b implemented | #211, #212 | next: P2 validate + fix; its change-log part is amended by the plugins design (PR #217) |
| [NL providers (Anthropic / OpenAI / Gemini / Ollama)](2026-10-02-nl-providers-design.md) | design | 10-02 | implemented | #211 | `qwen3:4b` default not yet tested live |

### Implementation plans

| Plan | Parent | Status | Implemented in |
|---|---|---|---|
| [Viewer P1: core render path](2026-09-30-viz-p1-plan.md) | viewer PRD | implemented | #194 |
| [Viewer P2: inspect & style](2026-09-30-viz-p2-plan.md) | viewer PRD | implemented | #194 |
| [DuckDB-only engine consolidation](2026-10-01-engine-consolidation-plan.md) | engine ADR | implemented | #195 |
| [Workbench P0: foundations](2026-10-02-workbench-p0-plan.md) | Workbench design | implemented | #211 |
| [Workbench P1a: Open / Import wizard](2026-10-02-workbench-p1a-plan.md) | Workbench design | implemented | #211 |
| [Workbench P1b: inspect + settings](2026-10-05-workbench-p1b-plan.md) | Workbench design | implemented | #212 |
| [NL providers](2026-10-02-nl-providers-plan.md) | NL providers design | implemented | #211 |

### Scopes

| Scope | Date | Status | Implemented in | Open follow-ups |
|---|---|---|---|---|
| [Data-table exploration view](data-table-exploration-scope.md) | 09-30 | Phases 0–1 implemented | #194 | Phase 2+ (cross-linking, export) not scheduled |
| [Overture → GMNS pipeline](overture-to-gmns-pipeline-scope.md) | 09-30 | implemented | #196 | "Phase 2" attribute refinements listed in the scope |
| [Benchmarking suite](benchmarking-suite-scope.md) | 09-30 | implemented (DuckDB-only) | #197 | nightly perf regression job: [#100](https://github.com/e-lo/netstead/issues/100) |

## In flight (not on `main` yet)

These records exist on branches. Add them to the tables above when they merge.

| Record | Kind | Where | Status |
|---|---|---|---|
| `2026-10-05-workbench-plugins-design.md` — entry-point plugin API (`netstead.workbench.plugins`), core owns network nouns, ProjectCard editing moves to an external plugin repo | design | #222 (Part 1); Part 2 PR pending | accepted; Parts 1 and 2 implemented. **Amends the Workbench design:** the in-core `changes/` package it planned is superseded; core stays card-agnostic |
| `2026-10-05-workbench-plugins-p1-plan.md` — Part 1, Python plugin core | plan | #222 | implemented (#222) |
| `2026-10-08-workbench-plugins-p2-plan.md` — Part 2, front-end slots: the `activate(wb)` loader, workspace tabs, the dock (today's right drawer), commands (palette, map feature, table row, selection), the map layer registry, `wb.schemaForm`, Settings → Plugins | plan | branch `feat/workbench-plugins-p2` | implemented (PR pending); its 10 open questions decided 2026-10-10. Builds on Part 1 (#222); P2's drawer tabs register into its dock |

## Gaps to close

- **No product-level PRD.** #98 was closed in favour of this index plus architecture.md §1. If a
  roadmap to GA and past it is wanted, add it here as `YYYY-MM-DD-netstead-prd.md`.
- **Workbench LAN exposure:** `netstead app --host 0.0.0.0` with an empty `io.allowed_roots` lets
  anyone on the network browse the home directory. Documented, not blocked; consider refusing a
  non-loopback host without explicit roots.
- **Quality-rule backlog** from pre-v1 issues: partially overlapping segments (#5) and unique
  natural-key combinations such as `link_id` + `time_day` (#7).
