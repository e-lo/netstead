"""Generic corral MCP server (task 4.11).

Exposes stateless tools over the Model Context Protocol so an AI
agent (Claude Desktop, Claude Code, ...) can describe + validate any
Frictionless data package by path / URL — without the agent having to
know Python.

Tools shipped here are **stateless**: each call takes a ``source``
path/URL and loads the package fresh. No server-side session, no
caching, no cross-call mutation. Stateful surfaces (editing sessions
with rollback, indexed scope ops) need per-session state and will
land via the :data:`state` kwarg seam (see :func:`build_server`).

Use via :func:`build_server` which returns a configured
:class:`mcp.server.fastmcp.FastMCP`. corral ships no CLI entry point
of its own for this; the runnable server is ``netstead mcp serve``,
which wraps this one (:func:`netstead.mcp.build_server`) and hooks it
into the stdio transport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from corral.dataset import Package

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.fastmcp import FastMCP


__all__ = ["build_server"]


def build_server(
    name: str = "corral",
    *,
    state: dict[str, Any] | None = None,
) -> FastMCP:
    """Return a configured :class:`FastMCP` exposing generic corral tools.

    The returned server is ready to ``run(transport="stdio")``. Tools:

    * ``describe_package(source)`` — package metadata: table list +
      row counts + engine name.
    * ``validate_package(source)`` — full validation; returns the
      :class:`~corral.reports.ValidationReport` as a JSON dict
      via :meth:`~corral.reports.ValidationReport.to_dict`
      (canonical wire shape).
    * ``list_tables(source)`` — short list of table names (cheap, no
      row counts).

    Args:
        name: MCP server display name. Default ``"corral"``.
        state: Optional shared-state dict for stateful tools (sessions,
            indexed scope caches, etc.) that compose on this server
            via :func:`netstead.mcp.build_server`. Stored on the
            returned server's ``settings`` under the key
            ``"corral_state"`` so domain extensions can read /
            write keys cooperatively. ``None`` (default) means the
            server is purely stateless — today's contract. The kwarg
            is documented + accepted now so the public signature can
            grow stateful tools later without a breaking change.

    Returns:
        A :class:`FastMCP` instance with the tools registered. Caller
        invokes ``.run(transport="stdio")`` from the CLI entry point.

    Examples:
        >>> import pytest
        >>> pytest.importorskip("mcp")
        <module ...>
        >>> server = build_server()
        >>> # The server object is ready; ``server.run(...)`` would block
        >>> # on stdio. Tests exercise the tools via the lower-level
        >>> # registry instead.
        >>> isinstance(server.name, str)
        True
        >>> # Stateful seam: pass a dict that future stateful tools
        >>> # (netstead.mcp.edit_session etc.) will read + write into.
        >>> shared = {}
        >>> server2 = build_server(state=shared)
        >>> isinstance(server2.name, str)
        True
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name=name, instructions=_INSTRUCTIONS)
    # Stash shared state on a stable attribute the domain MCP
    # extensions look up. Empty dict default so callers can always
    # do `server.corral_state.setdefault(...)` without a None check.
    server.corral_state = state if state is not None else {}  # type: ignore[attr-defined]

    @server.tool(name="describe_package", description=_DESCRIBE_DOC)
    def describe_package(source: str) -> dict[str, Any]:
        """Return metadata about the package at ``source``."""
        pkg = Package.from_source(source)
        tables = []
        for tname, table in pkg.tables.items():
            try:
                tables.append({"name": tname, "rows": table.count(), "columns": table.columns()})
            except Exception:  # pragma: no cover - per-table resilience
                tables.append({"name": tname, "rows": None, "columns": None})
        return {
            "source": source,
            "name": pkg.spec.name,
            "engine": type(pkg.engine).__name__,
            "table_count": len(pkg.tables),
            "tables": tables,
        }

    @server.tool(name="validate_package", description=_VALIDATE_DOC)
    def validate_package(source: str) -> dict[str, Any]:
        """Run full validation; return :meth:`ValidationReport.to_dict` (canonical shape)."""
        pkg = Package.from_source(source)
        report = pkg.validate()
        return report.to_dict()

    @server.tool(name="list_tables", description=_LIST_DOC)
    def list_tables(source: str) -> list[str]:
        """Return the table names in the package at ``source`` (cheap, no row counts)."""
        pkg = Package.from_source(source)
        return sorted(pkg.tables.keys())

    return server


_INSTRUCTIONS = """\
corral MCP server — stateless tools for any Frictionless data package.

Every tool takes a ``source`` (filesystem path or URL to a package
directory / datapackage.json file). Packages are loaded fresh per
call; there is no session or cache.

Use ``describe_package`` for a quick overview, ``list_tables`` for a
cheap name list, and ``validate_package`` to run the full schema +
FK + structural checks and get back a JSON-shaped report.
"""

_DESCRIBE_DOC = """\
Return metadata about the data package at ``source``. Output includes
the package name, engine, table count, and per-table {name, rows,
columns} entries.
"""

_VALIDATE_DOC = """\
Run full validation (structural + schema + foreign-key + sync-state)
on the package at ``source``. Returns the canonical
ValidationReport.to_dict() shape — {report_version, spec_version,
source, created_at, metadata, summary, issues}.
"""

_LIST_DOC = """\
Return the table names (sorted) in the package at ``source``. Cheap —
does not compute row counts; use ``describe_package`` for those.
"""
