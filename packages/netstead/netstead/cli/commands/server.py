"""``netstead server`` — run the self-hostable FastAPI app (issue #91)."""

from __future__ import annotations

from pathlib import Path

import typer

from .._extras import missing_extra, require_extra

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Register the ``server`` sub-app on ``app``."""
    server_app = typer.Typer(no_args_is_help=True, help="Run the netstead self-hostable HTTP server.")
    app.add_typer(server_app, name="server")

    @server_app.command(name="run")
    def server_run(
        config: Path = typer.Option(None, "--config", "-c", help="Path to server config (YAML/JSON)."),
        bind: str = typer.Option(None, "--bind", help="Override config bind address (default 127.0.0.1)."),
        port: int = typer.Option(None, "--port", help="Override config port (default 8000)."),
    ) -> None:
        """Start the netstead HTTP server with config from ``--config``.

        Reads :class:`corral.api.ServerSettings` from the config
        file (or uses defaults — localhost, no packages, auth=bearer
        with no token, which fails fast on first request).

        The CLI ``--bind`` / ``--port`` flags override the matching
        config keys for one-off testing without editing the file.
        """
        # The two netstead/corral modules go through require_extra() so the
        # static contract `netstead.cli must not import netstead.server`
        # holds: the import-linter scans static imports only. uvicorn is
        # third-party and under no contract, so it is a plain import (#173).
        server_module = require_extra("netstead.server", "server")
        api_module = require_extra("corral.api", "server")
        try:
            import uvicorn
        except ImportError as exc:
            missing_extra("uvicorn", "server", exc)

        settings = api_module.load_settings(config) if config else api_module.ServerSettings()
        if bind is not None:
            settings.bind = bind
        if port is not None:
            settings.port = port

        app_instance = server_module.build_app(settings)
        uvicorn.run(app_instance, host=settings.bind, port=settings.port)
