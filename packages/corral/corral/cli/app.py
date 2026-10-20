"""Generic corral CLI — typer app + ``build_app()`` factory.

Two entry points compose on the same factory:

* ``corral = corral.cli.app:app`` ships only the generic
  commands (validate, info — extended in Phase 4 task 4.2 with convert,
  scope, describe).
* ``netstead = netstead.cli.app:app`` calls :func:`build_app` to get a
  fresh Typer with the generic commands, then layers GMNS-aware ones
  on top (``quality``, GMNS-aware ``info``, etc.).

Reusing the factory keeps both CLIs identical for the validate/info
contract — same flags, same ``--json`` shape, same approval handling.

Every command accepts ``--json`` (so an agent gets a single
machine-readable document on stdout) and ``--yes/-y`` (auto-approve
gated ops, see :mod:`corral.cli.prompts`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import typer

from corral.dataset import Package, infer_write_format
from corral.operations import ApprovalRequired

from .prompts import run_with_approval
from .render import console, render_dict, render_issues

__all__ = ["app", "build_app"]

logger = logging.getLogger(__name__)


def build_app() -> typer.Typer:
    """Return a fresh :class:`typer.Typer` with the generic corral commands.

    Used by both the ``corral`` and ``netstead`` entry points so the
    two CLIs share the same validate/info contracts. Domain extensions
    (netstead) call this then attach their own commands; the returned
    app is otherwise independent (calling :func:`build_app` twice
    yields two unrelated apps).
    """
    typer_app = typer.Typer(
        no_args_is_help=True,
        rich_markup_mode="rich",
        context_settings={"help_option_names": ["-h", "--help"]},
        help=("corral — generic Frictionless data-package CLI. Add --json to any command for machine-readable output."),
    )

    @typer_app.command(name="validate")
    def validate(
        source: Path = typer.Argument(..., help="Path / URL to a data package (datapackage.json or table directory)."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout instead of rich panels."),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Auto-approve any gated ops (alternative to CORRAL_AUTO_APPROVE=1)."
        ),
    ) -> None:
        """Validate ``source`` against its declared Frictionless schema.

        Runs structural + schema + foreign-key + sync-state validators
        and renders the resulting :class:`~corral.reports.ValidationReport`.
        Exit code is non-zero iff any ERROR-severity issue is recorded.
        """
        package = Package.from_source(source)
        try:
            report = run_with_approval(package.validate, yes=yes)
        except ApprovalRequired:
            console.print("[yellow]validation declined — exiting 1.[/yellow]")
            raise typer.Exit(code=1) from None
        render_issues(report.issues, json_out=json_out, header=f"validation report: {source}")
        # Exit non-zero on hard errors so CI / scripts can branch.
        if any(getattr(i, "severity", None) and i.severity.value == "error" for i in report.issues):
            raise typer.Exit(code=1)

    @typer_app.command(name="info")
    def info(
        source: Path = typer.Argument(..., help="Path / URL to a data package."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout instead of rich panels."),
    ) -> None:
        """Print metadata about ``source`` — table list, row counts, spec name."""
        package = Package.from_source(source)
        data = {
            "name": package.spec.name,
            "source": str(source),
            "engine": type(package.engine).__name__,
            "table_count": len(package.tables),
            "tables": _table_summary(package),
        }
        render_dict(data, json_out=json_out, title=f"info: {source}")

    @typer_app.command(name="convert")
    def convert(
        source: Path = typer.Argument(..., help="Source data package path/URL."),
        dest: Path = typer.Argument(..., help="Destination path."),
        fmt: str = typer.Option(
            None,
            "--format",
            "-f",
            help="Output format (csv/parquet/duckdb/zipcsv). Default: infer from dest extension; fall back to parquet.",
        ),
        engine: str = typer.Option(
            None,
            "--engine",
            help="Engine: DuckDB (the only compute engine).",
        ),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve gated ops."),
    ) -> None:
        """Convert a data package from any supported format to another.

        Loads ``source`` (any format corral can read) and writes the
        same logical package to ``dest`` in the target format. Output
        format is taken from ``--format`` when supplied, otherwise
        inferred from the ``dest`` extension; an extension-less ``dest``
        defaults to partitioned parquet (architecture §6.1).
        """
        resolved_engine = _resolve_engine_or_exit(engine)
        package = Package.from_source(source, engine=resolved_engine)

        # Wrap the write through run_with_approval so a cost-model gate
        # on a large conversion surfaces as a prompt instead of an
        # uncaught exception. ``Package.write`` doesn't currently raise
        # ApprovalRequired itself, but routing through the helper keeps
        # the gating seam in place for when it grows one.
        try:
            run_with_approval(package.write, dest, format=fmt, yes=yes)
        except ApprovalRequired:
            console.print("[yellow]conversion declined — exiting 1.[/yellow]")
            raise typer.Exit(code=1) from None

        # The format Package.write just used, from the writer's own rules.
        effective_format = infer_write_format(dest, fmt)
        total_rows = 0
        for table in package.tables.values():
            try:
                total_rows += int(table.count())
            except Exception as exc:  # pragma: no cover - per-table resilience
                logger.warning("convert: could not count rows for %r — %s", table.name, exc)

        summary = {
            "source": str(source),
            "dest": str(dest),
            "format": effective_format,
            "engine": type(resolved_engine).__name__,
            "table_count": len(package.tables),
            "total_rows": total_rows,
        }
        render_dict(summary, json_out=json_out, title=f"convert: {source} → {dest}")

    return typer_app


def _resolve_engine_or_exit(name: str | None) -> Any:
    """Thin CLI wrapper around :func:`corral.engines.resolve_engine`.

    Converts the public resolver's :class:`ValueError` (raised on an
    unknown engine name) into :class:`typer.BadParameter` so the CLI
    exits with a clean non-zero + help message instead of a traceback.
    Existed as a private duplicate of the registry helper until task
    PR-A (Batch A review) consolidated it.
    """
    from corral.engines import resolve_engine

    try:
        return resolve_engine(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _table_summary(package: Package) -> list[dict]:
    """Return ``[{name, rows, columns}]`` for every table in ``package``.

    Materialises one row count per table; if a table is lazy and large,
    the engine pushes the count down to its backend so this remains
    cheap on regional-scale parquet sources.
    """
    out: list[dict] = []
    for name, table in package.tables.items():
        try:
            row_count = table.count()
            col_list = table.columns()
        except Exception as exc:  # pragma: no cover - per-table resilience
            logger.warning("info: skipping table %r — %s", name, exc)
            continue
        out.append({"name": name, "rows": row_count, "columns": col_list})
    return out


# The default ``corral`` entry point app — module-level so the
# console script resolves it as `corral.cli.app:app`. Build it
# once; consumers wanting a fresh app call :func:`build_app` again.
app = build_app()


if __name__ == "__main__":  # pragma: no cover - manual smoke
    app()
