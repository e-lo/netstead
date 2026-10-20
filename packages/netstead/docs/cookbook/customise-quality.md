---
title: Customise the data-quality rule pack
audience: users
kind: howto
summary: Override thresholds, disable rules, promote severity, and register your own rule via entry point.
---

# Customise the data-quality rule pack

## When to use this

GMNS defaults are designed for typical mid-sized urban networks. Override them when:

* Your network's speed limits, lane counts, or geometry tolerances differ from the defaults (a 30 mph residential cap, or a CRS in degrees instead of meters).
* You want to silence a rule that's noise on your data.
* You want to plug in a project-specific quality rule.

## Quick example

Raise the residential-speed threshold from the default 35 mph to 45 mph. `RuleConfig.thresholds` merges into the rule's defaults — you only specify the keys you want to change. Every residential link in Leavenworth is posted at 40 mph, so the default fires 271 warnings and 45 mph silences all of them:

```python
from netstead import Network
from netstead.fixtures import leavenworth
from corral.quality import run_quality, RuleConfig

net = Network.from_source(leavenworth.csv_dir())
default = run_quality(net)
relaxed = run_quality(
    net,
    config={"quality.high_speed_residential": RuleConfig(thresholds={"speed_limit_mph": 45.0})},
)
print(f"{len(default.issues)} issues at 35 mph, {len(relaxed.issues)} at 45 mph")
```

Expected:

```text
271 issues at 35 mph, 0 at 45 mph
```

![Quality report card for the Leavenworth fixture](../assets/screenshots/leavenworth-quality-report.png){ .screenshot }
*Quality report card at the default thresholds: 271 `quality.high_speed_residential` WARNINGs, one per residential link posted at 40 mph. Raising `speed_limit_mph` to 45 silences them all; lowering it to 30 changes nothing, because no residential link sits between 30 and 35 mph.*

## Step-by-step

### 1. List the rules currently registered

Inspect the registered rule pack to see codes, defaults, and severities. The GMNS pack ships nine rules out of the box:

```python
from corral.quality import list_rules, get_rule
from netstead.quality import register_all

register_all()                                   # populate the registry
for code in list_rules():
    rule = get_rule(code)
    print(f"{rule.code:42} {rule.severity.value:8} {rule.description[:50]}")
```

| Code | Threshold key | Default | Severity |
|---|---|---|---|
| `quality.high_speed_residential` | `speed_limit_mph` | `35` | WARNING |
| `quality.disconnected_components` | (no threshold) | — | INFO |
| `quality.lane_count_mismatch` | (no threshold) | — | WARNING |
| `quality.duplicate_near_nodes` | `epsilon_units` | `1e-5` | WARNING |
| `quality.sharp_angle_bends` | `min_angle_degrees` | `30` | WARNING |
| `quality.implausible_vc` | `vc_max` | `1.5` | WARNING |
| `quality.missing_critical_fields` | `coverage_min` | `0.90` | INFO |
| `quality.non_unique_combination` | `combinations` | see below | WARNING |
| `quality.overlapping_links` | `min_overlap_units` | `0` | WARNING |

Three of these are conditional: `quality.disconnected_components` builds the scipy graph index, so `run_quality` needs the `[graph]` extra; `quality.overlapping_links` needs shapely (the `[clean]` extra) and link shapes, inline or in the `geometry` table; `quality.sharp_angle_bends` only applies when the link table carries an inline `geometry` column (Leavenworth keeps geometry in a separate table, so the rule is skipped there).

`quality.non_unique_combination` catches rows that repeat a column combination GMNS expects to be unique even though the table's primary key is a surrogate id: two `link_tod` rows for the same `link_id` and `time_day` pass spec validation but contradict each other. It emits one issue per repeated combination, with the row positions in `extra["rows"]`. By default it checks `(<parent id>, time_day)` and `(<parent id>, timeday_id)` on every time-of-day table (`link_tod`, `lane_tod`, `segment_tod`, `segment_lane_tod`, `movement_tod`, `signal_timing_plan`), plus `(link_id, lane_num)` on `lane`, `(segment_id, lane_num)` on `segment_lane` and `(timing_plan_id, signal_phase_num)` on `signal_timing_phase` (read-only, as `netstead.quality.DEFAULT_UNIQUE_COMBINATIONS`). Setting `combinations` replaces that list, so name every combination you still want. A configured combination whose table or columns the network lacks is logged as a warning on the `netstead.quality.rules` logger (probably a typo). It is deliberately not an issue: `severity_override` applies to every issue a rule emits, so promoting this rule to ERROR in CI fails only on real duplicates, never on a network that lacks an optional table. A value that is not a mapping of table name to lists of column names raises `ValueError`, and the Workbench rejects it (and a negative `epsilon_units`) when the setting is saved:

