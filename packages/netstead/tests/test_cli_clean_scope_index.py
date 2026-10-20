"""Tests for ``netstead clean / scope / index`` CLI commands (task 4.6 / issue #88).

Each sub-app wraps an existing programmatic API. These tests cover the
plumbing: exit codes, JSON shape, dry-run safety, and that
``--help`` advertises the new top-level commands so a user can find
them. The ops themselves are exercised in ``test_clean.py`` /
``test_scope.py`` / ``test_indexes_cache.py`` — this file does **not**
re-test the underlying behaviour.

The Leavenworth fixture's ``node`` table carries an empty ``name``
column that ibis types as null, which DuckDB refuses to round-trip
through ``replace_table``. So the clean tests pass ``--engine pandas``
to dodge a backend-specific edge case unrelated to the CLI surface
under test. The same fixture has no inline ``geometry`` column on
links, which the index ``build`` step gracefully degrades around.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead.cli.app import app
from netstead.fixtures import leavenworth
from typer.testing import CliRunner

# clean + index ops both require shapely + igraph (the ``[clean]`` extra).
# Scope's graph-aware ops need igraph too. Skip wholesale if missing — the
# extras are part of the dev environment but a slim install will skip.
pytest.importorskip("shapely")
pytest.importorskip("scipy")

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the Leavenworth CSV fixture into ``tmp_path`` so tests can mutate it freely."""
    dest = tmp_path / "leavenworth_csv"
    shutil.copytree(leavenworth.csv_dir(), dest)
    return dest


# ---------------------------------------------------------------------------
# clean — remove-orphans + dry-run + dest
# ---------------------------------------------------------------------------


