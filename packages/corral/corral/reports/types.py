"""Foundational value types for the corral validation framework.

Every validation path in corral — schema, foreign-key, structural,
sync-state, and the domain quality plugins discovered via entry
points — returns the *same* :class:`ValidationReport` object. A single
report can be rendered to rich-console, JSON, or HTML
(:mod:`corral.reports.render`) without changing the producer side.

The contract is deliberately narrow:

- :class:`Severity` and :class:`Category` are ``str`` enums so they
  serialize to stable, human-readable JSON without custom encoders.
- :class:`Issue` is a *frozen* dataclass so a report can be safely
  held after the underlying data has changed.
- :class:`ValidationReport` is *mutable* so multiple checks can
  accumulate into it during a single run.

This module has zero engine, spec, or I/O dependencies — it's pure
data types + a couple of query helpers. Validators consume it; the
renderers in :mod:`corral.reports.render` format it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = ["Category", "Issue", "Severity", "ValidationReport"]


class Severity(StrEnum):
    """Severity of a single validation finding.

    The order matters: renderers display issues grouped
    ``ERROR -> WARNING -> INFO``. The ``str`` base (rather than ``int``)
    was chosen for JSON-serialisability — a validation report dumped to
    JSON is the same on Python 3.11 and Python 3.13 with no custom
    encoder, and ``severity == "error"`` works for users who read the
    JSON without re-importing the enum.

    For ordering, use :func:`severity_rank`.

    Severity is *orthogonal* to :class:`Category`. A data-quality
    finding carries ``Category.DATA_QUALITY`` AND one of the three
    severity values — a missing speed limit is typically ``INFO``,
    while a 70mph residential street is ``WARNING``. The pre-1.0
    ``Severity.DATA_QUALITY`` value conflated the two dimensions and
    was removed; quality rules now pick the real severity that matches
    how urgently the finding wants attention.

    Attributes:
        ERROR: Spec or contract violation. The data is wrong; downstream
            consumers should not trust it.
        WARNING: Likely problem that does not break correctness. The
            ``OutOfSyncWarning`` family lives here. Also the default
            severity for a suspicious data-quality finding.
        INFO: Informational only. No action required. Also the default
            severity for an awareness-only data-quality finding.

    Examples:
        >>> Severity("error") is Severity.ERROR
        True
        >>> Severity.WARNING.value
        'warning'
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Display + sort order for renderers and any caller that needs to surface
# the "loudest" issue first. Keep in sync with the docstring above.
_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.ERROR,
    Severity.WARNING,
    Severity.INFO,
)

#: How many issues to enumerate in :meth:`ValidationReport._repr_html_`
#: before collapsing the rest into a "…+N more issues" note.
_REPORT_ISSUE_PREVIEW = 10

#: Per-message truncation cap inside the notebook card so a stack-trace
#: -shaped message doesn't blow up cell height.
_REPORT_MESSAGE_TRUNCATE = 120


def severity_rank(severity: Severity) -> int:
    """Return the display rank for ``severity`` (0 = highest priority).

    Args:
        severity: The severity to rank.

    Returns:
        Integer index in the canonical display order
        (ERROR=0, WARNING=1, INFO=2).

    Examples:
        >>> severity_rank(Severity.ERROR)
        0
        >>> severity_rank(Severity.INFO)
        2
    """
    return _SEVERITY_ORDER.index(severity)


class Category(StrEnum):
    """Broad category of a validation finding.

    Used to group findings in reports and to filter the JSON / HTML
    output by rule family. Categories map to the validation modules
    that produce them — schema checks emit ``SCHEMA``, FK checks emit
    ``FOREIGN_KEY``, and so on.

    Attributes:
        SCHEMA: Field-level constraints — type, required, enum, regex,
            min/max. Produced by :mod:`corral.validation.schema_check`.
        STRUCTURAL: Package-level structure — missing required table,
            extra unknown table, missing file on disk. Produced by
            :mod:`corral.validation.structural`.
        FOREIGN_KEY: Cross-table referential integrity. Produced by
            :mod:`corral.validation.foreign_keys`.
        SYNC_STATE: A previously validated FK is now stale because one
            side has been mutated since the last check. Produced by
            :mod:`corral.validation.sync_state`.
        DATA_QUALITY: A configurable quality rule (e.g. ``high-speed
            on residential road``). Produced by quality plugins
            registered under the ``corral.quality.rules`` entry
            point — see :mod:`corral.quality`.

    Examples:
        >>> Category("foreign_key") is Category.FOREIGN_KEY
        True
    """

    SCHEMA = "schema"
    STRUCTURAL = "structural"
    FOREIGN_KEY = "foreign_key"
    SYNC_STATE = "sync_state"
    DATA_QUALITY = "data_quality"


