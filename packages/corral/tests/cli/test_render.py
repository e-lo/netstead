"""Tests for :func:`corral.cli.render.render_issues` — the rich findings table.

Pins the 2026-10-20 beta-feedback shape: a severity summary line first,
the package source printed once, repeated structural findings folded
into one row (``--all`` expands them), and a table that still reads at
a narrow terminal width.
"""

from __future__ import annotations

import io
import json
import re

import pytest
from corral.cli import render
from corral.reports import Category, Issue, Severity
from rich.console import Console

SOURCE = "/data/leavenworth/csv"
SUMMARY_RE = re.compile(r"\d+ errors? · \d+ warnings? · \d+ info")

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


def _leavenworth_like_issues() -> list[Issue]:
    missing = [
        Issue(
            severity=Severity.INFO,
            category=Category.STRUCTURAL,
            code="structural.missing_optional_resource",
            message=f"package source {SOURCE!r}: optional resource {name!r} is not present",
            table=name,
        )
        for name in OPTIONAL_TABLES
    ]
    unverifiable = Issue(
        severity=Severity.WARNING,
        category=Category.FOREIGN_KEY,
        code="fk.unverifiable",
        message="node FK zone_id->zone.zone_id: target table 'zone' missing from package",
        table="node",
        column="zone_id",
    )
    return [*missing, unverifiable]


@pytest.fixture(params=[120, 60], ids=["wide", "narrow"])
def captured(monkeypatch, request):
    """Swap the module console for one that records at the parametrised width."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=request.param, force_terminal=False, color_system=None)
    monkeypatch.setattr(render, "console", console)
    return buffer


def _lines(buffer: io.StringIO) -> list[str]:
    return buffer.getvalue().splitlines()


def _table_rows(buffer: io.StringIO) -> list[str]:
    """The body rows of the rich table, joined across wrapped lines, whitespace removed.

    A row starts at a line whose first cell holds a severity word; the
    lines that follow until the next such line (or the table's bottom
    rule) are its wrapped continuation. Rich wraps on spaces and folds
    long words without a separator, so the only width-independent
    comparison is one with every space removed: match with
    :func:`_squash`.
    """
    rows: list[list[str]] = []
    for line in _lines(buffer):
        if not line.startswith("│"):
            continue
        cells = [_squash(c) for c in line.split("│")[1:-1]]
        if cells[0] in {"error", "warning", "info"}:
            rows.append(cells)
        elif rows and cells[0] == "":
            rows[-1] = [a + b for a, b in zip(rows[-1], cells, strict=True)]
    return ["|".join(cells) for cells in rows]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_first_line_is_the_severity_summary(captured):
    render.render_issues(_leavenworth_like_issues(), json_out=False, source=SOURCE)
    assert SUMMARY_RE.fullmatch(_lines(captured)[0])
    assert _lines(captured)[0] == "0 errors · 1 warning · 16 info"


def test_source_path_is_printed_once_above_the_table(captured):
    render.render_issues(_leavenworth_like_issues(), json_out=False, source=SOURCE)
    text = captured.getvalue()
    assert text.count(SOURCE) == 1
    assert f"source: {SOURCE}" in text


def test_grouped_findings_render_as_one_row(captured):
    render.render_issues(_leavenworth_like_issues(), json_out=False, source=SOURCE)
    rows = _table_rows(captured)
    grouped = [r for r in rows if _squash("optional resources absent") in r]
    assert len(grouped) == 1
    assert _squash("16 tables") in grouped[0]
    assert _squash("(+8 more)") in grouped[0]
    assert len(rows) == 2  # the group plus the fk.unverifiable warning


def test_all_expands_the_group_to_one_row_per_finding(captured):
    render.render_issues(_leavenworth_like_issues(), json_out=False, source=SOURCE, all=True)
    rows = _table_rows(captured)
    assert len(rows) == 17
    assert sum(_squash("optional resource") in r and _squash("is not present") in r for r in rows) == 16
    assert not any(_squash("optional resources absent") in r for r in rows)
    # The lead-in is stripped from each expanded row; the path shows once.
    assert captured.getvalue().count(SOURCE) == 1


def test_header_precedes_the_summary(captured):
    render.render_issues(_leavenworth_like_issues(), json_out=False, header="validation report: x", source=SOURCE)
    lines = _lines(captured)
    assert lines[0] == "validation report: x"
    assert SUMMARY_RE.fullmatch(lines[1])


def test_source_already_in_the_header_is_not_printed_twice(captured):
    render.render_issues(
        _leavenworth_like_issues(), json_out=False, header=f"validation report: {SOURCE}", source=SOURCE
    )
    text = captured.getvalue()
    assert text.count(SOURCE) == 1
    assert "source:" not in text


def test_no_issues_prints_summary_then_clean_marker(captured):
    render.render_issues([], json_out=False)
    lines = _lines(captured)
    assert lines[0] == "0 errors · 0 warnings · 0 info"
    assert "no issues" in lines[1]


def test_narrow_console_keeps_severity_and_code_readable(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=buffer, width=60, force_terminal=False, color_system=None))
    render.render_issues(_leavenworth_like_issues(), json_out=False, source=SOURCE)
    text = buffer.getvalue()
    # Nothing is cropped off the right edge, and severity words never break.
    assert all(len(line) <= 60 for line in text.splitlines())
    assert all(line.endswith(("┓", "┃", "┩", "│", "┘")) for line in text.splitlines()[2:])
    assert "warning" in text and "info" in text
    assert not re.search(r"warn\s*\n", text)
    # Codes fold across lines instead of being cut with an ellipsis.
    assert "…" not in text
    assert "fk.unverifiable" in "\n".join(_table_rows(buffer))


def test_json_shape_is_unchanged_by_grouping(capsys):
    render.render_issues(_leavenworth_like_issues(), json_out=True, header="h", source=SOURCE, all=False)
    payload = json.loads(capsys.readouterr().out)
    assert payload["header"] == "h"
    assert len(payload["issues"]) == 17
    assert set(payload["issues"][0]) == {
        "severity",
        "category",
        "code",
        "message",
        "table",
        "column",
        "row",
        "fix_hint",
        "extra",
    }
    assert payload["issues"][0]["message"].startswith(f"package source {SOURCE!r}")
