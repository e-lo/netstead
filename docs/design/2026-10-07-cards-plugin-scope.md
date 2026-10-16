# ProjectCard "cards" plugin: scope

Status: **proposed** · Date: 2026-10-07 · Owner: Elizabeth Sall

- **Kind:** scope, for an **external** plugin repo (working name `netstead-cards`, plugin id `cards`). It may later
  move into, or sit beside, the `projectcard` package.
- **Parent:** [Workbench plugins design](2026-10-05-workbench-plugins-design.md) ("Target plugins: ProjectCard
  authoring"). That design moved the in-core `changes/` package (`NetworkChange`, `DraftCard`, `apply_card`,
  `Scenario`) out of netstead.
- **Origin:** the ProjectCard half of the first Workbench P2 draft (commit `eeaa01b` on `feat/workbench-p2`). P2
  was re-scoped to core validate + edit ([P2 plan](2026-10-07-workbench-p2-plan.md)); its card research is kept
  here so it is not lost.
- **Depends on:** plugins Parts 1 and 2 (the plugin core and the front-end slots) and Workbench P2 (core edits, Host API 1.2).

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

The plugin owns its nouns (design UX principle 1): *Draft card*, *Change*, exposed as `plugins.cards.*` state and
changed only through `cards.*` Actions (`cards.add_change`, `cards.undo_change`, `cards.commit`, `cards.import`,
...). Every network change it makes goes through core, so core's history, Edits tab, live warnings and dirty badge
see it.

## Requirements on the Host API (v1, as extended to 1.2 by P2)

| Host member | Provided by | What the plugin uses it for |
|---|---|---|
| `host.selection` with `projectcard: {picked, resolved, query}` | Part 1 (`selection`) + P2 Task 2 (facility form) | The card's `facility`: the query form for NL/query selections, the id form for clicks. `picked` says which. |
| `host.mutate(net_id, edits, note=...)` | Part 1 (passes `origin=<plugin id>`); P2 Task 5 makes that the pending edit's `source` | Applying a card change (lowered to corral edits) to the preview or the base network. |
| `host.derive(net_id, label=, note=)` | Part 1 | The preview network for a change before it is committed. |
| `host.writable(path)` (the design calls it `paths.writable`) | Part 1 | Writing card YAML inside `io.allowed_roots`. |
| `host.plan_update / plan_delete / plan_add` | P2 Task 7 (1.2) | Lowering a change onto core's edit planner, so a card cannot do what a cell edit may not (widen an integer column, add a column, delete a node a link uses). |
| `host.undo(net_id)` | P2 Task 7 (1.2) | Undoing the draft's last change; core refuses when the network's newest change is not the plugin's. |
| `host.edit_warnings(net_id)` | P2 Task 7 (1.2) | Showing open warnings before a commit, the same rule core applies before a save. |
| `host.dispatch`, `host.has_action`, `host.settings`, `host.submit_job` | Part 1 | `catalog.add_card`; plugin settings; long imports as jobs. |

Plugins import only `Host`, `BaseAction`, `ActionSpec`, `WorkbenchPlugin`, `EditPlan` and `EditRefused` from
`netstead.workbench.plugins`. `corral` (for `Edit`) and `projectcard` are ordinary pip dependencies.

## Decisions from the user

1. **`projectcard` is a required dependency.** The plugin validates cards with the `projectcard` package's own
   schema and validator. This is fine because it lives outside netstead core, so core stays lean. (The first P2
   draft vendored the v0.3.3 schema into core behind a `[projectcard]` extra, because the package pulls `ruff`,
   `toml`, `tabulate` and `jsonref` at runtime; that concern no longer applies to core.) Use the package's
   documented loader and validator (`projectcard.read_card(path, validate=True)` in v0.3.3, or what its docs name),
   and pin a minimum version.
2. **Use the schema's change-type names:** `roadway_property_change`, `roadway_deletion`, `roadway_addition`,
   `transit_property_change`, `transit_routing_change`, `transit_route_addition` (not the Workbench design's
   `add_transit_routes`), `transit_service_deletion`.
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

(Numbering follows the user's answers; answer 3 was about core and is recorded in the P2 plan.)

## Pending with the user (recommendations only; not decided)

### 7. `ignore_missing` defaults — **pending**

ProjectCard's `select_links` / `select_nodes` **require** `ignore_missing`. It says what Wrangler does when a
selected id is not in the network: `true` skips it, `false` fails the card.

- **Recommended:**
  - click-picks (`host.selection["projectcard"]["picked"]` is true) and deletions write `ignore_missing: false`: a
    fix aimed at a specific link should fail loudly when that link is gone;
  - NL and query selections carry the intent's value, which defaults to `true` (Wrangler's default): a query
    naming a street should still apply where some of its segments differ.
- Core already writes the flag on every selector (P2 Task 2) with the intent's value; the plugin overrides it to
  `false` for picks and deletions.

### Importing the offline report's edit-log YAML — **pending**

*What it is.* netstead's offline HTML validation report (`render_validation_html`, from `netstead.map`) has a
"Fix locally" editor in the browser. Each fix is collected, and the session downloads as a YAML "edit log"
(`netstead.map.edits.dump_edit_log`). It looks like a ProjectCard with `roadway_property_change` entries, and
`netstead.map.edits.apply_edits` replays it in Python. It is **not** schema-valid: it puts
`facility: {model_link_id: [...]}` at the top of the facility instead of under `links`, and it gives each change a
`notes` field, which the schema forbids.

