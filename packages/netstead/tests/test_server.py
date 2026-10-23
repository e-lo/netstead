"""Tests for the GMNS-aware FastAPI app (task 4.10 / issue #91).

End-to-end through ``TestClient`` against the Leavenworth fixture
mounted under a public id. Exercises both the generic corral
endpoints (still reachable) and the GMNS-aware /networks router.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("scipy")  # quality endpoint pulls in connectivity check via rule pack

from corral.api import AuthSettings, PackageRef, ServerSettings
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.server import build_app


def _settings() -> ServerSettings:
    """A test ServerSettings with the Leavenworth fixture under ``demo``."""
    return ServerSettings(
        bind="127.0.0.1",
        port=8000,
        auth=AuthSettings(kind="none"),
        packages=[PackageRef(id="demo", source=str(leavenworth.csv_dir()), description="Leavenworth")],
    )


# ---------------------------------------------------------------------------
# Generic endpoints still present
# ---------------------------------------------------------------------------


def test_generic_packages_endpoint_still_works():
    """Mounting netstead on top of corral keeps /packages reachable."""
    client = TestClient(build_app(_settings()))
    r = client.get("/packages")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "demo"


# ---------------------------------------------------------------------------
# /networks
# ---------------------------------------------------------------------------


def test_list_networks_returns_ids():
    """`/networks` lists configured networks (alias for /packages)."""
    client = TestClient(build_app(_settings()))
    r = client.get("/networks")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "demo"


def test_get_network_returns_gmns_metadata():
    """`/networks/{id}` includes spec_version + link/node counts."""
    client = TestClient(build_app(_settings()))
    r = client.get("/networks/demo")
    body = r.json()
    assert body["spec_version"] == "0.97"
    assert isinstance(body["links"], int) and body["links"] > 0
    assert isinstance(body["nodes"], int) and body["nodes"] > 0
    assert "link" in body["tables"]


def test_get_network_404_on_unknown_id():
    """Unknown network id -> 404."""
    client = TestClient(build_app(_settings()))
    r = client.get("/networks/nope")
    assert r.status_code == 404


def test_quality_endpoint_emits_issues():
    """`POST /networks/{id}/quality` returns an issues document."""
    client = TestClient(build_app(_settings()))
    r = client.post("/networks/demo/quality")
    body = r.json()
    assert "issues" in body
    # Leavenworth fires the high-speed-residential rule.
    codes = {i["code"] for i in body["issues"]}
    assert "quality.high_speed_residential" in codes


# ---------------------------------------------------------------------------
# Auth still applies on extra router
# ---------------------------------------------------------------------------


def test_networks_router_respects_bearer_auth():
    """The /networks router uses the same auth dependency as /packages."""
    settings = ServerSettings(
        bind="127.0.0.1",
        port=8000,
        auth=AuthSettings(kind="bearer", token="abc"),
        packages=[PackageRef(id="demo", source=str(leavenworth.csv_dir()))],
    )
    client = TestClient(build_app(settings))
    # No token -> 401.
    assert client.get("/networks").status_code == 401
    # Correct token -> 200.
    assert client.get("/networks", headers={"Authorization": "Bearer abc"}).status_code == 200


# ---------------------------------------------------------------------------
# CLI integration (`netstead server` subcommand surface)
# ---------------------------------------------------------------------------


def test_netstead_server_subcommand_listed_in_help():
    """`netstead --help` shows the `server` subcommand."""
    from netstead.cli.app import app as netstead_cli_app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(netstead_cli_app, ["--help"])
    assert result.exit_code == 0
    assert "server" in result.stdout


def test_netstead_server_run_help_runs():
    """`netstead server run --help` exits 0 (smoke; doesn't actually serve).

    Only checks exit code — substring assertions on the rendered help
    table flake on CI Linux because Rich wraps long option names across
    rows in the captured non-TTY output. The exit code already proves
    typer registered the command and its options without conflict.
    Concrete config/bind behavior is covered by the build_app +
    serve-bound integration tests elsewhere in this file.
    """
    from netstead.cli.app import app as netstead_cli_app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(netstead_cli_app, ["server", "run", "--help"])
    assert result.exit_code == 0


def test_netstead_server_run_imports_uvicorn_directly(monkeypatch, tmp_path):
    """Issue #173: only the netstead-internal modules go through ``require_extra``; uvicorn is a plain import."""
    import uvicorn
    from netstead.cli._extras import require_extra
    from netstead.cli.app import app as netstead_cli_app
    from netstead.cli.commands import server as server_cmd
    from typer.testing import CliRunner

    looked_up: list[str] = []

    def _recording_require_extra(module_name: str, extra_name: str):
        looked_up.append(module_name)
        return require_extra(module_name, extra_name)

    served: list[tuple[str, int]] = []
    monkeypatch.setattr(server_cmd, "require_extra", _recording_require_extra)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append((host, port)))

    config = tmp_path / "server.json"
    config.write_text('{"auth": {"kind": "none"}}', encoding="utf-8")
    result = CliRunner().invoke(netstead_cli_app, ["server", "run", "--config", str(config), "--port", "9411"])
    assert result.exit_code == 0, result.output
    assert served == [("127.0.0.1", 9411)]
    assert looked_up == ["netstead.server", "corral.api"]


def test_netstead_server_run_without_uvicorn_prints_the_server_extra_hint(monkeypatch):
    import sys

    from netstead.cli.app import app as netstead_cli_app
    from typer.testing import CliRunner

    monkeypatch.setitem(sys.modules, "uvicorn", None)  # makes `import uvicorn` raise ImportError
    result = CliRunner().invoke(netstead_cli_app, ["server", "run"])
    assert result.exit_code == 1
    assert "pip install 'netstead[server]'" in result.output
    assert "uvicorn requires the [server] extra" in result.output
