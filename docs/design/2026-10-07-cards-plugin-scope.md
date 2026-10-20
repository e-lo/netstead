# ProjectCard "project cards" plugin (`project_cards`): scope

Status: **implemented** (#228, #229, #237) · Date: 2026-10-07 · Owner: Elizabeth Sall

> **2026-10-19:** accepted. The pending decisions are decided (see "Decisions from the user", 7–11), the plugin lives
> in this monorepo as the first-party plugin `plugins/project-cards`, and the [implementation plan](2026-10-19-cards-plugin-plan.md) is
> written. Planning checked `projectcard` 0.3.3 and the Host API and found facts that change some assumptions below;
> they are marked **(2026-10-19)** and listed in the plan's "Facts found while planning".

- **Kind:** scope for the Workbench plugin "project cards" (id `project_cards`), built as the first-party plugin
  `plugins/project-cards` in this monorepo (distribution `netstead-project-cards`, import package
  `netstead_project_cards`; decision 9). It may later move into, or sit beside, the `projectcard` package.
- **Plan:** [Project cards plugin implementation plan](2026-10-19-cards-plugin-plan.md) (three PRs).
- **Parent:** [Workbench plugins design](2026-10-05-workbench-plugins-design.md) ("Target plugins: ProjectCard
  authoring"). That design moved the in-core `changes/` package (`NetworkChange`, `DraftCard`, `apply_card`,
  `Scenario`) out of netstead.
- **Origin:** the ProjectCard half of the first Workbench P2 draft (commit `eeaa01b` on `feat/workbench-p2`). P2
  was re-scoped to core validate + edit ([P2 plan](2026-10-07-workbench-p2-plan.md)); its card research is kept
  here so it is not lost.
- **Depends on:** plugins Parts 1 and 2 (the plugin core and the front-end slots), Workbench P2 (core edits, Host API
  1.2), P3a (saved selections, Host API 1.3), and Host API 1.5 (the plan's Task 1: `ActionError` / `NotSupportedYet`
  exported to plugins).

## What the plugin does

A user selects facilities, picks a change type, fills in the card's fields, previews the result on the map, and
commits the card to a YAML file (or a catalog). In plugin terms (design UX principle 2,
"selection → verb → form → preview → commit"):

1. **Select** with core's one shared selection (click, NL or query). The plugin reads `host.selection`.
2. **Verb:** a context command "New change ▸ {change type}", enabled when the selection is non-empty. Change types
   come from the projectcard schema.
3. **Form:** `wb.schemaForm`, prefilled from the selection's current values (`existing`).
4. **Preview:** `host.derive` a copy, apply the change to it with `host.mutate`, show it on the map.
5. **Commit:** apply to the base network, append to the draft card (plugin state, with a dirty badge), and write
   the card through `host.writable` when the user commits it. If `catalog.add_card` is registered, dispatch it too.

The plugin owns its nouns (design UX principle 1): *Draft card*, *Change*, exposed as `plugins.project_cards.*` state and
changed only through `project_cards.*` Actions (`project_cards.add_change`, `project_cards.undo_change`, `project_cards.commit`, `project_cards.import`,
...). Every network change it makes goes through core, so core's history, Edits tab, live warnings and dirty badge
see it.

## Requirements on the Host API (v1, as extended to 1.5)

| Host member | Provided by | What the plugin uses it for |
|---|---|---|
| `host.selection` with `projectcard: {picked, resolved, query}` | Part 1 (`selection`) + P2 Task 2 (facility form) | The card's `facility`: the query form for NL/query selections, the id form for clicks. `picked` says which. |
| `host.mutate(net_id, edits, note=...)` | Part 1 (passes `origin=<plugin id>`); P2 Task 5 makes that the pending edit's `source` | Applying a card change (lowered to corral edits) to the preview or the base network. |
| `host.derive(net_id, label=, note=)` | Part 1 | The preview network for a change before it is committed. |
| `host.writable(path)` (the design calls it `paths.writable`) | Part 1 | Writing card YAML inside `io.allowed_roots`. |
| `host.plan_update / plan_delete / plan_add` | P2 Task 7 (1.2) | Lowering a change onto core's edit planner, so a card cannot do what a cell edit may not (widen an integer column, add a column, delete a node a link uses). |
| `host.undo(net_id)` | P2 Task 7 (1.2) | Undoing the draft's last change; core refuses when the network's newest change is not the plugin's. |
| `host.edit_warnings(net_id)` | P2 Task 7 (1.2) | Showing open warnings before a commit, the same rule core applies before a save. |
| `host.dispatch`, `host.has_action`, `host.settings`, `host.submit_job` | Part 1 | `catalog.add_card`; `set_active_network` / `close_network` for the preview; plugin settings; long imports as jobs. |
| `host.saved_selections()`, `host.saved_selection(name)` | P3a (1.3) | Seeding a card's facility from a saved selection (`project_cards.add_change(saved_selection=...)`), with its stale-pin warnings. |
| `ActionError`, `NotSupportedYet` exported from `netstead.workbench.plugins` | Plan Task 1 (1.5) | Refusals the user sees (a plugin's other exceptions are recorded as internal errors). **(2026-10-19)** |

The plugin imports only `netstead.workbench.plugins` from netstead (`Host`, `BaseAction`, `ActionSpec`,
`WorkbenchPlugin`, `EditPlan`, `EditRefused`, `ActionError`, `NotSupportedYet`, `HOST_API`); an import-linter
contract enforces it. It needs no corral import: core's plans carry the edits. `projectcard`, `pyyaml` and `pydantic`
are ordinary dependencies of the package.

Not in the Host API **(2026-10-19)**: a lineage entry for a committed card (only `host.mutate` appends to the
lineage), and a read-path check (`project_cards.import` vets its file with `host.writable`). Both are follow-ups; v1 records
committed cards in plugin state.

## Decisions from the user

1. **`projectcard` is a required dependency** of the plugin package, not of netstead core. The plugin validates
   cards with the `projectcard` package's own schema and validator. (The first P2 draft vendored the v0.3.3 schema
   into core behind a `[projectcard]` extra, because the package pulls `ruff`, `toml`, `tabulate` and `jsonref` at
   runtime; that concern no longer applies to core.) Pinned `projectcard>=0.3.3,<0.4`. **(2026-10-19)** v0.3.3's
   `validate_card(card, parse_defaults=True)` fills schema defaults into the caller's dict, so a card without the
   required `ignore_missing` passes (and `read_card(path, validate=True)` validates that way). The plugin validates a
   deep copy with `parse_defaults=False`. The validator also does not check property values (see the schema facts).
2. **Use the schema's change-type names:** `roadway_property_change`, `roadway_deletion`, `roadway_addition`,
   `transit_property_change`, `transit_routing_change`, `transit_route_addition` (not the Workbench design's
   `add_transit_routes`), `transit_service_deletion`. The transit types are declared and answer "not yet
   supported".
4. **Values a card cannot hold.** A property's `set`/`existing` must be a number or a string.
   - Refuse an empty value and a list value, with a clear message: "a ProjectCard cannot set a property to empty".
   - Booleans become `1`/`0` (Wrangler's access flags use them anyway).
   - Clearing a value stays a core cell edit (it is not a card change).
5. **Provenance for query selections.** A change cannot carry extra fields (`additionalProperties: false`), so
   provenance goes in the card's top-level `notes`, one line per item, in the `key: value` style `map/edits`
   already uses:
   - `netstead.resolved[<i>]: model_link_id=[...]`: the ids change `i`'s query resolved to when it was made;
   - `netstead.note[<i>]: ...`: why change `i` was made (an issue code, a reason).
   - Import reads them back, so the plugin's own query-form cards re-import onto the ids they resolved to.
     Re-resolving a query on another network version is `apply_card` (later). Import refuses a query facility that
     has no provenance, and says so.

7. **`ignore_missing` defaults** (decided 2026-10-19). ProjectCard's `select_links` / `select_nodes` **require**
   `ignore_missing`: `true` makes Wrangler skip a selected id that is not in the network, `false` fails the card.
   - Click-picks (`host.selection["projectcard"]["picked"]` is true) and deletions write `ignore_missing: false`: a
     fix aimed at a specific link should fail loudly when that link is gone.
   - NL and query selections carry the intent's value, which defaults to `true` (Wrangler's default): a query naming
     a street should still apply where some of its segments differ.
   - Core writes the flag on every selector (P2 Task 2) with the intent's value (a pick's forms say `true`); the
     plugin writes `false` for picks and deletions.
   - **(2026-10-20)** A deletion made from a *query* selection is both, and follows the query: its links carry the
     query's flag, in the lowering (a resolved link that is gone is skipped) and in the card, so the card replays as
     it was applied. Typed and picked deletions, and every deletion's nodes, still write `false`.