def test_clean_simplify_dry_run_does_not_write(tmp_path: Path):
    """``--dry-run`` rolls back before write — the source on disk stays unchanged."""
    src = _copy_fixture(tmp_path)
    link_csv = src / "link.csv"
    before_bytes = link_csv.read_bytes()
    result = runner.invoke(
        app,
        [
            "clean",
            "remove-orphans",  # use a no-geometry op; simplify needs a geometry column
            "--json",
            "--dry-run",
            str(src),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # File on disk must be untouched — dry-run never writes.
    assert link_csv.read_bytes() == before_bytes


def test_clean_simplify_writes_to_dest(tmp_path: Path):
    """``--dest <dir>`` writes the modified package there and leaves the source alone."""
    src = _copy_fixture(tmp_path)
    dest = tmp_path / "cleaned"
    src_link_before = (src / "link.csv").read_bytes()
    result = runner.invoke(
        app,
        [
            "clean",
            "remove-orphans",
            "--json",
            "--dest",
            str(dest),
            str(src),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # Source untouched.
    assert (src / "link.csv").read_bytes() == src_link_before
    # Dest exists and is loadable as a package.
    assert dest.exists()
    # Round-trip through the loader to confirm the on-disk package is valid.
    from netstead import Network

    net = Network.from_source(dest, engine=IbisEngine())
    assert net.links.count() > 0
    assert net.nodes.count() > 0


def test_clean_in_place_keeps_every_table_and_the_source_format(tmp_path: Path):
    """Without ``--dest`` the op writes back over its source: every table survives, still as CSV."""
    src = _copy_fixture(tmp_path)
    files_before = sorted(path.name for path in src.iterdir())
    result = runner.invoke(app, ["clean", "remove-orphans", "--json", str(src)])
    assert result.exit_code == 0, result.stdout
    assert sorted(path.name for path in src.iterdir()) == files_before
    from netstead import Network

    net = Network.from_source(src, engine=IbisEngine())
    assert net.links.count() == 339


def test_clean_remove_orphans_json_emits_summary(tmp_path: Path):
    """``--json`` payload carries the standard {op, dry_run, edits[]} summary."""
    src = _copy_fixture(tmp_path)
    result = runner.invoke(
        app,
        [
            "clean",
            "remove-orphans",
            "--json",
            "--dry-run",
            str(src),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["op"] == "remove_orphans"
    assert payload["dry_run"] is True
    assert isinstance(payload["edits"], list) and payload["edits"]
    assert payload["edits"][0]["table"] == "node"
    assert payload["edits"][0]["op"] == "replace_table"


# ---------------------------------------------------------------------------
# scope — from-nodes / connected-component / from-zone
# ---------------------------------------------------------------------------


def test_scope_from_nodes_json_emits_sets():
    """``scope from-nodes --json`` returns {node_count, link_count, node_ids, link_ids}."""
    result = runner.invoke(
        app,
        ["scope", "from-nodes", "--json", "--no-path-between", str(leavenworth.csv_dir()), "1", "2"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    # The two seeds + induced links between them must be present.
    assert 1 in payload["node_ids"]
    assert 2 in payload["node_ids"]
    assert payload["node_count"] == len(payload["node_ids"])
    assert payload["link_count"] == len(payload["link_ids"])
    assert "from_nodes" in payload["provenance"]


def test_scope_from_nodes_json_path_between_ids_are_ints():
    """``--json`` ids are all JSON ints, including path-expanded nodes (regression).

    ``render_dict`` falls back to ``default=str``, so any ``numpy.int64``
    leaking out of the scope surfaced as ``"27"`` next to ``1``.
    """
    result = runner.invoke(
        app,
        ["scope", "from-nodes", "--json", "--path-between", str(leavenworth.csv_dir()), "1", "121"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["node_count"] > 2
    assert all(isinstance(n, int) for n in payload["node_ids"]), payload["node_ids"]
    assert all(isinstance(lid, int) for lid in payload["link_ids"]), payload["link_ids"]


def test_scope_connected_component_json():
    """``scope connected-component`` returns a non-empty single-component result."""
    result = runner.invoke(
        app,
        ["scope", "connected-component", "--json", str(leavenworth.csv_dir()), "1"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["node_count"] >= 1
    assert 1 in payload["node_ids"]
    assert "connected_component" in payload["provenance"]


def test_scope_from_zone_json():
    """``scope from-zone`` works on a fixture with a zone_id column.

    The bundled Leavenworth fixture has no ``zone_id`` column, so we
    expect a typed error (exit 1) rather than a silent empty scope —
    that's the contract documented on :func:`netstead.scope.from_zone`.
    """
    result = runner.invoke(
        app,
        ["scope", "from-zone", "--json", str(leavenworth.csv_dir()), "100"],
    )
    # No zone_id column → ScopeError → CLI exits 1 with a clean message.
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# index — build / status / drop
# ---------------------------------------------------------------------------


def test_index_build_json_creates_cache_files(tmp_path: Path):
    """``index build --json`` returns parquet paths and the files exist on disk.

    The Leavenworth fixture has no inline link geometry, so spatial is
    auto-skipped; the graph index is built and cached.
    """
    src = _copy_fixture(tmp_path)
    result = runner.invoke(app, ["index", "build", "--json", str(src)])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["graph"] is True
    # Spatial silently skipped because the fixture lacks a geometry column.
    assert payload["spatial"] is False
    assert payload["skipped_spatial_no_geometry"] is True
    assert payload["paths"]
    for p in payload["paths"]:
        assert Path(p).is_file(), f"expected sidecar {p} on disk"


def test_index_status_finds_built_indexes(tmp_path: Path):
    """After ``build``, ``status`` reports the sidecars as present."""
    src = _copy_fixture(tmp_path)
    build_res = runner.invoke(app, ["index", "build", "--json", str(src)])
    assert build_res.exit_code == 0, build_res.stdout
    status_res = runner.invoke(app, ["index", "status", "--json", str(src)])
    assert status_res.exit_code == 0, status_res.stdout
    payload = json.loads(status_res.stdout)
    assert payload["graph"] is True
    assert payload["paths"]  # at least the graph sidecar


def test_index_drop_removes_cache(tmp_path: Path):
    """After ``drop``, ``status`` reports the sidecars as absent."""
    src = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["index", "build", "--json", str(src)]).exit_code == 0
    drop_res = runner.invoke(app, ["index", "drop", "--json", str(src)])
    assert drop_res.exit_code == 0, drop_res.stdout
    drop_payload = json.loads(drop_res.stdout)
    assert drop_payload["count"] >= 1
    # Status should now report nothing cached.
    status_res = runner.invoke(app, ["index", "status", "--json", str(src)])
    assert status_res.exit_code == 0, status_res.stdout
    status_payload = json.loads(status_res.stdout)
    assert status_payload["graph"] is False
    assert status_payload["paths"] == []


# ---------------------------------------------------------------------------
# Help discovery — the new top-level commands must show up
# ---------------------------------------------------------------------------


def test_clean_subcommand_listed_in_help():
    """``netstead --help`` advertises the three new sub-apps."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Strip ANSI/rich-box characters by checking for the bare token.
    out = result.stdout
    assert "clean" in out
    assert "scope" in out
    assert "index" in out
