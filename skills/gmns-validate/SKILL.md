---
name: gmns-validate
description: Interpret netstead validation reports and data-quality findings on a GMNS network. Use when the user has run netstead validate / quality and needs help making sense of the issues, mapping codes to fixes, and prioritizing what to address.
---

# gmns-validate

Use this skill when a user shows you output from `netstead validate` or
`netstead quality`, asks "what does this error mean?", or has a network
producing dozens of issues and wants to know which to fix first. This skill
covers both schema-level validation (does the data parse?) and
GMNS-specific quality heuristics (does the network make physical sense?).

For *generic* Frictionless validation on non-GMNS data, use the
`corral-validate` skill. For authoring fresh networks, use `gmns-author`.

## Workflow

1. **Always run with `--json`.** The text output is for humans skimming;
   `--json` gives you a structured array you can group, filter, and count:
   ```bash
   netstead validate ./network --json > report.json
   ```
2. **Read `summary` first.** The header tells you total error/warning counts
   and which tables had issues. If `errors > 0`, the network won't load
   cleanly — fix those before quality issues.
3. **Group by `code`, not by row.** One root cause often emits 100+ issues.
   ```bash
   jq '.issues | group_by(.code)
       | map({code: .[0].code, severity: .[0].severity, count: length})
       | sort_by(-.count)' report.json
   ```
4. **Triage by severity:**
   - `error` — schema, type, or referential integrity. Fix immediately.
   - `warning` — quality heuristic flagged something physically suspect.
     Surface to the user; let them decide.
   - `info` — advisory only.
5. **Map the code to a fix.** See the code reference below.
6. **Re-validate after each batch of fixes.** Don't trust that fixing one
   `fk.missing_target` doesn't break a different table's FK.

## Issue code reference

### Schema codes (from corral)

- **`schema.required`** — required field is null/missing. Open the row and
  check the source export.
- **`schema.type`** — value can't be parsed as the declared type. Usually
  empty strings where numbers are expected, or commas inside numeric cells.
- **`schema.enum`** — value not in allowed list (e.g. `facility_type =
  "freewy"`). Check spelling and schema vocabulary.
- **`fk.missing_target`** — foreign key references an id that doesn't
  exist. For GMNS, the most common case is `link.from_node_id` or
  `to_node_id` not in `node.node_id`. Usually means the node table was
  filtered without filtering links.

### GMNS quality codes (from netstead.quality)

- **`quality.disconnected_components`** — the network graph has more than
  one connected component. Either some links are missing, or the data
  legitimately covers two unrelated areas. Inspect via
  `Network.from_source(...).components()`.
- **`quality.lane_count_mismatch`** — `link.lanes` doesn't match the count
  of rows in `lane.csv` for that link. Either the lane table is incomplete
  or `link.lanes` is wrong.
- **`quality.high_speed_residential`** — `facility_type` is `residential`
  or `local` but `free_speed` is above `speed_limit_mph` (default 35, in
  the network's own speed unit). Almost always a misclassified
  facility type. Look at the link's surroundings — it's usually an arterial
  mistagged.
- **`quality.duplicate_near_nodes`** — two nodes within `epsilon_units`
  (default `1e-5`, in CRS units) of each other. Usually one intersection
  digitised twice; merge with `netstead.clean.merge_close_nodes`. Tune the
  threshold for projected networks (metres).
- **`quality.sharp_angle_bends`** — a link's inline geometry turns sharper
  than `min_angle_degrees` (default 30°) at an interior vertex. Often a
  stray vertex or a link that should be split at that point.
- **`quality.implausible_vc`** — `volume / capacity` above `vc_max`
  (default 1.5). Only runs when the link table has a volume column.
- **`quality.missing_critical_fields`** — `length`, `free_speed`,
  `capacity` or `lanes` is non-null on fewer than `coverage_min` (default
  90%) of links. INFO: backfill or document why it is sparse.
- **`quality.overlapping_links`** — two links' shapes share a stretch of
  road (exact shared segments only). Touching, crossing and two-way twins
  with the identical shape are not flagged. Usually one link drawn on top
  of part of another: delete one, or split so each stretch is drawn once.
- **`quality.non_unique_combination`** — rows repeat a combination that
  should be unique, such as two `link_tod` rows for the same `link_id` and
  `time_day`, or two lanes with the same `lane_num` on one link. The
  issue's `extra["rows"]` lists the clashing rows; merge them or fix the
  key of the one that belongs elsewhere.

## Example: walking through a `quality.high_speed_residential` issue

The user's report includes:

```json
{
  "table": "link",
  "code": "quality.high_speed_residential",
  "severity": "warning",
  "row": 4127,
  "field": "free_speed",
  "message": "free_speed=55.0 on facility_type='residential'"
}
```

Your response should:

1. Pull the row to confirm:
   ```python
   from netstead import Network
   net = Network.from_source("./network")
   link = net.links.filter(link_id=...).to_pandas().iloc[0]
   print(link[["link_id", "from_node_id", "to_node_id",
               "facility_type", "free_speed", "lanes"]])
   ```
2. Inspect the surrounding network. A 55 mph "residential" link with 4
   lanes is almost certainly a minor arterial. Suggest:
   - Re-classify as `facility_type = "minor_arterial"` (preferred), or
   - Lower `free_speed` if classification is correct.
3. Check whether the issue is systemic — if 200 links are flagged, the
   upstream export is mislabeling a whole road class:
   ```bash
   jq '[.issues[] | select(.code == "quality.high_speed_residential")] | length' report.json
   ```
4. Don't auto-fix. Quality warnings encode judgment; surface options and let
   the user pick.

## Example: triaging a fresh report

```bash
netstead validate ./leavenworth --json > report.json

# Headline
jq '.summary' report.json
# {"errors": 0, "warnings": 12, "tables": 9}

# Group warnings by code
jq '.issues | group_by(.code)
    | map({code: .[0].code, count: length})
    | sort_by(-.count)' report.json
# [
#   {"code": "quality.stranded_node", "count": 7},
#   {"code": "quality.high_speed_residential", "count": 3},
#   {"code": "quality.duplicate_link", "count": 2}
# ]
```

Prioritize: 7 stranded nodes are likely safe to drop with `gmns-clean`;
3 high-speed residentials need human review; 2 duplicate links need
confirmation that they're not parallel facilities.

## See also

- `gmns-author` — fix issues by editing the source tables
- `gmns-clean` — drop stranded nodes / merge duplicates safely with rollback
- `corral-validate` — non-GMNS Frictionless validation
- `packages/netstead/netstead/quality/` — source of every `quality.*` code
