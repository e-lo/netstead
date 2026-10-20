"""Tests for :mod:`corral.reports.grouping` — collapsing repeated structural findings.

The 2026-10-20 beta feedback: validating Leavenworth prints sixteen
near-identical ``structural.missing_optional_resource`` lines, each
repeating the package source path, with the one WARNING that matters
buried at the bottom. These tests pin the helpers the CLI and HTML
report use to collapse them.
"""

from __future__ import annotations

import pytest
from corral.reports import Category, Issue, Severity, ValidationReport
from corral.reports.grouping import (
    IssueGroup,
    group_issues,
    resource_inventory,
    severity_summary,
    strip_source_prefix,
)

SOURCE = "/data/leavenworth/csv"

OPTIONAL_TABLES = [
    "location",
    "movement",
    "movement_tod",
    "segment",
    "segment_lane",
    "signal_coordination",
    "signal_phase_mvmt",
    "signal_timing_plan",
    "signal_timing_phase",
    "signal_detector",
    "segment_tod",
    "lane_tod",
    "segment_lane_tod",
    "zone",
    "config",
    "curb_seg",
]


def _missing_optional(name: str) -> Issue:
    return Issue(
        severity=Severity.INFO,
        category=Category.STRUCTURAL,
        code="structural.missing_optional_resource",
        message=f"package source {SOURCE!r}: optional resource {name!r} is not present",
        table=name,
    )


def _unverifiable(table: str, row: int | None) -> Issue:
    return Issue(
        severity=Severity.WARNING,
        category=Category.FOREIGN_KEY,
        code="fk.unverifiable",
        message=f"{table} FK zone_id->zone.zone_id: target table 'zone' missing from package",
        table=table,
        row=row,
    )


# ---------------------------------------------------------------------------
# group_issues
# ---------------------------------------------------------------------------


def test_sixteen_missing_optional_resources_collapse_to_one_group():
    issues = [_missing_optional(n) for n in OPTIONAL_TABLES]
    groups = group_issues(issues)
    assert len(groups) == 1
    (group,) = groups
    assert isinstance(group, IssueGroup)
    assert group.groupable is True
    assert group.code == "structural.missing_optional_resource"
    assert group.severity is Severity.INFO
    assert group.table_names == OPTIONAL_TABLES
    assert group.issues == issues
    assert group.count == 16


def test_row_level_findings_are_not_grouped():
    issues = [_unverifiable("node", 3), _unverifiable("link", 7)]
    groups = group_issues(issues)
    assert [g.groupable for g in groups] == [False, False]
    assert [g.count for g in groups] == [1, 1]
    assert [g.issues[0] for g in groups] == issues


def test_non_structural_findings_are_not_grouped_even_without_rows():
    """Only structural codes differ by resource name alone; FK messages carry detail."""
    issues = [_unverifiable("node", None), _unverifiable("link", None)]
    groups = group_issues(issues)
    assert [g.groupable for g in groups] == [False, False]


def test_mixed_severities_never_merge():
    info = _missing_optional("zone")
    warning = Issue(
        severity=Severity.WARNING,
        category=Category.STRUCTURAL,
        code="structural.missing_optional_resource",
        message="escalated",
        table="config",
    )
    second_info = _missing_optional("curb_seg")
    groups = group_issues([info, warning, second_info])
    assert [(g.severity, g.count, g.groupable) for g in groups] == [
        (Severity.INFO, 2, True),
        (Severity.WARNING, 1, False),
    ]


def test_a_single_structural_finding_stays_a_group_of_one():
    (group,) = group_issues([_missing_optional("zone")])
    assert group.groupable is False
    assert group.table_names == ["zone"]


def test_groups_keep_first_appearance_order():
    issues = [_unverifiable("node", None), *[_missing_optional(n) for n in OPTIONAL_TABLES[:2]]]
    groups = group_issues(issues)
    assert [g.code for g in groups] == ["fk.unverifiable", "structural.missing_optional_resource"]


def test_empty_input_gives_no_groups():
    assert group_issues([]) == []


# ---------------------------------------------------------------------------
# IssueGroup.summary
# ---------------------------------------------------------------------------


def test_summary_lists_resources_and_truncates_after_eight():
    (group,) = group_issues([_missing_optional(n) for n in OPTIONAL_TABLES])
    text = group.summary()
    assert text.startswith("optional resources absent: location, movement, ")
    assert text.endswith("signal_timing_plan (+8 more)")
    assert SOURCE not in text