- **Recommended:** the plugin's import reads it tolerantly: a top-level `model_link_id` / `model_node_id` becomes an
  id selection, and a per-change `notes` becomes that change's note. Leave the offline writer as it is (it is a
  separate surface, with its own tests), and file a follow-up to make it schema-valid.

## ProjectCard schema facts

Read from network-wrangler/projectcard (latest release **v0.3.3**, 2024-10-16; Apache-2.0):
- `select_links` and `select_nodes` **require `ignore_missing`**.
- `roadway_property_change` has `additionalProperties: false`, so there are no per-change `notes`.
- A property set allows only `existing` / `set` / `change`, typed `number | string` (no null, no boolean), plus
  `existing_value_conflict` (`error` / `warn` / `skip`).
- `roadway_link` requires `A, B, name, model_link_id, roadway, lanes, walk_access, bike_access, drive_access`.
- The transit addition type is `transit_route_addition`.
- `roadway_managed_lanes` reuses `roadway_property_change`; managed lanes are out of scope for now.
- The plugin's tests should pin the change-type names against the installed package's schema, so a rename upstream
  fails a test rather than a user's card.

## The writers today produce invalid cards

- `select/emit.to_projectcard` omitted `ignore_missing` unless it was false. **Fixed in core** by P2 Task 2: every
  selector now carries it, and `host.selection` exposes both facility forms.
- `map/edits.dump_edit_log` writes the top-level `model_link_id` facility and per-change `notes` (above). It **stays
  as it is**; the plugin reads it tolerantly (pending decision).

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
  `to_card()` / `to_yaml()` / `from_card()`; `read_card(path)` reads a card file or (pending) an offline edit log.

The first P2 draft has this model as complete, TDD-shaped code with tests (`netstead/changes/types.py`, `card.py`,
`log.py`: Tasks 5 and 8 of `docs/design/2026-10-07-workbench-p2-plan.md` at commit `eeaa01b`). It was planned, not
run. Start the plugin from it, with two changes: validate with the `projectcard` package instead of a vendored schema, and apply through core (next
section) instead of calling corral directly.

## Lowering a card change onto core edits

Every change becomes one `host.mutate` call (one pending edit, one undo step, atomic). Plan with core so a card
obeys the same storage rules as a cell edit, then apply the plan's edits:

| Change | Plan with | Notes |
|---|---|---|
| `roadway_property_change` with `set` | `host.plan_update(net, table, ids, {column: value})` | Before planning, check `existing` against the current values (`host.network().links_df()`), following `existing_value_conflict`: `error` refuses, `warn` applies and reports, `skip` drops that property. |
| `roadway_property_change` with `change` | one `plan_update` per distinct current value | A delta on rows with different current values becomes several updates; all their edits go in one `mutate`. |
| `roadway_deletion` | `host.plan_delete(net, "link", ids)`, then `"node"` | Core cascades dependents through the spec's foreign keys and refuses a node a link uses. `clean_nodes` (delete end nodes nothing else uses) is the plugin's: compute them after the link plan, add a node plan. |
| `roadway_addition` | `host.plan_add(net, "node", rows)`, then `"link"` | Core warns (does not refuse) on a missing end node or a duplicate key. Wrangler fails such a card, so the plugin refuses before `mutate`: check the end nodes exist (or are added in the same change) and the keys are free. |
| transit | — | "Not yet supported" (P6). |

The edits from all plans of one change are concatenated into one `host.mutate(net_id, edits, note=...)`. The note
names the change (`"cards: roadway_property_change link 1: free_speed 40 → 30"`), so the lineage and core's Edits
tab say what happened. Field names map through the mapping file (below) only when reading or writing the card file.

Core's live checks run after every `mutate`, whatever made it. Before `cards.commit` writes a file, show
`host.edit_warnings()` and require confirmation while any remain: the same rule core applies before saving a copy.

## Undo within a draft card

- `cards.undo_change` reverses the draft's **last** change (LIFO, one per click) through `host.undo(net_id)`, then
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
- the offline edit log imports (pending decision);
- a query without provenance is refused on import ("arrives with apply_card");
- transit cannot be written yet;
- an `existing` mismatch is a conflict and changes nothing; `warn` and `skip` behave as named;
- a delta groups rows by their current value;
- applying a card is all or nothing ("change 2 of 2: …"), and undo/commit follow the rules above;
- a card the plugin writes loads with `projectcard`'s own validator.

## Open questions for the plugin (beyond the two pending above)

- **Repo and name:** a new `netstead-cards` repo, or a module inside `projectcard`? Recommended: a new repo now;
  revisit once the Host API is stable (it is provisional until netstead v1.0).
- **Re-resolving queries (`apply_card`):** apply a card to another network version by re-running its query
  selections through core's `Select`. Out of scope for the first version.
- **Catalog interplay:** commit dispatches `catalog.add_card` when that Action exists (a soft dependency), else
  writes a file only.

## Out of scope

Scenarios and the card catalog (the separate `catalog` plugin), transit changes (P6), `pycode`, managed lanes, and
conflict or dependency resolution across cards.
