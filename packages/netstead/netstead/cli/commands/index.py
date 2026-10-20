"""``netstead index`` — spatial + graph index build/status/drop (issue #88).

Sidecars land under ``<source.parent>/_netstead_indexes/`` per
:func:`netstead.indexes.cache.cache_path`. ``build`` content-hashes
the link table (and node table for the graph index) so a re-build
over identical data is a cheap no-op.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import typer
from corral.cli.render import render_dict

from netstead import Network

from .._extras import require_extra
from .._helpers import list_index_sidecars, save_index_sidecar

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``index`` sub-app on ``app``."""
    index_app = typer.Typer(no_args_is_help=True, help="Spatial + graph index build/status/drop.")
    app.add_typer(index_app, name="index")

    @index_app.command(name="build")
    def index_build(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        spatial: bool = typer.Option(True, "--spatial/--no-spatial"),
        graph: bool = typer.Option(True, "--graph/--no-graph"),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Build spatial + graph indexes for the network at ``source``; cache to disk."""
        indexes = _import_indexes()
        net = Network.from_source(source)
        # Spatial index needs an inline ``geometry`` column on links;
        # auto-skip (with a logged note) rather than crashing so the
        # default ``build`` works on geometry-table-backed networks too.
        # Callers wanting to assemble + index in one go should run
        # :func:`netstead.semantics.assemble_link_geometry` first.
        skipped_spatial = False
        if spatial and "geometry" not in net.links.columns():
            spatial = False
            skipped_spatial = True
        spatial_idx, graph_idx = indexes.build_indexes(
            links=net.links,
            nodes=net.nodes if graph else None,
            spatial=spatial,
            graph=graph,
        )
        paths: list[str] = []
        if spatial_idx is not None:
            p = save_index_sidecar(indexes, source, net, "spatial", spatial_idx, kind_target="link")
            paths.append(str(p))
        if graph_idx is not None:
            p = save_index_sidecar(indexes, source, net, "graph", graph_idx, kind_target="link+node")
            paths.append(str(p))
        summary = {
            "source": str(source),
            "spatial": spatial_idx is not None,
            "graph": graph_idx is not None,
            "paths": paths,
            "skipped_spatial_no_geometry": skipped_spatial,
        }
        render_dict(summary, json_out=json_out, title=f"netstead index build: {source}")

    @index_app.command(name="status")
    def index_status(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Report which index sidecars exist next to ``source``."""
        paths = list_index_sidecars(source)
        summary = {
            "source": str(source),
            "spatial": any(".spatial." in p.name for p in paths),
            "graph": any(".graph." in p.name for p in paths),
            "paths": [str(p) for p in paths],
        }
        render_dict(summary, json_out=json_out, title=f"netstead index status: {source}")

    @index_app.command(name="drop")
    def index_drop(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Delete cached spatial + graph sidecars next to ``source``."""
        paths = list_index_sidecars(source)
        removed: list[str] = []
        for p in paths:
            try:
                p.unlink()
                removed.append(str(p))
            except OSError:  # pragma: no cover - race; surface but don't crash
                continue
        render_dict(
            {"source": str(source), "removed": removed, "count": len(removed)},
            json_out=json_out,
            title=f"netstead index drop: {source}",
        )


def _import_indexes() -> ModuleType:
    """Resolve :mod:`netstead.indexes` — core module but build needs scipy + shapely at call time.

    Routed through :func:`require_extra` so any future packaging change
    (e.g. moving the index ops behind ``[clean]``) lands with one tweak.
    """
    return require_extra("netstead.indexes", "clean")