def test_summary_lists_every_resource_when_at_or_under_the_limit():
    (group,) = group_issues([_missing_optional(n) for n in OPTIONAL_TABLES[:8]])
    assert group.summary() == "optional resources absent: " + ", ".join(OPTIONAL_TABLES[:8])


def test_summary_limit_is_adjustable():
    (group,) = group_issues([_missing_optional(n) for n in OPTIONAL_TABLES[:3]])
    assert group.summary(limit=2) == "optional resources absent: location, movement (+1 more)"


def test_summary_of_an_ungrouped_finding_is_its_message():
    (group,) = group_issues([_unverifiable("node", None)])
    assert group.summary() == group.issues[0].message


@pytest.mark.parametrize(
    ("code", "severity", "prefix"),
    [
        ("structural.missing_required_resource", Severity.ERROR, "required resources missing: "),
        ("structural.unexpected_resource", Severity.WARNING, "undeclared resources found: "),
        ("structural.something_new", Severity.INFO, "2 findings on: "),
    ],
)
def test_summary_wording_per_structural_code(code, severity, prefix):
    issues = [
        Issue(severity=severity, category=Category.STRUCTURAL, code=code, message="m", table=t) for t in ("a", "b")
    ]
    (group,) = group_issues(issues)
    assert group.summary() == prefix + "a, b"


# ---------------------------------------------------------------------------
# strip_source_prefix / severity_summary
# ---------------------------------------------------------------------------


def test_strip_source_prefix_removes_the_package_source_lead_in():
    message = _missing_optional("zone").message
    assert strip_source_prefix(message, SOURCE) == "optional resource 'zone' is not present"


def test_strip_source_prefix_leaves_other_messages_alone():
    assert strip_source_prefix("node FK zone_id->zone.zone_id", SOURCE) == "node FK zone_id->zone.zone_id"
    assert strip_source_prefix(_missing_optional("zone").message, None) == _missing_optional("zone").message


def test_severity_summary_pluralises_correctly():
    issues = [
        Issue(severity=Severity.ERROR, category=Category.SCHEMA, code="schema.required", message="m"),
        Issue(severity=Severity.ERROR, category=Category.SCHEMA, code="schema.required", message="m"),
        _unverifiable("node", None),
        *[_missing_optional(n) for n in OPTIONAL_TABLES],
    ]
    assert severity_summary(issues) == "2 errors · 1 warning · 16 info"
    assert severity_summary([issues[0]]) == "1 error · 0 warnings · 0 info"
    assert severity_summary([]) == "0 errors · 0 warnings · 0 info"


# ---------------------------------------------------------------------------
# resource_inventory
# ---------------------------------------------------------------------------


def test_resource_inventory_from_the_leavenworth_network():
    pytest.importorskip("netstead")
    from netstead import Network
    from netstead.fixtures import leavenworth

    net = Network.from_source(leavenworth.csv_dir())
    inventory = resource_inventory(net)
    by_name = {entry["name"]: entry for entry in inventory}
    assert [e["name"] for e in inventory] == [r.name for r in net.spec.resources]
    assert by_name["link"] == {"name": "link", "required": True, "present": True, "rows": 339}
    assert by_name["zone"] == {"name": "zone", "required": False, "present": False, "rows": None}
    assert by_name["geometry"]["required"] is False
    assert by_name["geometry"]["present"] is True


def test_resource_inventory_from_a_report_uses_structural_findings():
    from corral.spec.model import DataPackage, Resource

    spec = DataPackage(
        name="demo",
        resources=[
            Resource(name="link", required=True),
            Resource(name="node", required=True),
            Resource(name="zone"),
        ],
    )
    report = ValidationReport(source=SOURCE)
    report.add_issue(_missing_optional("zone"))
    report.add_issue(
        Issue(
            severity=Severity.ERROR,
            category=Category.STRUCTURAL,
            code="structural.missing_required_resource",
            message="node missing",
            table="node",
        )
    )
    assert resource_inventory(report, spec) == [
        {"name": "link", "required": True, "present": True, "rows": None},
        {"name": "node", "required": True, "present": False, "rows": None},
        {"name": "zone", "required": False, "present": False, "rows": None},
    ]


def test_resource_inventory_from_a_report_needs_a_spec():
    with pytest.raises(TypeError, match="spec"):
        resource_inventory(ValidationReport())
