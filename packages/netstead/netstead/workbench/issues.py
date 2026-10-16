"""Validation in the workbench: run the checks, then tie each issue to a record and to a map anchor.

A rule reports a row *position* (``Issue.row``) in the order it read the table (``Table.to_pandas()``), at the
version it ran on. Positions are turned into primary keys here, once, from that same order, so the UI can link
an issue to its row by key and to the map by ``anchor``:

* ``{"link": id}`` / ``{"node": id}``: the record itself, or the link/node a row points at (a lane's ``link_id``);
* ``{"lonlat": [x, y]}``: a rule that reported coordinates (``Issue.extra`` ``lon``/``lat`` or ``x``/``y``);
* ``None``: unlocated (a missing table, a whole-table finding).

The browser places anchors from the network buffer it has already decoded, so no geometry is sent twice.
Issue messages can quote data values: whoever renders them escapes them (the JSON routes send them as data).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from corral.quality import RuleConfig, list_rules, run_quality
from corral.reports import Severity, ValidationReport

from netstead.config import ValidationSettings
from netstead.quality import register_all
from netstead.viz.styling import json_scalar

from .editing import editable
from .related import primary_keys

__all__ = ["MAX_MARKERS", "SEVERITIES", "IssueSet", "locate_issues", "rule_configs", "run_validation"]

SEVERITIES = ("error", "warning", "info")
#: Located issues sent to the map; beyond this the markers answer says ``truncated``.
MAX_MARKERS = 50_000
#: Tables the map draws: an issue on one of their records anchors on the record itself.
_MAP_TABLES = ("link", "node")
#: Columns that place a row of another table on the map, via the record they point at.
_POINTS_AT = (("link_id", "link"), ("node_id", "node"))


@dataclass(frozen=True)
class IssueSet:
    """One validation run of one network version: the located issues, plus the report for the HTML export.

    ``network`` is the copy-on-write snapshot the run read (``registry.derived_copy``): the HTML export draws
    the issues on it, so an edit made after the run cannot move them.
    """

    net_id: str
    version: int
    issues: tuple[dict[str, Any], ...]
    report: ValidationReport
    unknown_rules: tuple[str, ...] = ()
    ran_at: float = field(default_factory=time.time)
    network: Any = field(default=None, repr=False, compare=False)

    def counts(self) -> dict[str, int]:
        """Issues per severity (every severity present, zeros included)."""
        found = Counter(i["severity"] for i in self.issues)
        return {s: found.get(s, 0) for s in SEVERITIES}

    def facets(self) -> dict[str, dict[str, int]]:
        """Issue counts per severity, table (``(none)`` for none) and code, over the whole set."""
        return {
            "severity": self.counts(),
            "table": dict(Counter(i["table"] or "(none)" for i in self.issues).most_common()),
            "code": dict(Counter(i["code"] for i in self.issues).most_common()),
        }

    def query(
        self,
        *,
        severity: Sequence[str] = (),
        table: str | None = None,
        code: str | None = None,
        located: bool | None = None,
        record: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """The issues matching every given filter, in report order. ``record`` is ``(table, str(key))``.

        A linear scan of every issue per call: fine at the issue counts a run produces (the routes page the result).
        """

        def keep(i: dict[str, Any]) -> bool:
            return (
                (not severity or i["severity"] in severity)
                and (table is None or (i["table"] or "(none)") == table)
                and (code is None or i["code"] == code)
                and (located is None or (i["anchor"] is not None) == located)
                and (record is None or (i["table"] == record[0] and str(i["key"]) == record[1]))
            )

        return [i for i in self.issues if keep(i)]

    def by_record(self) -> dict[str, dict[str, list[int]]]:
        """``{table: {str(key): [issue numbers]}}``, for issues tied to a record (row marks, "This record")."""
        out: dict[str, dict[str, list[int]]] = {}
        for i in self.issues:
            if i["table"] and i["key"] is not None:
                out.setdefault(i["table"], {}).setdefault(str(i["key"]), []).append(i["i"])
        return out


def rule_configs(settings: ValidationSettings) -> dict[str, RuleConfig]:
    """``validation.rules`` as corral ``RuleConfig`` objects, keyed by rule code."""
    return {
        code: RuleConfig(
            enabled=rule.enabled,
            severity_override=Severity(rule.severity_override) if rule.severity_override else None,
            thresholds=dict(rule.thresholds),
        )
        for code, rule in settings.rules.items()
    }


def run_validation(
    net: Any, settings: ValidationSettings, *, quality: bool = True
) -> tuple[ValidationReport, tuple[str, ...]]:
    """Spec, key and structure checks (``Network.validate``), then the quality rules.

    Returns ``(report, unknown rule codes)``: codes in ``validation.rules`` that no installed rule has.
    """
    report = net.validate()
    if not quality:
        return report, ()
    register_all()  # the entry point can be missing in an editable install (the quality CLI does the same)
    run_quality(net, config=rule_configs(settings), report=report)
    # After the run: run_quality discovers the entry-point rules, so every installed rule is known by now.
    unknown = tuple(sorted(set(settings.rules) - set(list_rules())))
    return report, unknown


def locate_issues(report: ValidationReport, net: Any) -> tuple[dict[str, Any], ...]:
    """Every issue as a JSON-safe dict, with its record ``key``, its map ``anchor``, and whether it is ``fixable``."""
    tables = net.tables
    pks = primary_keys(net.spec, {name: t.columns() for name, t in tables.items()})
    columns: dict[tuple[str, str], list[Any]] = {}

    def at(table: str, column: str, row: int) -> Any:
        if (table, column) not in columns:
            src = tables[table]
            columns[(table, column)] = (
                src.select(column).to_pandas()[column].tolist() if column in src.columns() else []
            )
        values = columns[(table, column)]
        return json_scalar(values[row]) if 0 <= row < len(values) else None

    out = []
    for n, issue in enumerate(report.issues):
        table = issue.table if issue.table in tables else None
        pk = pks.get(table) if table else None
        key = at(table, pk, issue.row) if table and pk and issue.row is not None else None
        schema = tables[table].expr.schema() if table else {}
        fixable = key is not None and issue.column not in (None, pk) and issue.column in schema
        fixable = fixable and editable(schema[issue.column])
        out.append(
            {
                "i": n,
                "severity": issue.severity.value,
                "category": issue.category.value,
                "code": issue.code,
                "message": issue.message,
                "fix_hint": issue.fix_hint,
                "table": issue.table,
                "column": issue.column,
                "row": issue.row,
                "key": key,
                "anchor": _anchor(issue, table, key, at),
                "fixable": bool(fixable),
            }
        )
    return tuple(out)


def _anchor(issue: Any, table: str | None, key: Any, at: Callable[[str, str, int], Any]) -> dict[str, Any] | None:
    x = issue.extra.get("lon", issue.extra.get("x"))
    y = issue.extra.get("lat", issue.extra.get("y"))
    if _is_number(x) and _is_number(y):
        return {"lonlat": [float(x), float(y)]}
    if table is None or key is None:
        return None
    if table in _MAP_TABLES:
        return {table: key}
    for column, target in _POINTS_AT:
        value = at(table, column, issue.row)
        if value is not None:
            return {target: value}
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
