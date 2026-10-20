"""Tests for ``corral convert`` (task 4.2 / issue #84).

End-to-end exercises the convert command through typer's
:class:`CliRunner` against the Leavenworth fixture. The roundtrip test
is the load-bearing one — it proves the CSV → parquet write produces a
package corral can read back with the same logical table set. The
remaining tests pin the format-inference / flag-parsing behaviours so a
later refactor of those rules can't silently drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from corral.cli import build_app
from corral.dataset import Package
from netstead.fixtures import leavenworth
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def app():
    """A fresh app per test so command-state can't leak between tests."""
    return build_app()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_convert_csv_to_parquet_roundtrip(app, tmp_path: Path):
    """Convert the Leavenworth CSV dir to a parquet dir, then read it back.

    The round-trip is the contract: after a convert we expect to be
    able to ``Package.from_source`` the destination and recover the
    same set of table names.
    """
    src = leavenworth.csv_dir()
    dest = tmp_path / "leavenworth.parquet"

    result = runner.invoke(app, ["convert", str(src), str(dest)])
    assert result.exit_code == 0, result.stderr
    assert dest.exists(), "convert did not create the destination directory"

    # Read the source + the destination and compare table inventories.
    src_pkg = Package.from_source(src)
    dest_pkg = Package.from_source(dest)
    assert set(dest_pkg.keys()) == set(src_pkg.keys())
    assert len(dest_pkg.keys()) >= 1


# ---------------------------------------------------------------------------
# --json summary
# ---------------------------------------------------------------------------


def test_convert_json_mode_emits_summary(app, tmp_path: Path):
    """``--json`` writes a single parseable summary dict on stdout."""
    src = leavenworth.csv_dir()
    dest = tmp_path / "out.parquet"

    result = runner.invoke(app, ["convert", "--json", str(src), str(dest)])
    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)
    assert {"source", "dest", "format", "table_count"} <= payload.keys()
    assert payload["table_count"] >= 1
    assert payload["format"] == "parquet"


# ---------------------------------------------------------------------------
# Format inference + override
# ---------------------------------------------------------------------------


def test_convert_format_inferred_from_extension(app, tmp_path: Path):
    """``dest=foo.parquet`` infers parquet without an explicit ``--format``."""
    src = leavenworth.csv_dir()
    dest = tmp_path / "inferred.parquet"

    result = runner.invoke(app, ["convert", "--json", str(src), str(dest)])
    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["format"] == "parquet"
    assert dest.exists()


def test_convert_explicit_format_overrides_extension(app, tmp_path: Path):
    """``--format csv`` wins over a ``.parquet`` extension."""
    src = leavenworth.csv_dir()
    # Suffix says parquet but the explicit flag should force CSV.
    dest = tmp_path / "override.parquet"

    result = runner.invoke(app, ["convert", "--json", "--format", "csv", str(src), str(dest)])
    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["format"] == "csv"
    # The directory exists and contains .csv files — never .parquet.
    assert dest.exists()
    children = list(dest.iterdir())
    assert children, "csv convert produced no files"
    assert any(c.suffix == ".csv" for c in children)
    assert not any(c.suffix == ".parquet" for c in children)


# ---------------------------------------------------------------------------
# Engine flag
# ---------------------------------------------------------------------------


def test_convert_rejects_unknown_engine(app, tmp_path: Path):
    """``--engine bogus`` exits non-zero with a helpful message."""
    src = leavenworth.csv_dir()
    dest = tmp_path / "wont-exist.parquet"

    result = runner.invoke(app, ["convert", "--engine", "bogus", str(src), str(dest)])
    assert result.exit_code != 0
    # typer.BadParameter surfaces on stderr; check the engine name and
    # the expected-values blurb both appear.
    combined = (result.stderr or "") + (result.stdout or "") + str(result.exception or "")
    assert "bogus" in combined
    assert "ibis" in combined or "pandas" in combined or "polars" in combined


@pytest.mark.parametrize(
    ("dest_name", "flags", "expected"),
    [
        ("net.zip", [], "zipcsv"),  # the old CLI mirror said "unknown" for a plain .zip
        ("net.csv.zip", [], "zipcsv"),
        ("bundle", ["--format", "zip"], "zipcsv"),  # the alias resolves, as in Package.write
    ],
)
def test_convert_summary_reports_the_format_package_write_used(app, tmp_path: Path, dest_name, flags, expected):
    """#169: the summary uses Package.write's own inference, so the two cannot drift."""
    dest = tmp_path / dest_name
    result = runner.invoke(app, ["convert", "--json", *flags, str(leavenworth.csv_dir()), str(dest)])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["format"] == expected
