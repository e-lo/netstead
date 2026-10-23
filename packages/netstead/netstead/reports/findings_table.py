"""CSV / XLSX writers for a list of validation :class:`~corral.reports.Issue` objects.

The two writers share a column set so a spreadsheet downloaded from one
format mirrors the other. The serialized columns are the
:class:`~corral.reports.Issue` dataclass fields plus an ``extra``
JSON column (so adapter-specific payload like FK targets or OSM ids
survives the round-trip without needing per-rule column expansion).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.reports import Issue

__all__ = ["write_findings_csv", "write_findings_xlsx"]


_COLUMNS: tuple[str, ...] = (
    "severity",
    "category",
    "code",
    "table",
    "column",
    "row",
    "message",
    "fix_hint",
    "extra",
)


def _row_for_issue(issue: Issue) -> list[object]:
    """Flatten one :class:`Issue` to a row matching :data:`_COLUMNS`."""
    extra_json = json.dumps(issue.extra) if issue.extra else ""
    return [
        issue.severity.value,
        issue.category.value,
        issue.code,
        issue.table if issue.table is not None else "",
        issue.column if issue.column is not None else "",
        issue.row if issue.row is not None else "",
        issue.message,
        issue.fix_hint if issue.fix_hint is not None else "",
        extra_json,
    ]


def write_findings_csv(issues: Iterable[Issue], path: str | Path) -> None:
    """Write ``issues`` to ``path`` as a UTF-8 CSV file.

    Columns: ``severity, category, code, table, column, row, message,
    fix_hint, extra``. The ``extra`` column is the issue's ``extra``
    dict serialized as JSON (empty string for an empty dict, so
    spreadsheet apps don't show ``{}``). Optional fields render as
    empty cells, not the literal string ``"None"``.

    Args:
        issues: The issues to write. Empty iterable is allowed — the
            file is created with just the header row.
        path: Output path. Parent directories are created if missing.

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from corral.reports import Category, Issue, Severity
        >>> from netstead.reports import write_findings_csv
        >>> issues = [Issue(severity=Severity.ERROR, category=Category.SCHEMA,
        ...                 code="schema.required", message="x", table="link")]
        >>> with tempfile.TemporaryDirectory() as d:
        ...     out = Path(d) / "f.csv"
        ...     write_findings_csv(issues, out)
        ...     out.is_file()
        True
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_COLUMNS)
        for issue in issues:
            writer.writerow(_row_for_issue(issue))


def write_findings_xlsx(issues: Iterable[Issue], path: str | Path) -> None:
    """Write ``issues`` to ``path`` as a single-sheet ``.xlsx`` workbook.

    Same columns as :func:`write_findings_csv`. Requires the
    ``openpyxl`` library, which ships with the ``[reports]`` extra
    (``pip install 'netstead[reports]'``).

    Args:
        issues: The issues to write. Empty iterable is allowed — the
            sheet is created with just the header row.
        path: Output path. Parent directories are created if missing.

    Raises:
        ImportError: When ``openpyxl`` is not installed. The message
            names the ``[reports]`` extra so the user knows the fix.
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(
            "netstead.reports.write_findings_xlsx requires openpyxl from the [reports] extra: "
            "pip install 'netstead[reports]'"
        ) from e

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "findings"
    ws.append(list(_COLUMNS))
    for issue in issues:
        ws.append(_row_for_issue(issue))
    wb.save(target)
