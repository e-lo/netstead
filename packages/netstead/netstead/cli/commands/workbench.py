"""``netstead app`` — the Netstead Workbench: map + tables + selection over one live session.

Requires the ``[server]`` extra. ``netstead viz`` and ``netstead select-serve`` are
aliases that call :func:`run_workbench` (the old standalone apps were removed in P1b).
"""

from __future__ import annotations

import ipaddress
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


_SET_ROOTS_HINT = (
    "List the folders to share in ./netstead.toml or your user config.toml:\n"
    "\n"
    "    [io]\n"
    '    allowed_roots = ["/data/networks"]\n'
    "\n"
    "or for one run, set NETSTEAD_IO__ALLOWED_ROOTS='[\"/data/networks\"]' in the environment."
)


def _is_any_loopback(host: str) -> bool:
    """Whether ``host`` names this machine in any spelling: ``localhost`` (any case) or a loopback IP.

    Wider than :func:`~netstead.workbench.server.is_loopback_host`, which lists only the exact
    spellings the server's Host-header guard protects; used here only to word the refusal.

    >>> [_is_any_loopback(h) for h in ("LOCALHOST", "127.0.0.2", "[::1]", "0.0.0.0", "nas.lan")]
    [True, True, True, False, False]
    """
    name = host.strip().lower()
    if name == "localhost":
        return True
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _refuse_public_bind_without_roots(settings: Settings) -> None:
    """Exit 2 when the bind host is unguarded and ``io.allowed_roots`` was never set.

    An empty ``io.allowed_roots`` means the home folder, and the Workbench has no authentication.
    The server applies its Host-header (DNS-rebinding) guard only to the exact loopback spellings
    in :func:`~netstead.workbench.server.is_loopback_host`, so any other host serves the home
    folder unguarded: to the network for a LAN address, or to a rebinding web page for another
    loopback spelling such as ``127.0.0.2``. Both are refused. Sources named on the command line
    don't count: ``settings`` is read before the CLI widens the roots for them. Blank root entries
    are rejected earlier, by the settings model.
    """
    from netstead.workbench.server import is_loopback_host

    host = settings.app.host
    if is_loopback_host(host) or settings.io.allowed_roots:
        return
    if _is_any_loopback(host):
        why = (
            f"{host!r} is a loopback address, but the Workbench's DNS-rebinding protection only covers "
            "127.0.0.1, localhost and ::1, so a web page could read your home folder through it. "
            "Bind one of those instead, or set io.allowed_roots."
        )
    else:
        why = (
            "Unset means your home folder, and the Workbench has no authentication, so anyone on the "
            "network could browse it."
        )
    typer.echo(
        f"error: refusing to bind to {host!r} while io.allowed_roots is unset. {why} {_SET_ROOTS_HINT}", err=True
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
