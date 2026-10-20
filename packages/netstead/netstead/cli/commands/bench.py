"""``netstead bench`` — read/validate/connectivity timing (issue #86)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer
from corral.cli.render import render_dict

from netstead import Network

from .._helpers import resolve_engine

_T = TypeVar("_T")

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``bench`` command on ``app``."""

    @app.command(name="bench")
    def bench(
        source: Path = typer.Argument(..., help="Path/URL to a GMNS network."),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Run read/validate/connectivity benchmarks; print timings.

        Phases timed (each via time.perf_counter):

        * ``load`` — :meth:`Network.from_source`.
        * ``validate`` — :meth:`Network.validate` (structural/schema only).
        * ``links_count`` + ``nodes_count`` — trivial materialisation sanity.
        * ``is_connected`` — full graph index build + component count
          (skipped silently when the ``[graph]`` extra is missing).

        Output is a dict with ``phases`` (list of ``{phase, seconds}``) and
        a ``total_seconds`` summary. ``--json`` writes the dict to stdout for
        machine consumption; otherwise rendered via the standard rich panel.
        """
        import time

        eng = resolve_engine(engine)

        timings: list[dict] = []

        def _time(phase: str, fn: Callable[[], _T]) -> _T:
            t0 = time.perf_counter()
            result = fn()
            timings.append({"phase": phase, "seconds": round(time.perf_counter() - t0, 4)})
            return result

        net = _time("load", lambda: Network.from_source(source, engine=eng))
        _time("validate", lambda: net.validate(foreign_keys=False, sync_state=False))
        _time("links_count", lambda: net.links.count())
        _time("nodes_count", lambda: net.nodes.count())
        # Connectivity needs the [graph] extra (scipy). Skip silently if not available.
        try:
            from netstead.semantics import is_connected

            _time("is_connected", lambda: is_connected(net))
        except ImportError:
            timings.append({"phase": "is_connected", "seconds": None, "skipped": "scipy not installed"})

        total = round(sum(t["seconds"] for t in timings if t.get("seconds") is not None), 4)
        data = {
            "source": str(source),
            "engine": type(eng).__name__,
            "total_seconds": total,
            "phases": timings,
        }
        render_dict(data, json_out=json_out, title=f"bench: {source}")

    @app.command(name="bench-suite")
    def bench_suite(
        sizes: str = typer.Option("S,M", "--sizes", help="Size tiers to sweep (comma-separated: S,M,L)."),
        operations: str = typer.Option(
            "network_create,selection,viz_pack",
            "--operations",
            help="Operation families to run (comma-separated).",
        ),
        out_format: str = typer.Option("json", "--format", help="Output format: json | csv | md."),
        output: Path = typer.Option(None, "--output", "-o", help="Write to this file instead of stdout."),
        repeats: int = typer.Option(3, "--repeats", help="Macro timing repeats (reported as median + min)."),
        warm_repeats: int = typer.Option(20, "--warm-repeats", help="Repeats for the warm selection number."),
        run_osm: bool = typer.Option(False, "--run-osm", help="Include live OSM builds (hits Overpass)."),
        fixtures: Path = typer.Option(None, "--fixtures", help="Override path to a fixtures.toml."),
        commit: str = typer.Option(None, "--commit", help="Git SHA to stamp into the env block."),
    ) -> None:
        """Run the DuckDB-path benchmark suite (create / selection / viz-pack).

        Sweeps the requested size tiers over the three operation families from
        the benchmarking-suite scope, capturing wall-time and peak memory, and
        emits one structured document. DuckDB (via ibis) is the single compute
        engine; pandas / polars / pyarrow are I/O formats, so there is no engine
        axis — the suite measures the DuckDB path and the binary store format.

        ``--format json`` (default) emits the canonical regression document;
        ``csv`` flattens the results; ``md`` renders the advertising table with
        an environment stamp and the standing honesty caveats. Fixture
        definitions live in ``benchmarks/fixtures.toml``; the L tier is resolved
        from the ``NETSTEAD_BENCH_LARGE`` / ``NETSTEAD_BENCH_LARGE_BBOX`` env vars
        (never committed).
        """
        # Imported here (not at module top) so `netstead bench` stays importable
        # without the suite's optional-feature deps, and so the import-linter
        # `netstead.cli -> netstead.osm` contract holds (the suite reaches osm via a
        # dynamic import, invisible to the static-import scan).
        from netstead.bench import report, suite

        document = suite.run_suite(
            sizes=tuple(s.strip().upper() for s in sizes.split(",") if s.strip()),
            operations=tuple(op.strip() for op in operations.split(",") if op.strip()),
            repeats=repeats,
            warm_repeats=warm_repeats,
            run_osm=run_osm,
            fixtures_path=str(fixtures) if fixtures else None,
            commit=commit,
        )

        renderers = {
            "json": lambda doc: report.render_json(doc),
            "csv": lambda doc: report.render_csv(doc["results"]),
            "md": lambda doc: report.render_markdown(doc),
        }
        if out_format not in renderers:
            raise typer.BadParameter(f"unknown --format {out_format!r}; expected json | csv | md")
        rendered = renderers[out_format](document)

        if output is not None:
            output.write_text(rendered, encoding="utf-8")
            typer.secho(f"wrote {out_format} results to {output}", fg="green", err=True)
        else:
            typer.echo(rendered)
