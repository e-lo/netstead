"""``netstead app`` — the Netstead Workbench: map + tables + selection over one live session.

Requires the ``[server]`` extra. ``netstead viz`` and ``netstead select-serve`` are
aliases that call :func:`run_workbench` (the old standalone apps were removed in P1b).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from netstead.config import Settings

__all__ = ["register", "run_workbench"]


def run_workbench(
    sources: Sequence[str],
    *,
    provider: str | None = None,
    model: str | None = None,
    basemap: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Build a session from settings + flag overrides, open ``sources``, and serve it (blocks)."""
    from netstead import workbench
    from netstead.config import SettingsError, load_settings
    from netstead.workbench.actions import OpenNetwork
    from netstead.workbench.errors import PathNotAllowed
    from netstead.workbench.paths import allowed_roots, is_allowed, local_locator, split_source

    flags = {
        "select.provider": provider,
        "select.model": model,
        "viz.basemap": basemap,
        "app.host": host,
        "app.port": port,
    }
    overrides: dict[str, Any] = {k: v for k, v in flags.items() if v is not None}
    to_open: list[str] = []
    trusted: list[str] = []  # local paths named on the command line (candidates for io.allowed_roots)
    for source in map(str, sources):
        try:
            kind, target = split_source(source)
            path = None if kind == "remote" else str(Path(target).expanduser().resolve())
        except (PathNotAllowed, RuntimeError, ValueError, OSError):
            path = None  # unsupported scheme or unusable path: never trusted; OpenNetwork reports it below
        if path is None:
            to_open.append(source)
            continue
        trusted.append(path)
        to_open.append(local_locator(source, path))  # keeps a duckdb:// format hint
    try:
        # Sources named on the command line are trusted: allow exactly those paths for this session.
        # Widen the roots *before* building the one Session, so plugins are discovered and loaded once.
        settings = load_settings(overrides=overrides).settings
        _refuse_public_bind_without_roots(settings)
        extra = [p for p in trusted if not is_allowed(p, settings)]
        if extra:
            overrides["io.allowed_roots"] = [str(r) for r in allowed_roots(settings)] + extra
            typer.echo(f"note: allowing {', '.join(extra)} for this session (io.allowed_roots)", err=True)
        session = workbench.Session(overrides=overrides)
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    for source in to_open:
        try:
            session.dispatch(OpenNetwork(source=source))
        except workbench.ActionError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
    from netstead.workbench.server import is_loopback_host

    app_settings = session.settings.app
    if not is_loopback_host(app_settings.host):
        source = session.loaded.sources.get("app.host", "default")
        typer.echo(
            f"WARNING: binding to {app_settings.host!r} (set by the {source} layer) exposes the Workbench "
            "to other machines on the network. It has no authentication and can open local files — only "
            "do this on a trusted network.",
            err=True,
        )
    opened = ", ".join(session.registry.ids()) or "none (open one from the header)"
    typer.echo(f"Netstead Workbench on http://{app_settings.host}:{app_settings.port}  (networks: {opened})")
    workbench.serve(session)


def _refuse_public_bind_without_roots(settings: Settings) -> None:
    """Exit 2 when the bind host is not loopback and ``io.allowed_roots`` was never set.

    An empty ``io.allowed_roots`` means the home folder, and the Workbench has no authentication, so a
    non-loopback bind with the default would let anyone on the network browse it. Sources named on the
    command line don't count: ``settings`` is read before the CLI widens the roots for them.
    """
    from netstead.workbench.server import is_loopback_host

    host = settings.app.host
    if is_loopback_host(host) or settings.io.allowed_roots:
        return
    typer.echo(
        f"error: refusing to bind to {host!r} while io.allowed_roots is unset. Unset means your home folder, "
        "and the Workbench has no authentication, so anyone on the network could browse it. List the folders "
        "to share first, in ./netstead.toml or your user config.toml:\n"
        "\n"
        "    [io]\n"
        '    allowed_roots = ["/data/networks"]\n'
        "\n"
        "or for one run: NETSTEAD_IO__ALLOWED_ROOTS='[\"/data/networks\"]' netstead app --host ...",
        err=True,
    )
    raise typer.Exit(2)


def register(app: typer.Typer) -> None:
    """Register the ``app`` command on ``app``."""

    @app.command(name="app")
    def app_cmd(
        sources: list[str] = typer.Argument(None, help="GMNS network paths/URLs to open."),
        provider: str = typer.Option(
            None, "--provider", help="NL parser: stub | anthropic | openai | gemini | ollama (default: settings)."
        ),
        model: str = typer.Option(
            None,
            "--model",
            help="NL model id (default: settings, else the provider default; see `netstead llm status`).",
        ),
        basemap: str = typer.Option(None, "--basemap", help="Basemap: positron | esri (default: settings)."),
        host: str = typer.Option(None, "--host", help="Bind host (default: settings, 127.0.0.1)."),
        port: int = typer.Option(None, "--port", help="Bind port (default: settings, 8850)."),
    ) -> None:
        """Serve the Netstead Workbench: open, inspect, and select on GMNS networks in the browser."""
        run_workbench(sources or [], provider=provider, model=model, basemap=basemap, host=host, port=port)