8. **Importing the offline report's edit-log YAML** (decided 2026-10-19). netstead's offline HTML validation report
   (`render_validation_html`, from `netstead.map`) has a "Fix locally" editor; the session downloads as a YAML "edit
   log" (`netstead.map.edits.dump_edit_log`) that looks like a ProjectCard with `roadway_property_change` entries
   and that `netstead.map.edits.apply_edits` replays. It is **not** schema-valid: it puts
   `facility: {model_link_id: [...]}` at the top of the facility instead of under `links`, and gives each change a
   `notes` field, which the schema forbids.
   - The plugin's import reads it tolerantly: a top-level `model_link_id` / `model_node_id` becomes an id selection,
     and a per-change `notes` becomes that change's note.
   - The offline writer stays as it is (a separate surface with its own tests). The plan's last task writes a short
     follow-up note (in this scope's "Follow-ups") instead of filing an issue.
   - **(2026-10-20)** Done: both writers (`dump_edit_log` and the browser's `buildYaml`) now emit schema-valid cards
     (facility under `links` / `nodes` with `ignore_missing: false`, notes as `netstead.note[<i>]` lines). The
     tolerant read stays for logs written before.
9. **Repo and name** (decided 2026-10-19): inside this monorepo, as the first-party plugin `plugins/project-cards`.
   First-party plugins live in `plugins/<name>`, each its own uv workspace package (the root workspace lists
   `plugins/*` beside `packages/*`) that may import only `netstead.workbench.plugins`' public API. This one is
   "project cards": distribution `netstead-project-cards`, import package `netstead_project_cards`, plugin id
   `project_cards` (Actions `project_cards.*`, settings and state under `plugins.project_cards`), entry point
   `project_cards = "netstead_project_cards:plugin"` in `netstead.workbench.plugins`. An import-linter contract
   enforces the import rule; its other dependencies are `projectcard`, `pyyaml` and `pydantic`. It reuses what
   `projectcard` does correctly and keeps its own code only where `projectcard`'s is unusable (the plan's "Reuse of
   `projectcard`" table says which, and why). It has its own CI test run and coverage gate, consistent with corral's and netstead's.
10. **Saved selections seed a facility.** Since P3a, a named selection can be reused: `project_cards.add_change` (and
    `project_cards.preview`) take `saved_selection=<name>` and build the facility from `host.saved_selection(name)` (the
    same picked / query rule, with its stale-pin warnings passed on), and the panel's form route takes `saved=`.
11. **v1 scope.** All three roadway change types (property change with `set` and a `change` delta grouped by current
    value; deletion with `clean_nodes`; addition with derived access flags), draft card state, the Actions
    `project_cards.add_change`, `project_cards.undo_change`, `project_cards.commit`, `project_cards.import` (plus `project_cards.preview` /
    `project_cards.close_preview`), recorded and replayable, a preview on a derived network, commit to YAML via
    `host.writable` (dispatching `catalog.add_card` when it exists), and the UI. Every `project_cards.*` Action sets
    `mutates`, so the assistant drafts it for approval. Out of scope: `apply_card` re-resolution, scenarios, the
    catalog plugin, transit, `pycode`, managed lanes.

(Numbering follows the user's answers; answer 3 was about core and is recorded in the P2 plan.)

## ProjectCard schema facts

Read from network-wrangler/projectcard (latest release **v0.3.3**, 2024-10-16; Apache-2.0):
- `select_links` and `select_nodes` **require `ignore_missing`**.
- `roadway_property_change` has `additionalProperties: false`, so there are no per-change `notes`.
- A property set allows only `existing` / `set` / `change`, typed `number | string` (no null, no boolean), plus
  `existing_value_conflict` (`error` / `warn` / `skip`). **(2026-10-19)** The validator never applies that type:
  `property_changes` is an `anyOf` whose alternatives (`{"price": {...}}`, `{"lanes": {...}}`) are not JSON Schema
  keywords, so `set: null`, `set: true` and `set: [1]` validate. The plugin's own model enforces decision 4.
- **(2026-10-19)** `select_links` takes exactly one of `name` / `ref` / `osm_link_id` / `model_link_id` / `all`
  (`oneOf`); `modes` items are `drive` / `walk` / `bike` / `transit` / `any`; `model_link_id`, `model_node_id`, `A`
  and `B` are integers. A query naming both a street and a route number is not a valid card: the plugin checks each
  change with `projectcard` before applying it.
- **(2026-10-19)** `projectcard` 0.3.3's `utils.py` has an invalid escape sequence; under this repo's
  warnings-as-errors pytest policy, importing it is a `SyntaxError` unless the `SyntaxWarning` is filtered.
- `roadway_link` requires `A, B, name, model_link_id, roadway, lanes, walk_access, bike_access, drive_access`.
- The transit addition type is `transit_route_addition`.
- `roadway_managed_lanes` reuses `roadway_property_change`; managed lanes are out of scope for now.
- The plugin's tests should pin the change-type names against the installed package's schema, so a rename upstream
  fails a test rather than a user's card.

## The writers today produce invalid cards

- `select/emit.to_projectcard` omitted `ignore_missing` unless it was false. **Fixed in core** by P2 Task 2: every
  selector now carries it, and `host.selection` exposes both facility forms.
- `map/edits.dump_edit_log` writes the top-level `model_link_id` facility and per-change `notes` (decision 8). It
  **stays as it is**; the plugin reads it tolerantly.

## The change model

GMNS-native in the model, Wrangler-native only in the card file:

- `Selection`: `table` (`link` | `node`), and exactly one of `ids` (GMNS `link_id` / `node_id`, as a click picks
  them) or `query` (already in card form, as `to_projectcard(..., form="query")` emits it) plus `resolved` (the link
  ids it resolved to: provenance). A query applies to its `resolved` ids; re-resolving it is `apply_card` (later).
- `PropertyChange`: `existing`, exactly one of `set` / `change` (a numeric delta), `existing_value_conflict`.
  Values are `int | float | str`; booleans validate to `1`/`0`; empty and list values are refused (decision 4).
- `NetworkChange`, discriminated on `type`, with the schema's names (decision 2):
  - `RoadwayPropertyChange(facility, property_changes, note)`;
  - `RoadwayDeletion(links, nodes, clean_nodes, note)`;
  - `RoadwayAddition(links, nodes, note)`: GMNS records; every link needs `link_id, from_node_id, to_node_id`,
    every node `node_id, x_coord, y_coord`; nodes are added before links;
  - the four transit types, declared so the union and the NL tool vocabulary are stable, answering "not yet
    supported" until the transit component lands (P6).
- `DraftCard(project, tags, dependencies{prerequisites, corequisites, conflicts}, notes, changes)`, with
  `to_card()` / `to_yaml()` / `from_card()`; `read_card_file(path)` reads a card file or an offline edit log.

The first P2 draft has this model as complete, TDD-shaped code with tests (`netstead/changes/types.py`, `card.py`,
`log.py`: Tasks 5 and 8 of `docs/design/2026-10-07-workbench-p2-plan.md` at commit `eeaa01b`). It was planned, not
run. Start the plugin from it, with two changes: validate with the `projectcard` package instead of a vendored schema, and apply through core (next
section) instead of calling corral directly.

## Lowering a card change onto core edits

Every change becomes one `host.mutate` call (one pending edit, one undo step, atomic), except a deletion that also
deletes nodes (see the table). Plan with core so a card
obeys the same storage rules as a cell edit, then apply the plan's edits:

| Change | Plan with | Notes |
|---|---|---|
| `roadway_property_change` with `set` | `host.plan_update(net, table, ids, {column: value})` | Before planning, check `existing` against the current values (`host.network().links_df()`), following `existing_value_conflict`: `error` refuses, `warn` applies and reports, `skip` drops that property. |
| `roadway_property_change` with `change` | one `plan_update` per distinct current value | A delta on rows with different current values becomes several updates; all their edits go in one `mutate`. |
| `roadway_deletion` | `host.plan_delete(net, "link", ids)`, then `"node"` | Core cascades dependents through the spec's foreign keys and refuses a node a link uses. `clean_nodes` (delete end nodes nothing else uses) is the plugin's. **(2026-10-19)** A node plan reads the network as it is, so it refuses a node its own change's links still use: the links are deleted first (one `host.mutate`), then the nodes (a second), inside one Action handler. The change is atomic at the Action, and `project_cards.undo_change` undoes both pending edits. |
| `roadway_addition` | `host.plan_add(net, "node", rows)`, then `"link"` | Core warns (does not refuse) on a missing end node or a duplicate key. Wrangler fails such a card, so the plugin refuses before `mutate`: check the end nodes exist (or are added in the same change) and the keys are free. |
| transit | — | "Not yet supported" (P6). |

The edits from all plans of one change are concatenated into one `host.mutate(net_id, edits, note=...)`. The note
names the change (`"project_cards: roadway_property_change link 1: free_speed 40 → 30"`), so the lineage and core's Edits
tab say what happened. Field names map through the mapping file (below) only when reading or writing the card file.

Core's live checks run after every `mutate`, whatever made it. Before `project_cards.commit` writes a file, show
`host.edit_warnings()` and require confirmation while any remain: the same rule core applies before saving a copy.

## Undo within a draft card

- `project_cards.undo_change` reverses the draft's **last** change (LIFO, one per click) through `host.undo(net_id)`, then
  drops it from the draft. It is a recorded Action, so a replayed session reproduces the same network.
- Core and the plugin share one pending-edit list per network, and undo is strict LIFO across sources: core's Undo
  refuses when the newest change is the plugin's, and `host.undo` refuses when it is core's (or another plugin's),
  naming who made it. This keeps the draft card and the network in step.
- Committed cards are frozen. Their changes stay applied and the card id joins the network's lineage; reverting a
  committed card is a later feature (apply its inverse as a new card).
- A preview on a derived network is discarded by closing that network; nothing on the base needs undoing.

## The GMNS ↔ Wrangler mapping file

Maintained data, not code, shipped with the plugin (`mappings/gmns_to_wrangler.yaml`). When Wrangler renames a
field, change it here. The content drafted for the first P2 plan:

```yaml
# GMNS (netstead) <-> network_wrangler / ProjectCard names. This is maintained data, not code: when Wrangler
# renames a field, change it here. A column not listed keeps its GMNS name in cards (ProjectCard allows
# any property name in property_changes).
version: 1
selectors:            # the key a card's facility uses to pick records by id
  link: {link_id: model_link_id}
  node: {node_id: model_node_id}
fields:               # property names (property changes) and record fields (roadway additions)
  link:
    link_id: model_link_id
    from_node_id: A
    to_node_id: B
    facility_type: roadway
  node:
    node_id: model_node_id
    x_coord: X
    y_coord: Y
values:               # per card field: GMNS value -> card value (an unlisted value passes through)
  roadway: {}
derived:              # link fields Wrangler requires that GMNS has no column for; written on additions only
  walk_access: {column: allowed_uses, any_of: [walk]}
  bike_access: {column: allowed_uses, any_of: [bike]}
  drive_access: {column: allowed_uses, any_of: [auto, drive, truck]}
```

A small `FieldMap` reads it: `selector(table) -> (gmns key, card key)`, `to_card(table, column)`,
`from_card(table, name)`, `card_value` / `gmns_value`, and `derive(record)` for the access flags (a rule whose
source column is absent yields nothing: never an invented default; schema validation reports what is missing).
The first draft's Task 4 (same commit) has it as planned code with tests.

## Behaviours to pin in the plugin's tests

Carried over from the first P2 draft's tests:
- a pick writes Wrangler names (`model_link_id`, `roadway`), `existing_value_conflict`, and `ignore_missing`, and
  validates with `projectcard`;
- a query keeps its query and records `netstead.resolved[i]` in the notes; reading the card back gives the same
  selection;
- deletion (`clean_nodes`) and addition (with derived access flags) cards validate;
- YAML round-trips (`read_card(write(card)) == card`);
- the offline edit log imports (decision 8);
- a query without provenance is refused on import ("arrives with apply_card");
- transit cannot be written yet;
- an `existing` mismatch is a conflict and changes nothing; `warn` and `skip` behave as named;
- a delta groups rows by their current value;
- applying a card is all or nothing ("change 2 of 2: …"), and undo/commit follow the rules above;
- a card the plugin writes loads with `projectcard`'s own validator.

## Open questions for the plugin

- **Re-resolving queries (`apply_card`):** apply a card to another network version by re-running its query
  selections through core's `Select`. Out of scope for the first version.
- **Catalog interplay:** commit dispatches `catalog.add_card` when that Action exists (a soft dependency), else
  writes a file only.

## Out of scope

Scenarios and the card catalog (the separate `catalog` plugin), transit changes (P6), `pycode`, managed lanes, and
conflict or dependency resolution across cards.

## Follow-ups

From the implementation (2026-10; #228, #229, #237). Not filed as issues yet; each is small and independent.

**Host API (core)**

- **`Host.mutate` should wrap corral's `EditingError` in `ActionError`.** Today a plugin catches the corral error
  itself to give the user a clean refusal.
- **A rollback hook for plugins.** A `commit` nested in a parent Action that later fails can't be undone: core rolls
  back its own state, but not the file a plugin wrote or its in-memory draft. Candidate: an `on_rollback` callback a
  handler registers on the Host.
- **A stable edit id.** `host.mutate` (its result) and/or `host.pending_edits` should name each edit, so a plugin
  that undoes its own edits needn't read `NetworkHandle.lineage`.
- **A lineage entry on commit.** The scope wants a committed card's id in the network's lineage; `Host` has no
  method for it (only `host.mutate` appends). Candidate: `host.note_lineage(net_id, text)`.
- **A read-path check.** `project_cards.import` vets the file it reads with `host.writable`. Candidate:
  `host.readable(path)`.
- **Compact-mode assistant tools from plugins.** A plugin can't declare compact tools, so with a local model the
  card Actions are offered only when `assistant.compact_tools = "off"`.

**Other core and the plugin's reads**

- ~~**The offline report's edit-log writer is not schema-valid.**~~ **Done (2026-10-20).**
  `netstead.map.edits.dump_edit_log` and the browser's `buildYaml` write the facility under `links` / `nodes` with
  `ignore_missing: false` and each change's notes as a `netstead.note[<i>]` line of the card's `notes`;
  `load_edit_log` / `apply_edits` read both shapes, and the plugin's tolerant reader stays for old files only. Core
  does not depend on `projectcard`; its tests validate with it when installed.
- **`list_columns` performance.** The plugin's `routes.list_columns` checks every cell of each object column to find
  list-valued ones, on every form read; that is slow on large link tables. Sample, or ask core for the column types.
- **"Selection actions ▾" wording.** `packages/netstead/docs/cookbook/workbench-plugins.md` (the `"selection"`
  context row) and `static/js/commands.js` (the menu's comment) place it in the Details panel; it is in the
  **Selection** panel.

**The plugin**

- ~~**Honour a query selection's own `links.ignore_missing` when lowering.**~~ **Done (2026-10-20).**
  `RoadwayDeletion.ignore_missing` (its links') is set from the selection: `false` for a pick, the query's flag for a
  query. The lowering skips a missing link when it is `true`, the card writes it on `links`, and import reads it
  back (see decision 7).
- **`apply_card`:** re-resolve a card's query selections on another network version (out of scope for v1).
- **Scoped (time-of-day) values** in property changes.
- **TOML cards**, once `projectcard` fixes `_read_toml`.
- **Transit changes (P6).** The transit commands answer "not yet supported".
- **projectcard upstream.** Its `validate_card` fills defaults into the caller's dict; `property_changes` is never
  type-checked; `utils.py` has an invalid escape (`SyntaxWarning`). Report them, then drop the warning filter.
