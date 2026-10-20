"""``netstead scope`` — read-only network-aware scope builders (issue #88).

Each subcommand builds a :class:`NetworkScope` from a seed and prints
``{node_count, link_count, node_ids, link_ids}``. ``netstead.scope`` is
part of core (no extra needed) but the underlying :class:`~netstead.graph.GMNSGraph`
requires scipy (the ``[graph]`` extra) — surface a typed error if scipy
is absent.

Domain errors raised by ``netstead.scope`` (subclasses of ``ScopeError``)
are converted to clean CLI exits with a red message; non-domain
exceptions propagate.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import typer
from corral.cli.render import render_dict

from netstead import Network

from .._extras import require_extra
from .._helpers import summarise_scope

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``scope`` sub-app on ``app``."""
    scope_app = typer.Typer(no_args_is_help=True, help="Build a NetworkScope and print its (links, nodes) sets.")
    app.add_typer(scope_app, name="scope")

    @scope_app.command(name="from-nodes")
    def scope_from_nodes_cmd(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        node_ids: list[int] = typer.Argument(..., help="Seed node ids."),
        path_between: bool = typer.Option(True, "--path-between/--no-path-between"),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Build a scope from seed node ids and print its (links, nodes) sets."""
        scope_mod = _import_scope()
        ScopeError = scope_mod.ScopeError

        net = Network.from_source(source)
        try:
            scope = scope_mod.from_nodes(net, node_ids, path_between=path_between)
        except ScopeError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summarise_scope(scope), json_out=json_out, title=f"netstead scope from-nodes: {source}")

    @scope_app.command(name="from-node")
    def scope_from_node_cmd(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        node_id: int = typer.Argument(..., help="Seed node id."),
        network_buffer: str = typer.Option(
            "0.5mi", "--network-buffer", help="Distance with unit (e.g. '0.5mi', '800m')."
        ),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Scope = all nodes within ``--network-buffer`` network-distance of ``node_id``."""
        scope_mod = _import_scope()
        ScopeError = scope_mod.ScopeError

        net = Network.from_source(source)
        try:
            scope = scope_mod.from_node(net, node_id, network_buffer=network_buffer)
        except ScopeError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summarise_scope(scope), json_out=json_out, title=f"netstead scope from-node: {source}")

    @scope_app.command(name="connected-component")
    def scope_connected_component_cmd(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        seed_node_id: int = typer.Argument(..., help="Seed node id whose component to return."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Scope = the weakly-connected component containing ``seed_node_id``."""
        scope_mod = _import_scope()
        ScopeError = scope_mod.ScopeError

        net = Network.from_source(source)
        try:
            scope = scope_mod.connected_component(net, seed_node_id)
        except ScopeError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summarise_scope(scope), json_out=json_out, title=f"netstead scope connected-component: {source}")

    @scope_app.command(name="from-zone")
    def scope_from_zone_cmd(
        source: Path = typer.Argument(..., help="Path to the GMNS network."),
        zone_ids: list[int] = typer.Argument(..., help="Zone ids whose nodes (+ incident links) to include."),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Scope = all nodes with ``zone_id`` in ``zone_ids`` plus their incident links."""
        scope_mod = _import_scope()
        ScopeError = scope_mod.ScopeError

        net = Network.from_source(source)
        try:
            scope = scope_mod.from_zone(net, zone_ids)
        except ScopeError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        render_dict(summarise_scope(scope), json_out=json_out, title=f"netstead scope from-zone: {source}")


def _import_scope() -> ModuleType:
    """Resolve :mod:`netstead.scope`, first checking the ``[graph]`` extra is installed.

    ``netstead.scope`` itself is part of the core install (no extra needed),
    but every scope builder reaches :class:`~netstead.graph.GMNSGraph` at
    call time, and that import fails without scipy — deep inside the op,
    as a raw traceback. Probing :mod:`netstead.graph` up front through
    :func:`require_extra` turns that into the same one-line "install the
    extra" exit the clean / mcp / server commands use.
    """
    require_extra("netstead.graph", "graph")
    return require_extra("netstead.scope", "graph")
