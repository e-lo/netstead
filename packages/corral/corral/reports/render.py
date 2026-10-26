"""Rendering for :class:`~corral.reports.ValidationReport`.

This module contains the three renderers shipped in Phase 2:

- :func:`render_rich` — formatted rich-console string, suitable for the
  CLI default output. Uses the ``rich`` library that already ships as
  a corral dependency. (Task 2.1.)
- :func:`render_json` — stable JSON snapshot with an explicit
  ``report_version`` field, for machine and AI consumers (the MCP
  server, the FastAPI server, the ``--json`` flag on every CLI).
  (Task 2.1.)
- :func:`render_html` — interactive single-file HTML report with
  inlined CSS + JS, embedded report-data blob, severity-ordered tables,
  filter controls, click-to-expand row context, optional Vega-Lite map
  view for geo-located issues. (Task 2.2 / issue #61.)

Severity ordering in every renderer is part of the contract:
``ERROR -> WARNING -> INFO``. Within each severity group, issues are
further grouped by table (with ``None``-table / cross-cutting issues
rendered first, since they tend to be the structural problems that
explain everything else). Quality findings are not a fourth bucket —
they live in whichever severity their rule chose, with
``Category.DATA_QUALITY`` distinguishing them from spec violations.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib import resources
from io import StringIO
from typing import Any

from jinja2 import Environment, StrictUndefined
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .types import (
    Issue,
    Severity,
    ValidationReport,
    severity_rank,
)

__all__ = ["render_html", "render_json", "render_rich"]


# ---------------------------------------------------------------------------
# Color + label mapping (inline by design — these are tiny, only used here,
# and clearer next to the rendering code than imported from a constants
# module).
# ---------------------------------------------------------------------------

_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "blue",
}

_SEVERITY_LABEL: dict[Severity, str] = {
    Severity.ERROR: "ERROR",
    Severity.WARNING: "WARNING",
    Severity.INFO: "INFO",
}


# ---------------------------------------------------------------------------
# Rich console renderer
# ---------------------------------------------------------------------------


def render_rich(report: ValidationReport) -> str:
    """Render the report as a rich-formatted string.

    Layout:

    - Header panel: ``source`` + ``spec_version`` + per-severity counts.
    - One section per severity in canonical order (ERROR, WARNING, INFO).
      Within each section, issues are grouped by table.
      Each issue is one row: ``[code] message`` with the severity color;
      a second indented line shows ``table:column[row]`` plus the
      ``fix_hint`` when present.
    - Footer: totals + the ``is_clean`` verdict.

    The function returns the rendered string; the caller decides whether
    to ``print()`` it, write it to a file, or stream it elsewhere — so
    the same renderer works for the CLI, the notebook, and tests.

    Args:
        report: The report to render.

    Returns:
        The rendered string. Includes ANSI color codes — strip with
        ``rich.text.Text.from_ansi(s).plain`` if a plaintext copy is
        needed.

    Examples:
        >>> from corral.reports import (
        ...     ValidationReport, Severity, Category,
        ... )
        >>> r = ValidationReport(source="x.gmns", spec_version="0.97")
        >>> r.add(severity=Severity.ERROR, category=Category.SCHEMA,
        ...       code="schema.required",
        ...       message="link.from_node_id row 0: value is null",
        ...       table="link")
        Issue(...)
        >>> out = render_rich(r)
        >>> "schema.required" in out
        True
        >>> "x.gmns" in out
        True
    """
    # `record=True` captures the rich output into an internal buffer so
    # callers get a plain Python string back. force_terminal=True keeps
    # the ANSI color codes in the export so tests can assert on them
    # and the CLI displays color even when stdout isn't a TTY.
    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=120,
        record=False,
    )

    _render_header(console, report)
    _render_body(console, report)
    _render_footer(console, report)

    return buffer.getvalue()


def _render_header(console: Console, report: ValidationReport) -> None:
    """Top panel: source + spec version + per-severity counts."""
    title_parts = ["Validation report"]
    if report.source:
        title_parts.append(f"— {report.source}")
    title = " ".join(title_parts)

    body = Text()
    body.append("spec_version: ", style="dim")
    body.append(f"{report.spec_version or 'unspecified'}\n")
    body.append("counts: ", style="dim")
    body.append(
        ", ".join(
            f"{_SEVERITY_LABEL[s].lower()}={report.count(s)}" for s in (Severity.ERROR, Severity.WARNING, Severity.INFO)
        )
    )

    console.print(Panel(body, title=title, expand=False))


def _render_body(console: Console, report: ValidationReport) -> None:
    """Per-severity sections, each with a table grouped by source table."""
    if not report.issues:
        console.print("[dim]No issues recorded.[/dim]")
        return

    # Stable sort by (severity_rank, "" if table is None else table) keeps
    # the canonical severity order AND groups by table within each section.
    # Sort is stable, so original insertion order survives within a group.
    for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO):
        group = report.by_severity(severity)
        if not group:
            continue
        _render_severity_group(console, severity, group)


def _render_severity_group(console: Console, severity: Severity, issues: list[Issue]) -> None:
    """Render the table of issues for one severity level."""
    style = _SEVERITY_STYLE[severity]
    label = _SEVERITY_LABEL[severity]

    table = Table(
        title=f"[{style}]{label}[/] ({len(issues)})",
        show_header=True,
        header_style="bold",
        expand=False,
        title_justify="left",
    )
    table.add_column("Code", no_wrap=True)
    table.add_column("Location", no_wrap=True)
    table.add_column("Message", overflow="fold")

    # Sort by (table-name, row) so issues in the same file cluster together.
    for issue in sorted(issues, key=_issue_sort_key):
        table.add_row(
            Text(issue.code, style=style),
            _format_location(issue),
            _format_message(issue),
        )

    console.print(table)


def _issue_sort_key(issue: Issue) -> tuple[str, int, int]:
    """Sort key: table (blank first), then row, then severity rank.

    Cross-cutting issues (``table is None``) bubble to the top of their
    severity group because they often explain the rest — e.g. a missing
    table makes every per-row error in that table redundant.
    """
    return (
        issue.table or "",
        issue.row if issue.row is not None else -1,
        severity_rank(issue.severity),
    )


def _format_location(issue: Issue) -> Text:
    """Render the ``table:column[row]`` location prefix.

    Returns ``-`` for cross-cutting issues with no table.
    """
    if issue.table is None:
        return Text("—", style="dim")
    parts = [issue.table]
    if issue.column is not None:
        parts.append(f":{issue.column}")
    if issue.row is not None:
        parts.append(f"[{issue.row}]")
    return Text("".join(parts))


def _format_message(issue: Issue) -> Text:
    """Render the message, with the fix hint on a second line when present."""
    out = Text(issue.message)
    if issue.fix_hint:
        out.append("\n→ ", style="dim")
        out.append(issue.fix_hint, style="italic dim")
    return out


def _render_footer(console: Console, report: ValidationReport) -> None:
    """Footer: totals + the is_clean verdict."""
    verdict = "clean" if report.is_clean else "issues found"
    verdict_style = "green" if report.is_clean else "red"
    total = report.count()

    line = Text()
    line.append("Total: ", style="dim")
    line.append(f"{total} issue{'s' if total != 1 else ''}  ")
    line.append("verdict: ", style="dim")
    line.append(verdict, style=f"bold {verdict_style}")
    line.append(f"  (is_clean={report.is_clean})", style="dim")

    console.print(line)


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------


def render_json(report: ValidationReport, *, indent: int = 2) -> str:
    """Render the report as a JSON string with a stable schema.

    The schema (frozen as ``report_version="1"``):

    .. code-block:: json

        {
            "report_version": "1",
            "spec_version": "0.97",
            "source": "leavenworth.gmns",
            "created_at": "2026-05-18T12:34:56.789012",
            "metadata": {},
            "summary": {
                "error": 1, "warning": 0, "info": 0,
                "data_quality": 0, "is_clean": false
            },
            "issues": [
                {
                    "severity": "error",
                    "category": "schema",
                    "code": "schema.required",
                    "message": "link.from_node_id row 0: value is null",
                    "table": "link",
                    "column": "from_node_id",
                    "row": 0,
                    "fix_hint": "Provide a value for from_node_id.",
                    "extra": {}
                }
            ]
        }

    Args:
        report: The report to serialize.
        indent: ``json.dumps`` indent setting (default 2).

    Returns:
        A JSON string. Free of ANSI codes; safe to write to disk or
        return from an HTTP endpoint.

    Examples:
        >>> import json
        >>> from corral.reports import ValidationReport
        >>> r = ValidationReport(source="empty.gmns")
        >>> json.loads(render_json(r))["report_version"]
        '1'
    """
    return report.to_json(indent=indent)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

# Vega-Lite is loaded from CDN ONLY when the report actually has a map
# section (i.e. at least one issue carries geo coords). Inlining ~250KB
# of Vega bytes into every emailed report was rejected; documented as a
# deliberate offline-mode trade-off in render_html's docstring. The data
# + filter UI work fully offline either way.
_VEGA_CDN_URLS: tuple[str, ...] = (
    "https://cdn.jsdelivr.net/npm/vega@5",
    "https://cdn.jsdelivr.net/npm/vega-lite@5",
    "https://cdn.jsdelivr.net/npm/vega-embed@6",
)


def render_html(
    report: ValidationReport,
    *,
    title: str | None = None,
    include_map: bool = True,
) -> str:
    """Render the report as a self-contained interactive HTML file.

    Returns one HTML string with CSS + JS + data embedded — no external
    dependencies, no network calls. Open in a browser as-is, or save with
    ``Path.write_text()``.

    Features:

    - Header with ``spec_version``, ``source``, ``created_at``, and
      per-severity badge counts plus a clean/unclean verdict chip.
    - Severity-ordered issue tables (ERROR → WARNING → INFO)
      with filter controls (severity, category, table, code substring).
    - Click any row to expand a detail panel showing ``fix_hint`` and the
      raw ``extra`` payload.
    - If ``include_map=True`` AND at least one issue carries geo coords
      in ``extra`` (``lon``/``lat`` *or* ``x``/``y``), embed a Vega-Lite
      map view of the located issues.
    - "Export JSON" button downloads the underlying ``report.to_dict()``.

    **Offline-mode trade-off:** the map section pulls Vega-Lite from a
    public CDN at view time. Everything else — data, filtering, expand,
    export — works fully offline. Inlining Vega-Lite's ~250KB into every
    emailed report was rejected as too heavy; the rest of the report is
    self-contained. For fully offline viewing, pass ``include_map=False``.

    Args:
        report: The :class:`ValidationReport` to render.
        title: Optional override for the ``<title>`` tag and the page
            ``<h1>``. Defaults to ``"Validation Report"``, suffixed with
            the report's source when present.
        include_map: If ``False``, skip the map section even when geo
            data is available. Use this for offline-only contexts.

    Returns:
        A single HTML string suitable for ``write_text()`` or embedding.

    Examples:
        >>> from corral.reports import (
        ...     ValidationReport, Severity, Category,
        ... )
        >>> r = ValidationReport(source="x.gmns", spec_version="0.97")
        >>> _ = r.add(severity=Severity.ERROR, category=Category.SCHEMA,
        ...           code="schema.required",
        ...           message="link.from_node_id row 0: value is null",
        ...           table="link")
        >>> html = render_html(r)
        >>> html.lstrip().startswith("<!DOCTYPE html>")
        True
        >>> "schema.required" in html
        True
    """
    # Resolve title (used in both <title> and <h1>). Reader-friendly default.
    if title is None:
        title = "Validation Report"
        if report.source:
            title = f"{title} — {report.source}"

    payload = report.to_dict()
    severities: tuple[Severity, ...] = (
        Severity.ERROR,
        Severity.WARNING,
        Severity.INFO,
    )

    # Group issues by severity for the per-section tables. Each section
    # iterates over its bucket in insertion order; the renderer-side
    # ordering contract is the severity sections themselves, not a
    # within-section re-sort. (The rich renderer re-sorts by table+row
    # for terminal density; for the interactive HTML, filtering covers
    # the same need without forcing a particular order on the reader.)
    issues_by_severity: dict[str, list[dict[str, Any]]] = {sev.value: [] for sev in severities}
    for issue_dict in payload["issues"]:
        issues_by_severity[issue_dict["severity"]].append(issue_dict)

    # Filter dropdowns: only show options that actually appear in the data,
    # so the UI doesn't pretend the user can filter by tables that aren't
    # there.
    filter_options = {
        "category": _sorted_unique(i["category"] for i in payload["issues"]),
        "table": _sorted_unique(i["table"] for i in payload["issues"] if i.get("table")),
    }

    has_geo = include_map and _any_geo(payload["issues"])
    map_spec_json = _build_map_spec_json(payload["issues"]) if has_geo else ""

    css = _read_resource("report.css")
    js = _read_resource("report.js")
    template_source = _read_resource("report.html.j2")

    env = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    template = env.from_string(template_source)
    return template.render(
        title=title,
        report=payload,
        summary=payload["summary"],
        issues_by_severity={sev.value: issues_by_severity[sev.value] for sev in severities},
        severities=[sev.value for sev in severities],
        severity_labels={sev.value: _SEVERITY_LABEL[sev] for sev in severities},
        filter_options=filter_options,
        has_geo=has_geo,
        map_spec_json=map_spec_json,
        vega_cdn_urls=list(_VEGA_CDN_URLS) if has_geo else [],
        css=css,
        js=js,
        # Embedded for the Export JSON button. Re-serialize without indent
        # so the inline blob is compact; the download button will save it
        # as-is. (Tests parse it back to dict for equality.)
        report_json=json.dumps(payload),
    )


# ---------------------------------------------------------------------------
# Helpers (kept inline + small per Lens C: a reader of render_html should
# not have to chase across files to learn how geo detection works).
# ---------------------------------------------------------------------------


def _read_resource(name: str) -> str:
    """Read a bundled template / asset file as text.

    Uses :mod:`importlib.resources` so this works whether the package was
    installed via pip (zipped wheel) or run from a source checkout. The
    package name is spelled out explicitly (rather than via ``__package__``)
    so a type checker can prove ``resources.files`` receives a ``str``.
    """
    return (resources.files("corral.reports") / "templates" / name).read_text(encoding="utf-8")


def _sorted_unique(values: Iterable[str | None]) -> list[str]:
    """Stable de-dupe + sort for filter-option lists."""
    return sorted({v for v in values if v})


def _extract_coords(issue: dict[str, Any]) -> tuple[float, float] | None:
    """Return ``(lon, lat)`` if ``issue["extra"]`` carries geo coords.

    Accepts either GMNS-style ``x``/``y`` (longitude / latitude) or the
    more generic ``lon``/``lat`` naming. Returns ``None`` when neither
    pair is present, or when the values are not finite numbers. The
    map renderer ignores issues without coords.
    """
    extra = issue.get("extra") or {}
    if not isinstance(extra, dict):
        return None
    lon = extra.get("lon", extra.get("x"))
    lat = extra.get("lat", extra.get("y"))
    if lon is None or lat is None:
        return None
    try:
        lon_f = float(lon)
        lat_f = float(lat)
    except (TypeError, ValueError):
        return None
    return (lon_f, lat_f)


def _any_geo(issues: Iterable[dict[str, Any]]) -> bool:
    """``True`` when at least one issue carries usable geo coords."""
    return any(_extract_coords(i) is not None for i in issues)


def _build_map_spec_json(issues: Iterable[dict[str, Any]]) -> str:
    """Build a Vega-Lite v5 spec (as JSON) for the located issues.

    Each located issue becomes one circle, colored by severity. Tooltips
    surface ``code`` + ``message`` so a reader can hover for context.
    Returns an empty string if no issues are located — callers should
    gate on :func:`_any_geo` first.
    """
    points: list[dict[str, Any]] = []
    for issue in issues:
        coords = _extract_coords(issue)
        if coords is None:
            continue
        lon, lat = coords
        points.append(
            {
                "lon": lon,
                "lat": lat,
                "severity": issue["severity"],
                "code": issue["code"],
                "message": issue["message"],
                "table": issue.get("table") or "",
                "row": issue.get("row"),
            }
        )

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "corral located validation issues",
        "width": "container",
        "height": 360,
        "data": {"values": points},
        "mark": {"type": "circle", "size": 80, "opacity": 0.8},
        "encoding": {
            "longitude": {"field": "lon", "type": "quantitative"},
            "latitude": {"field": "lat", "type": "quantitative"},
            "color": {
                "field": "severity",
                "type": "nominal",
                # Severity palette matches the report CSS so the map keys
                # to the same colors readers see in the badges + tables.
                "scale": {
                    "domain": [
                        Severity.ERROR.value,
                        Severity.WARNING.value,
                        Severity.INFO.value,
                    ],
                    "range": ["#d1242f", "#9a6700", "#0969da"],
                },
            },
            "tooltip": [
                {"field": "code", "type": "nominal"},
                {"field": "message", "type": "nominal"},
                {"field": "severity", "type": "nominal"},
                {"field": "table", "type": "nominal"},
                {"field": "row", "type": "quantitative"},
            ],
        },
    }
    return json.dumps(spec)