@dataclass(frozen=True)
class Issue:
    """A single validation finding.

    Frozen so a report can be safely held by callers (or shown in a UI)
    after the underlying data changes — once an issue is recorded, it
    snapshots the failure. Hashable for the same reason, so consumers
    can dedupe via set membership without writing a custom ``__hash__``.

    The ``code`` field is a stable, dotted, namespaced identifier — the
    string callers grep tracebacks for and filter reports by. Examples:
    ``"schema.required"``, ``"schema.enum"``, ``"fk.missing_target"``,
    ``"structural.missing_table"``, ``"sync.fk_stale"``,
    ``"quality.high_speed_residential"``. The leading namespace
    (``schema.``, ``fk.``, ``structural.``, ``sync.``, ``quality.``)
    mirrors :class:`Category` and keeps codes greppable per rule family.

    The ``message`` field MUST name the input that broke — table,
    column, row, value — not a generic phrase. The v0.3 line of bugs
    where "FK violation" was the entire user-facing string is what this
    contract is designed to avoid.

    The optional ``fix_hint`` is a single short sentence telling the
    user what to do. Renderers display it on a second line when present.

    Attributes:
        severity: How bad it is.
        category: Which rule family produced it.
        code: Stable dotted identifier (e.g. ``"schema.required"``).
        message: Human-readable, names the input that broke.
        table: Table name, or ``None`` for cross-cutting / structural
            issues that don't belong to a single table.
        column: Column / field name, or ``None`` if not field-specific.
        row: Zero-based row index, or ``None`` if not row-specific.
        fix_hint: Optional one-sentence remediation hint.
        extra: Adapter-specific extras — geo coordinates, target table
            for FK violations, etc. Renderers may surface known keys.

    Examples:
        >>> issue = Issue(
        ...     severity=Severity.ERROR,
        ...     category=Category.FOREIGN_KEY,
        ...     code="fk.missing_target",
        ...     message="link row 12: from_node_id=99 not found in node.node_id",
        ...     table="link",
        ...     column="from_node_id",
        ...     row=12,
        ...     fix_hint="Add a node row with node_id=99, or remove the link.",
        ... )
        >>> issue.severity
        <Severity.ERROR: 'error'>
        >>> issue.row
        12
    """

    severity: Severity
    category: Category
    code: str
    message: str
    table: str | None = None
    column: str | None = None
    row: int | None = None
    fix_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash on the stable identity fields, ignoring the mutable ``extra`` dict.

        ``frozen=True`` normally generates ``__hash__``, but the ``extra``
        dict field is mutable and unhashable. Hashing on the identity
        fields (everything except ``extra``) means equal issues — same
        severity, location, code, message, fix hint — collapse in a
        ``set()``. This matches how a human would dedupe a report: two
        findings with the same code+message at the same row are the
        same finding, regardless of which validator's debug payload they
        happen to carry.
        """
        return hash(
            (
                self.severity,
                self.category,
                self.code,
                self.message,
                self.table,
                self.column,
                self.row,
                self.fix_hint,
            )
        )


@dataclass
class ValidationReport:
    """Result of one or more validation passes over a data package.

    Mutable — multiple checks (schema + FK + structural + sync_state +
    data_quality) build it up over a single run by calling
    :meth:`add_issue` or :meth:`add`. When the run is complete, hand it
    to a renderer (:func:`~corral.validation.render_rich`,
    :func:`~corral.validation.render_json`, or the HTML renderer in
    :mod:`corral.reports.render`).

    The report carries the spec version and source identifier so the
    rendered output is self-describing — a saved JSON or HTML report
    tells you which spec it was validated against and where the data
    came from.

    Attributes:
        spec_version: The spec version this run was validated against
            (e.g. ``"0.97"``). Optional — populated by the caller.
        source: Path / URL identifier of the package being validated.
            Used by renderers in the header.
        issues: All findings recorded so far. Order is insertion order;
            renderers re-sort by severity.
        metadata: Free-form metadata about the run — engine name,
            scope, timestamps. Echoed in the JSON output.
        created_at: When the report was constructed (timezone-naive
            local time, matching ``datetime.now()``).

    Examples:
        >>> report = ValidationReport(spec_version="0.97", source="leavenworth.gmns")
        >>> issue = report.add(
        ...     severity=Severity.ERROR,
        ...     category=Category.SCHEMA,
        ...     code="schema.required",
        ...     message="link.from_node_id row 0: value is null",
        ...     table="link",
        ... )
        >>> report.has_errors
        True
        >>> report.count(Severity.ERROR)
        1
    """

    spec_version: str | None = None
    source: str | None = None
    issues: list[Issue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    # -- Mutation surface ---------------------------------------------------

    def add_issue(self, issue: Issue) -> None:
        """Append an already-constructed :class:`Issue` to the report.

        Args:
            issue: The issue to record.

        Examples:
            >>> report = ValidationReport()
            >>> report.add_issue(Issue(
            ...     severity=Severity.WARNING,
            ...     category=Category.SYNC_STATE,
            ...     code="sync.fk_stale",
            ...     message="link FK to node is stale",
            ... ))
            >>> len(report.issues)
            1
        """
        self.issues.append(issue)

    def add(
        self,
        *,
        severity: Severity,
        category: Category,
        code: str,
        message: str,
        **kw: Any,
    ) -> Issue:
        """Construct an :class:`Issue`, append it, and return it.

        This is the convenience builder validators usually call directly
        — the returned issue is handy when the validator wants to log
        or annotate the same finding elsewhere.

        Args:
            severity: Severity of the finding.
            category: Category of the finding.
            code: Stable dotted identifier (e.g. ``"schema.required"``).
            message: Human-readable description that names the broken input.
            **kw: Any of ``table``, ``column``, ``row``, ``fix_hint``,
                ``extra`` from :class:`Issue`.

        Returns:
            The newly created (and stored) issue.

        Examples:
            >>> report = ValidationReport()
            >>> issue = report.add(
            ...     severity=Severity.ERROR,
            ...     category=Category.SCHEMA,
            ...     code="schema.required",
            ...     message="link.from_node_id row 0: value is null",
            ...     table="link",
            ...     column="from_node_id",
            ...     row=0,
            ... )
            >>> issue.code
            'schema.required'
        """
        issue = Issue(
            severity=severity,
            category=category,
            code=code,
            message=message,
            **kw,
        )
        self.add_issue(issue)
        return issue

    # -- Query surface ------------------------------------------------------

    def by_severity(self, severity: Severity) -> list[Issue]:
        """Return all issues at the given severity, preserving insertion order.

        Examples:
            >>> r = ValidationReport()
            >>> r.add(severity=Severity.ERROR, category=Category.SCHEMA,
            ...       code="schema.required", message="x")
            Issue(severity=<Severity.ERROR: 'error'>, ...)
            >>> len(r.by_severity(Severity.ERROR))
            1
            >>> r.by_severity(Severity.WARNING)
            []
        """
        return [i for i in self.issues if i.severity is severity]

    def by_category(self, category: Category) -> list[Issue]:
        """Return all issues in the given category, preserving insertion order.

        Examples:
            >>> r = ValidationReport()
            >>> r.add(severity=Severity.ERROR, category=Category.FOREIGN_KEY,
            ...       code="fk.missing_target", message="x")
            Issue(...)
            >>> [i.code for i in r.by_category(Category.FOREIGN_KEY)]
            ['fk.missing_target']
        """
        return [i for i in self.issues if i.category is category]

    def by_table(self, table: str) -> list[Issue]:
        """Return all issues attached to ``table``, preserving insertion order.

        Cross-cutting issues (``Issue.table is None``) are excluded —
        they would match every table query otherwise.

        Examples:
            >>> r = ValidationReport()
            >>> r.add(severity=Severity.ERROR, category=Category.SCHEMA,
            ...       code="schema.required", message="x", table="link")
            Issue(...)
            >>> r.add(severity=Severity.ERROR, category=Category.STRUCTURAL,
            ...       code="structural.missing_table", message="y")  # no table
            Issue(...)
            >>> [i.table for i in r.by_table("link")]
            ['link']
        """
        return [i for i in self.issues if i.table == table]

    def count(self, severity: Severity | None = None) -> int:
        """Count issues, optionally filtered to a single severity.

        Args:
            severity: If given, count only issues at this severity.
                ``None`` (the default) counts every issue.

        Examples:
            >>> r = ValidationReport()
            >>> r.add(severity=Severity.ERROR, category=Category.SCHEMA,
            ...       code="schema.required", message="x")
            Issue(...)
            >>> r.add(severity=Severity.WARNING, category=Category.SYNC_STATE,
            ...       code="sync.fk_stale", message="y")
            Issue(...)
            >>> r.count()
            2
            >>> r.count(Severity.ERROR)
            1
        """
        if severity is None:
            return len(self.issues)
        return sum(1 for i in self.issues if i.severity is severity)

    # -- Verdict properties -------------------------------------------------

    @property
    def has_errors(self) -> bool:
        """``True`` when the report contains at least one ``ERROR``.

        Examples:
            >>> r = ValidationReport()
            >>> r.has_errors
            False
        """
        return any(i.severity is Severity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        """``True`` when the report contains at least one ``WARNING``.

        Examples:
            >>> r = ValidationReport()
            >>> r.has_warnings
            False
        """
        return any(i.severity is Severity.WARNING for i in self.issues)

    @property
    def is_clean(self) -> bool:
        """``True`` when no errors AND no warnings are present.

        ``INFO`` issues do *not* break ``is_clean`` — they're
        informational, and surfaced for awareness. Data-quality findings
        (``Category.DATA_QUALITY``) carry whichever severity the rule
        chose; ``WARNING`` ones flip ``is_clean`` to ``False``, ``INFO``
        ones don't.

        Examples:
            >>> r = ValidationReport()
            >>> r.is_clean
            True
            >>> r.add(severity=Severity.INFO, category=Category.STRUCTURAL,
            ...       code="structural.optional_missing", message="x")
            Issue(...)
            >>> r.is_clean
            True
        """
        return not (self.has_errors or self.has_warnings)

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict snapshot of the report.

        Schema:

        .. code-block:: python

            {
                "report_version": "1",
                "spec_version": "0.97" | None,
                "source": str | None,
                "created_at": "ISO-8601",
                "metadata": {...},
                "summary": {"error": N, "warning": N, "info": N,
                            "data_quality": N, "is_clean": bool},
                "issues": [
                    {"severity": "error", "category": "schema",
                     "code": "...", "message": "...", "table": ..., ...},
                    ...
                ],
            }

        ``Enum`` values flatten to their ``.value`` strings; ``datetime``
        flattens via :meth:`datetime.isoformat`. The key set is stable;
        downstream consumers (the HTML renderer, the MCP
        server, the FastAPI server) rely on it.

        Examples:
            >>> r = ValidationReport(spec_version="0.97", source="x.gmns")
            >>> d = r.to_dict()
            >>> d["report_version"]
            '1'
            >>> d["summary"]["is_clean"]
            True
        """
        return {
            "report_version": "1",
            "spec_version": self.spec_version,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
            "summary": {
                "error": self.count(Severity.ERROR),
                "warning": self.count(Severity.WARNING),
                "info": self.count(Severity.INFO),
                # Category-based count — kept in the summary now that
                # DATA_QUALITY is a category, not a severity. Preserves
                # the JSON shape pre-1.0 report consumers grew up on.
                "data_quality": len(self.by_category(Category.DATA_QUALITY)),
                "is_clean": self.is_clean,
            },
            "issues": [_issue_to_dict(i) for i in self.issues],
        }

    def to_json(self, path: str | Path | None = None, *, indent: int = 2) -> str:
        """Return the report as a JSON string. Optionally write to ``path``.

        Thin wrapper around :func:`json.dumps` on :meth:`to_dict`.

        Args:
            path: Optional file path. When given, the rendered JSON is
                also written to ``path`` (parent dirs created if needed).
                The string is still returned so chained
                ``report.to_json("r.json")`` works.
            indent: ``json.dumps`` indent setting. Default 2.

        Examples:
            >>> import json
            >>> r = ValidationReport()
            >>> data = json.loads(r.to_json())              # in-memory
            >>> data["summary"]["is_clean"]
            True
            >>> import tempfile, pathlib
            >>> with tempfile.TemporaryDirectory() as d:    # write-to-file
            ...     out = pathlib.Path(d) / "r.json"
            ...     _ = r.to_json(out)
            ...     out.is_file()
            True
        """
        import json

        rendered = json.dumps(self.to_dict(), indent=indent)
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        return rendered

    def to_rich(self) -> str:
        """Return the rich-console rendering of this report.

        Convenience wrapper around
        :func:`corral.reports.render_rich`. Kept here so that
        ``str(report)`` round-trips through the rich renderer without
        the caller importing :mod:`corral.reports.render`.

        Examples:
            >>> r = ValidationReport(source="empty.gmns")
            >>> "empty.gmns" in r.to_rich()
            True
        """
        # Local import to keep this module dependency-free at import time
        # — `rich` is only loaded when someone actually renders.
        from .render import render_rich

        return render_rich(self)

    def to_html(
        self,
        path: str | Path | None = None,
        *,
        title: str | None = None,
        include_map: bool = True,
    ) -> str:
        """Return the interactive single-file HTML rendering of this report.

        Shortcut for :func:`corral.reports.render_html`. See its
        docstring for the offline-mode trade-off around the optional
        Vega-Lite map section.

        Args:
            path: Optional file path. When given, the rendered HTML is
                also written to ``path`` (parent directories created if
                needed). The string is still returned so chained
                ``report.to_html("r.html").splitlines()`` works.
            title: Optional override for the ``<title>`` and ``<h1>``.
            include_map: If ``False``, skip the map section even when
                geo-located issues are present.

        Returns:
            A single self-contained HTML string.

        Examples:
            >>> r = ValidationReport(source="empty.gmns")
            >>> html = r.to_html()                              # in-memory
            >>> html.lstrip().startswith("<!DOCTYPE html>")
            True
            >>> import tempfile, pathlib
            >>> with tempfile.TemporaryDirectory() as d:        # write-to-file
            ...     out = pathlib.Path(d) / "r.html"
            ...     _ = r.to_html(out)
            ...     out.is_file()
            True
        """
        # Local import for the same reason as ``to_rich`` — keep this
        # module free of jinja2 / render-module imports at module load.
        from .render import render_html

        html = render_html(self, title=title, include_map=include_map)
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
        return html

    def __str__(self) -> str:
        """Alias for :meth:`to_rich` — usable from ``print(report)``."""
        return self.to_rich()

    def _repr_html_(self) -> str:
        """Render a compact Jupyter-friendly card summarizing the report.

        Header carries the source locator + spec version; the body
        shows per-severity counts and the first ten issues as a
        ``severity | code | table | message`` table, with a "…+N more
        issues" note when more are present. For the full standalone
        HTML report (with the issue table + optional map), use
        :meth:`to_html`.

        Examples:
            >>> from corral.reports import Category, Severity, ValidationReport
            >>> r = ValidationReport(source="empty.gmns")
            >>> r._repr_html_().startswith("<div")
            True
            >>> _ = r.add(severity=Severity.ERROR, category=Category.SCHEMA,
            ...           code="schema.required", message="x", table="t")
            >>> "ERROR" in r._repr_html_()
            True
        """
        # Local import — the notebook helper is stdlib-only but we
        # don't want the load-time edge from reports → notebook.
        from corral.notebook import card, escape, kv_line, small_table, truncation_note

        # Per-severity counts in canonical display order — matches the
        # CLI / rich renderer so the visual hierarchy is the same.
        counts: list[tuple[str, object]] = []
        for sev in _SEVERITY_ORDER:
            n = self.count(sev)
            label = sev.name  # ERROR / WARNING / INFO
            counts.append((label, n))
        if self.spec_version:
            counts.append(("spec", self.spec_version))

        preview = self.issues[:_REPORT_ISSUE_PREVIEW]
        rows: list[list[object]] = []
        for issue in preview:
            msg = issue.message
            if len(msg) > _REPORT_MESSAGE_TRUNCATE:
                msg = msg[: _REPORT_MESSAGE_TRUNCATE - 1] + "…"
            rows.append([issue.severity.name, issue.code, issue.table or "—", msg])
        table_html = small_table(["severity", "code", "table", "message"], rows)
        note_html = truncation_note(max(0, len(self.issues) - len(preview)), "issues")
        # ``escape`` is imported but not directly used here — the helpers
        # escape everything they touch. Kept in the import for symmetry
        # with the other call sites and so a future inline cell stays
        # safe-by-default.
        _ = escape

        body = kv_line(counts) + table_html + note_html
        subtitle = self.source or ""
        return card("ValidationReport", body, subtitle=subtitle)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue_to_dict(issue: Issue) -> dict[str, Any]:
    """Flatten one :class:`Issue` to a JSON-serializable dict.

    ``dataclasses.asdict`` does most of the work; we only need to coerce
    the two ``Enum`` fields to their ``.value`` strings so they survive
    a ``json.dumps`` round-trip without a custom encoder.
    """
    raw = asdict(issue)
    raw["severity"] = issue.severity.value
    raw["category"] = issue.category.value
    return raw
