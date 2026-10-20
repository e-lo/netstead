"""Optional-extra import helper for netstead CLI commands.

Replaces the 5 sites that called ``importlib.import_module()`` with their
own divergent error-handling. One helper, one consistent error message,
one place to add caching / dev-time warnings later.

This lives in :mod:`netstead.cli` (not ``netstead.clean`` / ``netstead.server`` /
``netstead.mcp``) so the import-linter contract
``netstead.cli ↛ netstead.{clean,server,mcp}`` keeps holding — the import-linter
scans static imports only, so runtime discovery via :func:`importlib.import_module`
is the architecture-blessed way to thread a CLI entry point into an optional
submodule.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import NoReturn

import typer

__all__ = ["missing_extra", "require_extra"]


def require_extra(module_name: str, extra_name: str) -> ModuleType:
    """Import ``module_name`` or exit the CLI with a helpful install hint.

    Replaces the prior pattern of inline ``try/except ImportError`` in each
    command (which diverged across 5 sites — some printed ``clean`` even for
    ``server`` imports). Centralising this means future enhancements (caching,
    version probes, dev-time warnings) land in exactly one place.

    Args:
        module_name: The submodule (e.g. ``"netstead.server"``,
            ``"netstead.clean"``).
        extra_name: The pip extra to suggest (e.g. ``"server"``,
            ``"clean"``).

    Returns:
        The imported module.

    Raises:
        typer.Exit: With code 1 and a red error printed to stderr when
            the import fails. The error message tells the user exactly
            which extra to install.
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        missing_extra(module_name, extra_name, e)


def missing_extra(module_name: str, extra_name: str, error: ImportError) -> NoReturn:
    """Exit the CLI with the install hint for a failed import of a third-party optional dependency.

    For a plain ``import`` in a ``try`` (e.g. ``uvicorn``), where :func:`require_extra`'s
    importlib indirection buys nothing because no import-linter contract covers the module.

    Raises:
        typer.Exit: Always, with code 1 after printing the hint to stderr.
    """
    typer.secho(
        f"{module_name} requires the [{extra_name}] extra: pip install 'netstead[{extra_name}]' ({error})",
        fg="red",
        err=True,
    )
    raise typer.Exit(code=1) from None
