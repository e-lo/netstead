"""``netstead quality`` — run the GMNS data-quality rule pack."""

from __future__ import annotations

from pathlib import Path

import typer
from corral.cli.render import render_issues
from corral.quality import RuleConfig, run_quality

from netstead import Network
from netstead.quality import register_all

from .._extras import require_extra

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``quality`` command on ``app``."""

    @app.command(name="quality")
    def quality_cmd(
        source: Path = typer.Argument(..., help="Path / URL to a GMNS network."),
        spec_version: str = typer.Option(None, "--spec", help="Override the default GMNS spec version."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout instead of rich panels."),
    ) -> None:
        """Run the GMNS data-quality rule pack against ``source``.

        Each rule emits Severity.WARNING / INFO findings with category
        DATA_QUALITY. The output is a ValidationReport (rendered as a
        rich table or JSON document per ``--json``).
        """
        # The disconnected-components rule builds a GMNSGraph (scipy, the
        # [graph] extra) at run time; probe it up front so a slim install
        # gets the standard "install the extra" exit, not a traceback
        # from inside the rule pack.
        require_extra("netstead.graph", "graph")
        # Ensure rules are registered even when the entry point isn't
        # picked up (e.g. editable installs that skipped re-install).
        register_all()
        net = Network.from_source(source, spec_version=spec_version)
        report = run_quality(net, config={code: RuleConfig() for code in []})
        render_issues(report.issues, json_out=json_out, header=f"quality report: {source}")
        # Quality issues are WARNING/INFO by default — never exit non-zero
        # from this command. Callers wanting hard fail use --json + check
        # for non-empty issues themselves.
