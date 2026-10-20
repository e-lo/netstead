"""Tests for :func:`corral.docgen.click_to_md` — CLI reference from a click tree."""

from __future__ import annotations

import click
import pytest
from corral.docgen import click_to_md


@pytest.fixture
def cli() -> click.Group:
    @click.group()
    def root():
        """A tiny tool.

        Second paragraph with :meth:`Thing.method` and a [clean] extra.
        """

    @root.command()
    @click.argument("source", type=click.Path())
    @click.option("--json", "json_out", is_flag=True, help="Emit JSON.")
    @click.option("--limit", type=int, default=10, help="Rows to show | at most.")
    def info(source, json_out, limit):
        """Show a summary (needs the [reports] extra)."""

    @root.group()
    def spec():
        """Spec sub-commands."""

    @spec.command(name="list")
    @click.option("--kind", type=click.Choice(["a", "b"]), default="a", help="Which kind.")
    def spec_list(kind):
        """List specs."""

    return root


def test_headings_and_index(cli: click.Group) -> None:
    md = click_to_md(cli, prog="tiny")
    assert "## `tiny info`" in md
    assert "## `tiny spec`" in md
    assert "### `tiny spec list`" in md  # one level deeper than its group
    assert "| [`tiny info`](#tiny-info) | Show a summary" in md
    assert "| [`tiny spec`](#tiny-spec) | Spec sub-commands. | per sub-command |" in md
    assert "- [`tiny spec list`](#tiny-spec-list) — List specs." in md


def test_params_tables_and_json_marker(cli: click.Group) -> None:
    md = click_to_md(cli, prog="tiny")
    assert "| `SOURCE` | path | yes |" in md
    assert "| `--json` | flag |  | Emit JSON. |" in md
    assert "| `--limit` | integer | `10` | Rows to show \\| at most. |" in md
    assert "| `--kind` | choice: `a` / `b` | `a` | Which kind. |" in md
    assert "`--json` writes the result to stdout as JSON" in md
    assert "`--help`" not in md.split("## `tiny info`")[1].split("## `tiny spec`")[0]


def test_brackets_are_escaped_not_swallowed(cli: click.Group) -> None:
    md = click_to_md(cli, prog="tiny")
    assert "needs the \\[reports\\] extra" in md


def test_usage_line_per_command(cli: click.Group) -> None:
    md = click_to_md(cli, prog="tiny")
    assert "Usage: tiny info [OPTIONS] SOURCE" in md
    assert "Usage: tiny spec list [OPTIONS]" in md