```python
report = run_quality(
    net,
    config={
        "quality.non_unique_combination": RuleConfig(
            thresholds={"combinations": {"link_tod": [["link_id", "time_day"]], "link": [["osm_way_id", "from_node_id"]]}}
        )
    },
)
```

### 2. Override a threshold

`RuleConfig.thresholds` is merged into the rule's defaults — you only specify the keys you want to change. Override multiple rules in one call:

```python
report = run_quality(
    net,
    config={
        "quality.high_speed_residential": RuleConfig(thresholds={"speed_limit_mph": 25.0}),
        "quality.sharp_angle_bends": RuleConfig(thresholds={"min_angle_degrees": 45.0}),
    },
)
```

The Workbench applies the same per-rule configuration from its `validation.rules` setting (Settings → Validation, keyed by rule code) — see [Validate and fix in the Workbench](workbench.md#validate-and-fix).

### 3. Disable a rule entirely

A disabled rule produces no issues and isn't listed in the report:

```python
report = run_quality(
    net,
    config={"quality.missing_critical_fields": RuleConfig(enabled=False)},
)
```

### 4. Promote (or demote) severity

A common case: your CI pipeline should fail on lane-count mismatches even though the rule ships as WARNING. Use `severity_override` to promote, then check `report.issues` and exit non-zero accordingly:

```python
import netstead
from netstead.fixtures import leavenworth
from corral.quality import RuleConfig, run_quality
from corral.reports import Severity

net = netstead.read(leavenworth.csv_dir())
report = run_quality(
    net,
    config={"quality.lane_count_mismatch": RuleConfig(severity_override=Severity.ERROR)},
)
has_errors = any(i.severity is Severity.ERROR for i in report.issues)
# In a CI script: sys.exit(1 if has_errors else 0)
```

### 5. Write your own rule and register it

A rule is a plain class (no base class needed — `corral.quality.Rule` is a `Protocol`) with three attributes, `code`, `description` and `severity`, and two methods: `applies_to(package) -> bool`, a cheap predicate the runner calls first, and `run(package, report, config)`, which appends findings to `report` with `report.add_issue(...)`. Accept the third `config` argument to read `RuleConfig.thresholds`; a rule with no thresholds may define `run(self, package, report)` and the runner passes two arguments. Don't apply `severity_override` yourself — `run_quality` rewrites the severity of every issue your rule emitted after it returns.

Write the predicate against the table's ibis expression (`package.tables["link"].expr`) so DuckDB filters the rows and only the offending ones reach Python; on a 200k-link network that is the difference between materialising every row and fetching a handful. Number the rows with `ibis.row_number()` *before* filtering so `Issue.row` is the row's position in the whole table. This rule flags links longer than a configurable maximum (Leavenworth's `length` column is in metres):

```python
import ibis
from corral.quality import RuleConfig, register_rule, run_quality
from corral.reports import Category, Issue, Severity


class LongLinkRule:
    code = "quality.long_link"
    description = "Link longer than the configured maximum length."
    severity = Severity.WARNING

    def applies_to(self, package) -> bool:
        link = package.tables.get("link")
        return link is not None and {"link_id", "length"} <= set(link.columns())

    def run(self, package, report, config: RuleConfig | None = None) -> None:
        thresholds = config.thresholds if config else {}
        max_length = float(thresholds.get("max_length", 1000.0))
        link = package.tables["link"].expr
        link = link.mutate(row=ibis.row_number())
        offending = (
            link.filter(link.length.notnull(), link.length > max_length)
            .select("row", "link_id", "length")
            .order_by("row")
        )
        for row in offending.to_pyarrow().to_pylist():
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=f"link {row['link_id']} is {row['length']:.0f} long (> {max_length:.0f})",
                    table="link",
                    column="length",
                    row=row["row"],
                    fix_hint="Split the link, or check its length.",
                )
            )


register_rule(LongLinkRule())
report = run_quality(net, config={"quality.long_link": RuleConfig(thresholds={"max_length": 500.0})})
print(sum(1 for i in report.issues if i.code == "quality.long_link"), "links over 500 m")
```

Expected:

```text
4 links over 500 m
```

To ship the rule with a package, expose a **zero-argument factory that returns `list[Rule]`** and declare it under the `corral.quality.rules` entry point in your `pyproject.toml`. `run_quality` loads every entry point on its first call and registers each returned rule — this is exactly how netstead registers its own pack (`netstead.quality:register_all`):

<!-- doctest: skip -->
```python
# myproject/rules.py
def register_rules() -> list:
    return [LongLinkRule()]
```

```toml
[project.entry-points."corral.quality.rules"]
myproject = "myproject.rules:register_rules"
```

## Common variations

???+ note "Default — override one threshold for one run"
    Most common pattern: tighten or loosen a single threshold without touching anything else.

    ```python
    report = run_quality(
        net,
        config={"quality.high_speed_residential": RuleConfig(thresholds={"speed_limit_mph": 30.0})},
    )
    ```

??? note "Silence a noisy rule entirely"
    Disable it via `RuleConfig(enabled=False)`; the rule contributes no issues and doesn't appear in the report.

    ```python
    report = run_quality(net, config={"quality.missing_critical_fields": RuleConfig(enabled=False)})
    ```

??? note "Promote a WARNING to ERROR for CI"
    Use `severity_override`; then exit non-zero if any ERROR issue appears.

    ```python
    report = run_quality(
        net,
        config={"quality.lane_count_mismatch": RuleConfig(severity_override=Severity.ERROR)},
    )
    ```

??? note "Ship a custom rule with your package"
    Point the `corral.quality.rules` entry point at a zero-argument callable that returns your rule instances; `run_quality` discovers and registers them on first call.

    ```toml
    [project.entry-points."corral.quality.rules"]
    myproject = "myproject.rules:register_rules"
    ```

## Pitfalls

* **`epsilon_units` is CRS-sensitive.** The default `1e-5` is tight for WGS84 degrees (~1 metre near the equator). A network in projected meters needs something like `epsilon_units=0.5` for the same intent. Always set this explicitly for non-WGS84 networks.
* **`quality.overlapping_links` measures overlap in CRS units too.** It flags two links whose shapes share a stretch longer than `min_overlap_units` (default `0`, so any shared stretch). Links that only meet at a node, cross, or carry the identical shape (the usual way two directions of a two-way street are drawn) are not flagged; a short link running along part of a longer one is. Raise `min_overlap_units` to ignore short shared stretches, e.g. `0.00002` (about 2 m) in WGS84 degrees or `2.0` in projected metres.
* **`quality.overlapping_links` matches exactly, with no tolerance.** Only segments the two shapes share exactly are caught; two digitisations of the same road a hair apart, or whose vertices never line up on a common segment, are not flagged, and `min_overlap_units` does not widen the match. It can also get slow when many link bounding boxes overlap with real area, as long or diagonal links in WGS84 do; disable it with `RuleConfig(enabled=False)` if it dominates a run.
* **`epsilon_units` must not be negative.** `quality.duplicate_near_nodes` raises `ValueError` for a negative value; `0` flags only exact duplicates.
* **`severity_override` doesn't mute — it replaces.** Setting `severity_override=Severity.INFO` on a WARNING rule still produces issues; if you want to drop them, use `enabled=False`.
* **`applies_to` is a method, not a tuple.** The runner calls `rule.applies_to(package)` before `run` and skips the rule silently when it returns `False`. Check for the tables and columns you need there (a rule reading `signal` should return `False` when the table is absent) rather than raising inside `run`.

## See also

* [Quickstart](../quickstart.md#4-run-data-quality-checks) — the default quality flow.
* [API reference](../reference/api.md) — `run_quality`, `RuleConfig`, `Rule`, `Issue`.
