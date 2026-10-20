"""Tests for ``netstead spec list/diff`` + ``netstead doctor`` (tasks 4.3 / 4.5).

Issues: #85 (spec list/diff), #87 (doctor).

Follows the pattern in ``test_cli.py`` — uses :class:`typer.testing.CliRunner`
and asserts on ``--json`` payloads (the rich path is exercised
incidentally by running the commands at all).
"""

from __future__ import annotations

import json

from netstead.cli.app import app
from netstead.spec import DEFAULT_SPEC, SUPPORTED_SPECS
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# spec list
# ---------------------------------------------------------------------------


def test_spec_list_json_lists_supported_versions():
    """`netstead spec list --json` returns {default, supported} matching the module constants."""
    result = runner.invoke(app, ["spec", "list", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["default"] == DEFAULT_SPEC
    assert payload["supported"] == list(SUPPORTED_SPECS)


# ---------------------------------------------------------------------------
# spec diff
# ---------------------------------------------------------------------------


def test_spec_diff_returns_resource_deltas():
    """Diffing two adjacent versions returns the documented shape (structure-only test)."""
    result = runner.invoke(app, ["spec", "diff", "--json", "0.96", "0.97"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["v1"] == "0.96"
    assert payload["v2"] == "0.97"
    assert isinstance(payload["added_resources"], list)
    assert isinstance(payload["removed_resources"], list)
    assert isinstance(payload["changed_resources"], list)
    # If any resource changed, each entry has the documented keys.
    for entry in payload["changed_resources"]:
        assert set(entry) >= {"name", "added_fields", "removed_fields", "changed_fields"}
        for change in entry["changed_fields"]:
            assert set(change) >= {"name", "v1_type", "v2_type"}


def test_spec_diff_same_version_returns_empty_diffs():
    """Diffing a version against itself yields empty added/removed/changed lists."""
    result = runner.invoke(app, ["spec", "diff", "--json", "0.97", "0.97"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["added_resources"] == []
    assert payload["removed_resources"] == []
    assert payload["changed_resources"] == []


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_runs_and_returns_checks():
    """`netstead doctor` exits 0 in a healthy env and prints a meaningful number of checks."""
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    # python-version + 3 spec versions + leavenworth + env + at least one extra
    assert len(payload) >= 3


def test_doctor_json_mode():
    """The JSON payload is a list of dicts with name/ok/detail keys."""
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    for check in payload:
        assert set(check) >= {"name", "ok", "detail"}
        assert isinstance(check["name"], str)
        assert isinstance(check["ok"], bool)
        assert isinstance(check["detail"], str)


def test_doctor_extras_check_includes_installed_field():
    """`doctor --json` extras checks carry `installed: bool` agents can filter on."""
    result = runner.invoke(app, ["doctor", "--json"])
    # Doctor may exit non-zero on actual failures; --json shape still must be parseable.
    payload = json.loads(result.stdout)
    extras = [c for c in payload if c["name"].startswith("extra:")]
    assert len(extras) >= 1, "doctor should report at least one extras check"
    assert all("installed" in c for c in extras), "every extras check should carry an `installed: bool` field"
    assert all(isinstance(c["installed"], bool) for c in extras)


def test_doctor_extras_ok_stays_true_when_extra_absent():
    """Optional extras keep ok=True when absent — installed=False is the agent-readable signal."""
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    extras = [c for c in payload if c["name"].startswith("extra:")]
    # All optional extras report ok=True regardless of installed state.
    assert all(c["ok"] is True for c in extras)


def test_doctor_extras_probe_the_packages_each_extra_actually_installs():
    """The probe table must match pyproject: scipy is [graph], not [clean]; no igraph."""
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    names = {c["name"] for c in payload if c["name"].startswith("extra:")}
    assert "extra:graph[scipy]" in names
    assert {"extra:clean[shapely]", "extra:clean[geopandas]", "extra:clean[pyproj]"} <= names
    assert not any("igraph" in n for n in names)
