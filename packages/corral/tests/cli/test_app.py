"""Tests for the generic corral CLI app (task 4.1a / issue #82).

Uses typer's :class:`CliRunner` so every command is exercised end-to-end
against the Leavenworth fixture. ``--json`` paths assert a single
parseable document on stdout; rich paths only assert non-zero output
(visual formatting is not pinned).
"""

from __future__ import annotations

import json

import pytest
from corral.cli import build_app
from netstead.fixtures import leavenworth
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def app():
    """A fresh app per test so command-state can't leak between tests."""
    return build_app()


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def test_info_rich_mode_runs(app):
    """`info <fixture>` exits 0 and prints something on stderr (rich panel)."""
    result = runner.invoke(app, ["info", str(leavenworth.csv_dir())])
    assert result.exit_code == 0, result.stderr


def test_info_json_mode_emits_parseable_document(app):
    """`info --json` writes a single JSON object on stdout with the expected keys."""
    result = runner.invoke(app, ["info", "--json", str(leavenworth.csv_dir())])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["table_count"] >= 1
    assert {"name", "source", "engine", "table_count", "tables"} <= payload.keys()
    # Each table summary has name + rows + columns.
    assert all({"name", "rows", "columns"} <= t.keys() for t in payload["tables"])


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_json_mode_emits_issues_document(app):
    """`validate --json` writes {header, issues: [...]} on stdout."""
    result = runner.invoke(app, ["validate", "--json", str(leavenworth.csv_dir())])
    # Leavenworth is clean per the spec; exit 0 expected.
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "header" in payload and "issues" in payload
    assert isinstance(payload["issues"], list)


def test_validate_rich_mode_runs(app):
    """`validate` in rich mode exits 0 on Leavenworth and prints to stderr."""
    result = runner.invoke(app, ["validate", str(leavenworth.csv_dir())])
    assert result.exit_code == 0, result.stderr


# ---------------------------------------------------------------------------
# Help + arg parsing
# ---------------------------------------------------------------------------


def test_app_help_lists_commands(app):
    """`--help` lists at least validate + info."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "validate" in result.stdout
    assert "info" in result.stdout


def test_unknown_command_exits_nonzero(app):
    """Unknown subcommand exits non-zero (typer's standard behavior)."""
    result = runner.invoke(app, ["nope"])
    assert result.exit_code != 0


def test_build_app_returns_fresh_app_each_call():
    """Two build_app() calls produce two distinct typer.Typer instances.

    Important: netstead's CLI relies on getting its own copy so it can
    register over commands without mutating the corral singleton.
    """
    a, b = build_app(), build_app()
    assert a is not b
