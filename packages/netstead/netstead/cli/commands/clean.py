"""``netstead clean`` — network-editing ops with rollback (issue #88).

Each subcommand wraps an op from :mod:`netstead.clean` (optional
``[clean]`` extra). The op runs inside a :class:`Session` so we
get atomic rollback + a diff to show; ``--dry-run`` rolls back
before write so the on-disk network is unchanged. Without
``--dest`` the modified network is written back over ``source``;
with ``--dest`` it goes to the given path (mirroring the
``netstead info``/``validate`` convention of leaving the source
alone when the caller asks for somewhere else).

Domain errors raised by ``netstead.clean`` (subclasses of ``CleanError``)
are converted to clean CLI exits with a red message; non-domain
exceptions propagate so contributor bugs surface as real tracebacks.
"""

from __future__ import annotations

from pathlib import Path

import typer
from corral.cli.render import render_dict

from .._extras import require_extra
from .._helpers import (
    load_network_and_session_cls,
    summarise_results,
    write_network,
)

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``clean`` sub-app on ``app``."""
    clean_app = typer.Typer(no_args_is_help=True, help="GMNS network-editing ops (requires the 'clean' extra).")
    app.add_typer(clean_app, name="clean")

    @clean_app.command(name="simplify-geometry")
    def clean_simplify(
        source: Path = typer.Argument(..., help="Path to the GMNS network to edit."),
        dest: Path = typer.Option(
            None, "--dest", help="Where to write the modified network. Default: overwrite source."
        ),
        mode: str = typer.Option("redundant_only", "--mode", help="redundant_only or douglas_peucker."),
        tolerance: float = typer.Option(0.0, "--tolerance", help="Tolerance in CRS units (douglas_peucker only)."),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change; do not write."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Simplify link geometries on the network at ``source``."""
        clean = require_extra("netstead.clean", "clean")
        # CleanError is re-exported on netstead.clean; reach it through the
        # dynamically-resolved module so the static import-linter contract
        # ``netstead.cli ↛ netstead.clean`` keeps holding.
        CleanError = clean.CleanError

        net, Session = load_network_and_session_cls(source, engine)
        try:
            with Session(net) as session:
                result = clean.simplify_geometry(net, session, mode=mode, tolerance=tolerance)
                summary = summarise_results(result, op="simplify_geometry", source=source, dest=dest, dry_run=dry_run)
                if dry_run:
                    session.rollback()
            if not dry_run:  # after the session commits, so the edited tables are its new baseline
                write_network(net, source=source, dest=dest)
        except CleanError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summary, json_out=json_out, title=f"netstead clean simplify-geometry: {source}")

    @clean_app.command(name="merge-close-nodes")
    def clean_merge(
        source: Path = typer.Argument(..., help="Path to the GMNS network to edit."),
        dest: Path = typer.Option(
            None, "--dest", help="Where to write the modified network. Default: overwrite source."
        ),
        threshold_m: float = typer.Option(5.0, "--threshold-m", help="Distance threshold in node CRS units."),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change; do not write."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Merge node pairs within ``--threshold-m`` of each other; rewrite incident links."""
        clean = require_extra("netstead.clean", "clean")
        # CleanError is re-exported on netstead.clean; reach it through the
        # dynamically-resolved module so the static import-linter contract
        # ``netstead.cli ↛ netstead.clean`` keeps holding.
        CleanError = clean.CleanError

        net, Session = load_network_and_session_cls(source, engine)
        try:
            with Session(net) as session:
                results = clean.merge_close_nodes(net, session, threshold_m=threshold_m)
                summary = summarise_results(results, op="merge_close_nodes", source=source, dest=dest, dry_run=dry_run)
                if dry_run:
                    session.rollback()
            if not dry_run:  # after the session commits, so the edited tables are its new baseline
                write_network(net, source=source, dest=dest)
        except CleanError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summary, json_out=json_out, title=f"netstead clean merge-close-nodes: {source}")

    @clean_app.command(name="remove-orphans")
    def clean_remove_orphans(
        source: Path = typer.Argument(..., help="Path to the GMNS network to edit."),
        dest: Path = typer.Option(
            None, "--dest", help="Where to write the modified network. Default: overwrite source."
        ),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change; do not write."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Drop nodes with no incident links."""
        clean = require_extra("netstead.clean", "clean")
        # CleanError is re-exported on netstead.clean; reach it through the
        # dynamically-resolved module so the static import-linter contract
        # ``netstead.cli ↛ netstead.clean`` keeps holding.
        CleanError = clean.CleanError

        net, Session = load_network_and_session_cls(source, engine)
        try:
            with Session(net) as session:
                result = clean.remove_orphans(net, session)
                summary = summarise_results(result, op="remove_orphans", source=source, dest=dest, dry_run=dry_run)
                if dry_run:
                    session.rollback()
            if not dry_run:  # after the session commits, so the edited tables are its new baseline
                write_network(net, source=source, dest=dest)
        except CleanError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summary, json_out=json_out, title=f"netstead clean remove-orphans: {source}")

    @clean_app.command(name="recompute-lengths")
    def clean_recompute_lengths(
        source: Path = typer.Argument(..., help="Path to the GMNS network to edit."),
        dest: Path = typer.Option(
            None, "--dest", help="Where to write the modified network. Default: overwrite source."
        ),
        geodesic: bool = typer.Option(False, "--geodesic", help="Compute haversine length in meters (WGS84)."),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change; do not write."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Recompute ``link.length`` from the inline geometry column."""
        clean = require_extra("netstead.clean", "clean")
        # CleanError is re-exported on netstead.clean; reach it through the
        # dynamically-resolved module so the static import-linter contract
        # ``netstead.cli ↛ netstead.clean`` keeps holding.
        CleanError = clean.CleanError

        net, Session = load_network_and_session_cls(source, engine)
        try:
            with Session(net) as session:
                result = clean.recompute_lengths(net, session, geodesic=geodesic)
                summary = summarise_results(result, op="recompute_lengths", source=source, dest=dest, dry_run=dry_run)
                if dry_run:
                    session.rollback()
            if not dry_run:  # after the session commits, so the edited tables are its new baseline
                write_network(net, source=source, dest=dest)
        except CleanError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summary, json_out=json_out, title=f"netstead clean recompute-lengths: {source}")
