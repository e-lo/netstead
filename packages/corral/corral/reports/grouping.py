"""Collapse repeated structural findings and inventory a package's resources.

A structural pass over a package that ships nine of the twenty-five
GMNS tables emits sixteen ``structural.missing_optional_resource``
findings whose messages differ only by resource name, each repeating
the package source path. Rendered one per line they bury the single
WARNING a user needs to act on. This module gives renderers two
helpers:

* :func:`group_issues` — fold findings that share a code and differ
  only by resource into one :class:`IssueGroup`, keeping everything
  else as a group of one so a renderer can iterate uniformly.
* :func:`resource_inventory` — the "which tables are present" fact
  as one table (``name`` / ``required`` / ``present`` / ``rows``)
  instead of one finding per absent table.

Plus two small formatting helpers the CLI and the HTML report share:
:func:`severity_summary` (``2 errors · 1 warning · 16 info``) and
:func:`strip_source_prefix` (drop the ``package source '…': `` lead-in
once the path has been printed on its own line).

Grouping is deliberately narrow: only ``Category.STRUCTURAL`` findings
without a row can group, because only there is the message a pure
function of the resource name. A foreign-key or schema finding names a
column, a value, or a row and must stay on its own line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import Category, Issue, Severity, ValidationReport

__all__ = [
    "IssueGroup",
    "group_issues",
    "resource_inventory",
    "severity_summary",
    "strip_source_prefix",
]

#: How many resource names a group summary spells out before ``(+N more)``.
DEFAULT_SUMMARY_LIMIT = 8

#: Lead-in per structural code when a group is summarised on one line.
_GROUP_LEAD_IN: dict[str, str] = {
    "structural.missing_optional_resource": "optional resources absent: ",
    "structural.missing_required_resource": "required resources missing: ",
    "structural.unexpected_resource": "undeclared resources found: ",
}

#: Structural codes that mean "the spec declares it, the source lacks it".
_MISSING_CODES = frozenset({"structural.missing_required_resource", "structural.missing_optional_resource"})


@dataclass(frozen=True)
class IssueGroup:
    """One row of a grouped report: a code, its severity, and the findings behind it.

    ``groupable`` is ``True`` when the group collapsed two or more
    findings; a group of one carries ``groupable=False`` so a renderer
    knows to print the finding's own message. ``table_names`` holds
    the ``table`` of each member that has one, in first-seen order.
    """

    code: str
    severity: Severity
    table_names: list[str]
    issues: list[Issue]
    groupable: bool

    @property
    def count(self) -> int:
        """Number of findings in the group."""
        return len(self.issues)

    def summary(self, *, limit: int = DEFAULT_SUMMARY_LIMIT) -> str:
        """One-line text for the group: a lead-in plus the resource names.

        Spells out at most ``limit`` names and appends ``(+N more)`` for
        the rest. A group of one returns the finding's own message.

        Examples:
            >>> issues = [
            ...     Issue(Severity.INFO, Category.STRUCTURAL, "structural.missing_optional_resource", "m", table=t)
            ...     for t in ("zone", "config", "curb_seg")
            ... ]
            >>> group_issues(issues)[0].summary(limit=2)
            'optional resources absent: zone, config (+1 more)'
        """
        if not self.groupable:
            return self.issues[0].message
        lead_in = _GROUP_LEAD_IN.get(self.code, f"{self.count} findings on: ")
        names = self.table_names
        shown = ", ".join(names[:limit])
        if len(names) > limit:
            shown += f" (+{len(names) - limit} more)"
        return lead_in + shown


def group_issues(issues: list[Issue]) -> list[IssueGroup]:
    """Fold findings that share a structural code into one group each.

    Two or more findings group when they share ``code`` and
    ``severity``, are ``Category.STRUCTURAL`` and have ``row is None``.
    Every other finding becomes a group of one with ``groupable=False``.
    Groups come back in the order their first finding appeared, so a
    renderer that iterates the result preserves the report's ordering.

    Examples:
        >>> issues = [
        ...     Issue(Severity.INFO, Category.STRUCTURAL, "structural.missing_optional_resource", "m", table=t)
        ...     for t in ("zone", "config")
        ... ]
        >>> [(g.count, g.groupable, g.table_names) for g in group_issues(issues)]
        [(2, True, ['zone', 'config'])]
    """
    groups: list[IssueGroup] = []
    by_key: dict[tuple[str, Severity], list[Issue]] = {}
    order: list[tuple[str, Severity] | Issue] = []

    for issue in issues:
        if issue.category is Category.STRUCTURAL and issue.row is None:
            key = (issue.code, issue.severity)
            if key not in by_key:
                by_key[key] = []
                order.append(key)
            by_key[key].append(issue)
        else:
            order.append(issue)

    for entry in order:
        if isinstance(entry, Issue):
            groups.append(_single(entry))
            continue
        members = by_key[entry]
        if len(members) < 2:
            groups.append(_single(members[0]))
            continue
        groups.append(
            IssueGroup(
                code=entry[0],
                severity=entry[1],
                table_names=[i.table for i in members if i.table is not None],
                issues=list(members),
                groupable=True,
            )
        )
    return groups


def _single(issue: Issue) -> IssueGroup:
    return IssueGroup(
        code=issue.code,
        severity=issue.severity,
        table_names=[issue.table] if issue.table is not None else [],
        issues=[issue],
        groupable=False,
    )


def severity_summary(issues: list[Issue]) -> str:
    """``2 errors · 1 warning · 16 info`` — counts with correct singular/plural.

    Examples:
        >>> severity_summary([Issue(Severity.ERROR, Category.SCHEMA, "schema.required", "m")])
        '1 error · 0 warnings · 0 info'
    """
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity is Severity.WARNING)
    info = sum(1 for i in issues if i.severity is Severity.INFO)
    return (
        f"{errors} error{'' if errors == 1 else 's'} · {warnings} warning{'' if warnings == 1 else 's'} · {info} info"
    )


def strip_source_prefix(message: str, source: str | None) -> str:
    """Drop the ``package source '<source>': `` lead-in structural messages carry.

    Renderers print the source once above the findings; repeating the
    path in every row is the noise the 2026-10-20 beta feedback called
    out. Messages that don't start with that exact lead-in are returned
    unchanged, as is everything when ``source`` is ``None``.

    Examples:
        >>> strip_source_prefix("package source '/d/csv': optional resource 'zone' is not present", "/d/csv")
        "optional resource 'zone' is not present"
    """
    if source is None:
        return message
    prefix = f"package source {source!r}: "
    return message[len(prefix) :] if message.startswith(prefix) else message


def resource_inventory(package_or_report: Any, spec: Any | None = None) -> list[dict[str, Any]]:
    """List every spec-declared resource with whether it is required and present.

    Returns one ``{"name", "required", "present", "rows"}`` dict per
    resource of the spec, in spec order. ``required`` follows the
    structural pass's opt-in policy (only ``required=True`` counts; see
    :mod:`corral.validation.structural`).

    Args:
        package_or_report: A :class:`~corral.dataset.Package` (or
            subclass such as ``netstead.Network``) — presence comes from
            its loaded ``tables`` and ``rows`` from each table's
            ``count()`` — or a :class:`ValidationReport`, in which case
            presence is read off its ``structural.missing_*_resource``
            findings and ``rows`` is ``None``.
        spec: The :class:`~corral.spec.model.DataPackage` to inventory.
            Defaults to the package's own ``spec``; required for a
            report.

    Raises:
        TypeError: when a report is given without a ``spec``.

    Examples:
        >>> from corral.spec.model import DataPackage, Resource
        >>> spec = DataPackage(name="d", resources=[Resource(name="link", required=True), Resource(name="zone")])
        >>> report = ValidationReport()
        >>> report.add_issue(Issue(Severity.INFO, Category.STRUCTURAL, "structural.missing_optional_resource",
        ...     "m", table="zone"))
        >>> [(e["name"], e["required"], e["present"]) for e in resource_inventory(report, spec)]
        [('link', True, True), ('zone', False, False)]
    """
    if isinstance(package_or_report, ValidationReport):
        if spec is None:
            raise TypeError("resource_inventory(report) needs a spec: pass the DataPackage the report was run against")
        missing = {i.table for i in package_or_report.issues if i.code in _MISSING_CODES and i.table is not None}
        return [
            {
                "name": resource.name,
                "required": resource.required is True,
                "present": resource.name not in missing,
                "rows": None,
            }
            for resource in spec.resources
        ]

    package = package_or_report
    if spec is None:
        spec = package.spec
    tables = package.tables
    return [
        {
            "name": resource.name,
            "required": resource.required is True,
            "present": resource.name in tables,
            "rows": tables[resource.name].count() if resource.name in tables else None,
        }
        for resource in spec.resources
    ]
