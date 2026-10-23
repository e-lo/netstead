"""Tests for the GMNS-aware MCP server (task 4.11 / issue #94).

Same pattern as packages/corral/tests/mcp/test_mcp.py — introspect
the registered tools and call them directly so we don't need an MCP
stdio peer.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")
pytest.importorskip("scipy")  # connected_components tool

from netstead.fixtures import leavenworth
from netstead.mcp import build_server


def _call_tool(server, name: str, **kwargs):
    """Synchronously dispatch a FastMCP tool by name."""
    return asyncio.run(server._tool_manager.call_tool(name, kwargs))


# ---------------------------------------------------------------------------
# Server composition
# ---------------------------------------------------------------------------


def test_gmns_server_inherits_generic_tools():
    """The netstead MCP server still exposes the generic corral tools."""
    tools = {t.name for t in build_server()._tool_manager.list_tools()}
    assert {"describe_package", "validate_package", "list_tables"} <= tools


def test_gmns_server_adds_network_aware_tools():
    """The GMNS-specific tools are registered on top."""
    tools = {t.name for t in build_server()._tool_manager.list_tools()}
    assert {"describe_network", "quality_check", "connected_components", "scope_from_nodes"} <= tools


def test_gmns_server_forwards_state_kwarg():
    """netstead's build_server forwards `state=` to the generic corral factory (F5)."""
    shared: dict[str, object] = {}
    server = build_server(state=shared)
    assert server.corral_state is shared


# ---------------------------------------------------------------------------
# describe_network
# ---------------------------------------------------------------------------


def test_describe_network_returns_gmns_metadata():
    """describe_network surfaces spec_version + link/node counts."""
    server = build_server()
    r = _call_tool(server, "describe_network", source=str(leavenworth.csv_dir()))
    assert r["spec_version"] == "0.97"
    assert isinstance(r["links"], int) and r["links"] > 0
    assert isinstance(r["nodes"], int) and r["nodes"] > 0


# ---------------------------------------------------------------------------
# quality_check
# ---------------------------------------------------------------------------


def test_quality_check_returns_issues_with_data_quality_category():
    """quality_check emits the GMNS rule pack's issues."""
    server = build_server()
    r = _call_tool(server, "quality_check", source=str(leavenworth.csv_dir()))
    assert "issues" in r
    # Leavenworth fires the high-speed-residential rule.
    codes = {i["code"] for i in r["issues"]}
    assert "quality.high_speed_residential" in codes


def test_quality_check_returns_canonical_to_dict_shape():
    """quality_check returns the canonical ValidationReport.to_dict shape too (F1, schema parity).

    Every issue carries the canonical issue dict (including ``extra``) — the
    same wire shape used by the api + generic mcp validate_package tool.
    """
    server = build_server()
    result = _call_tool(server, "quality_check", source=str(leavenworth.csv_dir()))
    assert {"report_version", "summary", "issues"} <= result.keys()
    if result["issues"]:
        assert "extra" in result["issues"][0]


# ---------------------------------------------------------------------------
# connected_components
# ---------------------------------------------------------------------------


def test_connected_components_on_leavenworth_is_one():
    """Leavenworth is a single weakly-connected component."""
    server = build_server()
    r = _call_tool(server, "connected_components", source=str(leavenworth.csv_dir()))
    assert r["component_count"] == 1
    assert sum(r["sizes"]) == r["sizes"][0]  # all nodes in one component


# ---------------------------------------------------------------------------
# scope_from_nodes
# ---------------------------------------------------------------------------


def test_scope_from_nodes_returns_id_lists():
    """scope_from_nodes returns subsets of (node_ids, link_ids)."""
    server = build_server()
    r = _call_tool(
        server,
        "scope_from_nodes",
        source=str(leavenworth.csv_dir()),
        node_ids=[1, 2],
        path_between=False,
    )
    assert isinstance(r["node_ids"], list)
    assert 1 in r["node_ids"] and 2 in r["node_ids"]
    assert r["result_link_count"] == len(r["link_ids"])


def test_scope_from_nodes_path_between_result_is_json_serialisable():
    """scope_from_nodes with far-apart seeds returns plain ints (regression).

    ``path_between=True`` folds shortest-path nodes into the scope; those
    came back as ``numpy.int64`` and FastMCP's result serializer raised
    "Unable to serialize unknown type: <class 'numpy.int64'>". Dispatch
    through the public ``server.call_tool`` so the serializer runs.
    """
    import json

    server = build_server()
    args = {"source": str(leavenworth.csv_dir()), "node_ids": [1, 121], "path_between": True}
    asyncio.run(server.call_tool("scope_from_nodes", args))  # must not raise ToolError
    r = _call_tool(server, "scope_from_nodes", **args)
    assert r["result_node_count"] > 2  # intermediate path nodes were folded in
    assert all(type(n) is int for n in r["node_ids"])
    assert all(type(lid) is int for lid in r["link_ids"])
    json.dumps(r)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_mcp_subcommand_listed_in_netstead_help():
    """`netstead --help` shows the `mcp` subcommand."""
    from netstead.cli.app import app as netstead_cli_app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(netstead_cli_app, ["--help"])
    assert result.exit_code == 0
    assert "mcp" in result.stdout


def test_mcp_serve_help_runs():
    """`netstead mcp serve --help` exits cleanly (smoke).

    We only check exit code: the typer/rich help renderer is
    terminal-width sensitive and substring assertions on the rendered
    output flake on CI Linux runners (where the help panel wraps long
    option names across rows). The exit code already proves the
    command and its options were registered without conflict.

    Direct tool-surface coverage (server.build_server(), per-tool
    behavior) is in the earlier tests in this file — those exercise
    the real code paths, not the help rendering.
    """
    from netstead.cli.app import app as netstead_cli_app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(netstead_cli_app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
