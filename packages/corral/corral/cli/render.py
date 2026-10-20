"""Output helpers for CLI commands — rich console + structured JSON.

Every CLI command renders through one of these helpers so the
``--json`` contract is uniform: when ``json_out`` is True, we emit
exactly one JSON document on stdout (no log noise, no progress bars);
otherwise we render a rich panel/table to stderr-aware
:class:`~rich.console.Console`.

Why split rendering from the command body: agents calling the CLI via
``--json`` parse a single document. Rules dropping log noise into the
JSON stream would break them. Centralising the formatter here keeps
the contract obvious — and Phase 4 task 4.7's approval prompts use
the same console so prompts surface to a human even when ``--json``
is set on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from corral.reports.grouping import group_issues, severity_summary, strip_source_prefix

__all__ = ["console", "render_dict", "render_issues", "render_table"]

# A single shared Console so multiple commands look identical and
# auto-detect notebook vs terminal once. ``stderr=True`` keeps prompts
# + progress on stderr so ``--json`` on stdout stays parseable.
console = Console(stderr=True)


def render_dict(data: dict[str, Any], *, json_out: bool, title: str | None = None) -> None:
    """Render a flat dict as a 2-column rich table OR a JSON document.

    ``data`` values must be JSON-serialisable when ``json_out=True``.
    The function emits to stdout for JSON (so it's pipeable) and to
    stderr for rich (so it doesn't collide with --json on stdout).
    """
    if json_out:
        # stdout, no trailing extras — agents parse a single document.
        json.dump(data, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    table = Table(title=title, show_header=False)
    table.add_column("key", style="bold cyan")
    table.add_column("value")
    for key, value in data.items():
        table.add_row(str(key), str(value))
    console.print(table)


def render_table(rows: list[dict[str, Any]], *, json_out: bool, title: str | None = None) -> None:
    """Render a list of homogeneous dicts as a rich table OR a JSON array.

    Column order is taken from the first row. Empty input prints an
    informational message in rich mode and ``[]`` in JSON mode.
    """
    if json_out:
        json.dump(rows, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    if not rows:
        console.print(f"[dim]{title or 'rows'}: (none)[/dim]")
        return

    table = Table(title=title)
    for col in rows[0]:
        table.add_column(str(col))
    for row in rows:
        table.add_row(*(str(row.get(c, "")) for c in rows[0]))
    console.print(table)


def render_issues(
    issues: list[Any],
    *,
    json_out: bool,
    header: str | None = None,
    source: str | None = None,
    all: bool = False,  # mirrors the CLI flag name
) -> None:
    """Render a list of :class:`~corral.reports.Issue` records.

    JSON mode emits ``{"header": ..., "issues": [...]}`` — one dict per
    issue via :func:`_issue_to_payload`; the shape is a published
    contract and is not affected by ``source`` or ``all``.

    Rich mode prints, in order: the header (when given), a severity
    summary line (``0 errors · 1 warning · 16 info``), ``source: <path>``
    once (when given and not already part of the header), then the
    table. Structural findings that share a
    code and differ only by resource — the sixteen
    ``structural.missing_optional_resource`` lines a partial GMNS
    package produces — fold into one row whose table column says
    ``16 tables`` and whose message lists the resources
    (:func:`~corral.reports.grouping.group_issues`). ``all=True``
    expands them to one row per finding. The ``package source '…': ``
    lead-in is stripped from every message once ``source`` has been
    printed on its own line.

    At a narrow console width the severity and row columns never wrap,
    and code / table / message fold rather than truncate with an
    ellipsis, so every code stays greppable. (No ``min_width`` on
    purpose: rich adds it on top of padding and overshoots the console
    by a column, cropping the right border.)
    """
    if json_out:
        # Each Issue is a frozen dataclass; vars() works on it.
        payload = [_issue_to_payload(i) for i in issues]
        json.dump({"header": header, "issues": payload}, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    if header:
        console.print(f"[bold]{escape(header)}[/bold]")
    console.print(escape(severity_summary(issues)), highlight=False)
    if not issues:
        console.print("[green]✓ no issues[/green]")
        return
    if source and not (header and str(source) in header):
        # The CLI headers already name the source; print it once, not twice.
        console.print(f"[dim]source:[/dim] {escape(str(source))}", highlight=False)

    table = Table()
    table.add_column("severity", no_wrap=True)
    table.add_column("code", overflow="fold")
    table.add_column("table", overflow="fold")
    table.add_column("row", no_wrap=True, justify="right")
    table.add_column("message", ratio=1, overflow="fold")
    for group in group_issues(issues):
        if group.groupable and not all:
            table.add_row(
                str(group.severity),
                group.code,
                f"{len(group.table_names)} tables",
                "",
                group.summary(),
            )
            continue
        for issue in group.issues:
            table.add_row(
                str(issue.severity),
                issue.code,
                str(issue.table or ""),
                "" if issue.row is None else str(issue.row),
                strip_source_prefix(issue.message, source),
            )
    console.print(table)


def _issue_to_payload(issue: Any) -> dict[str, Any]:
    """Flatten an :class:`~corral.reports.Issue` to a JSON-safe dict."""
    fields = ("severity", "category", "code", "message", "table", "column", "row", "fix_hint", "extra")
    return {f: _scalar(getattr(issue, f, None)) for f in fields}


def _scalar(value: Any) -> Any:
    """Convert StrEnum / Path / dataclass values into JSON-safe scalars."""
    if value is None:
        return None
    if hasattr(value, "value"):  # StrEnum
        return value.value
    if isinstance(value, dict):
        return {k: _scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scalar(v) for v in value]
    return value
