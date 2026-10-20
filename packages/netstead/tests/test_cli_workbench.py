"""Tests for `netstead app` and its viz / select-serve aliases."""

import json
import shutil
from pathlib import Path

import pytest
from netstead.cli.app import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def served(monkeypatch, tmp_path):
    """Capture the session handed to serve() instead of starting uvicorn; isolate config."""
    monkeypatch.setenv("NETSTEAD_CONFIG_DIR", str(tmp_path / "user"))
    monkeypatch.chdir(tmp_path)
    captured = []
    monkeypatch.setattr("netstead.workbench.serve", captured.append)
    return captured


def test_app_opens_sources_and_applies_flag_overrides(served, rdu_source):
    result = runner.invoke(app, ["app", rdu_source, "--port", "9301", "--basemap", "esri"])
    assert result.exit_code == 0, result.output
    (session,) = served
    assert session.registry.ids() == ["rdu-i40"]
    assert (session.settings.app.port, session.settings.viz.basemap) == (9301, "esri")
    assert session.loaded.sources["app.port"] == "session"
    assert "http://127.0.0.1:9301" in result.output


def test_app_with_no_sources_starts_empty(served):
    assert runner.invoke(app, ["app"]).exit_code == 0
    assert len(served[0].registry) == 0


def test_app_bad_source_exits_1(served, tmp_path):
    result = runner.invoke(app, ["app", str(tmp_path / "missing")])
    assert result.exit_code == 1 and "could not open" in result.output and served == []


def test_app_bad_flag_value_exits_2(served):
    result = runner.invoke(app, ["app", "--provider", "gpt"])
    assert result.exit_code == 2 and "invalid settings" in result.output


def test_viz_is_an_alias(served, rdu_source):
    result = runner.invoke(app, ["viz", rdu_source, "--port", "9302"])
    assert result.exit_code == 0 and "netstead app" in result.output
    assert served[0].settings.app.port == 9302


def test_select_serve_is_an_alias(served, rdu_source):
    result = runner.invoke(app, ["select-serve", rdu_source, "--provider", "stub"])
    assert result.exit_code == 0 and served[0].registry.ids() == ["rdu-i40"]


def test_app_warns_on_public_bind(served, monkeypatch, tmp_path):
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(tmp_path)]))
    result = runner.invoke(app, ["app", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "0.0.0.0" in result.output
    assert "no authentication" in result.output
    assert "session" in result.output  # names the source layer that set app.host


def test_app_refuses_public_bind_without_explicit_allowed_roots(served, monkeypatch):
    """Empty ``io.allowed_roots`` means the home folder; serving that to the LAN is refused, with the fix."""
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    result = runner.invoke(app, ["app", "--host", "0.0.0.0"])
    assert result.exit_code == 2, result.output
    assert served == []
    assert "0.0.0.0" in result.output and "io.allowed_roots" in result.output
    assert "NETSTEAD_IO__ALLOWED_ROOTS" in result.output and "netstead.toml" in result.output


def test_app_refuses_public_bind_set_in_config_without_allowed_roots(served, monkeypatch):
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("NETSTEAD_APP__HOST", "0.0.0.0")
    result = runner.invoke(app, ["app"])
    assert result.exit_code == 2 and "io.allowed_roots" in result.output and served == []


def test_cli_sources_do_not_count_as_explicit_roots_for_a_public_bind(served, monkeypatch, rdu_source):
    """Sources named on the command line widen the roots for the session, but the home default still applies."""
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    result = runner.invoke(app, ["app", rdu_source, "--host", "0.0.0.0"])
    assert result.exit_code == 2 and "io.allowed_roots" in result.output and served == []


@pytest.mark.parametrize("alias", ["viz", "select-serve"])
def test_aliases_refuse_public_bind_without_explicit_allowed_roots(served, monkeypatch, alias, rdu_source):
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    result = runner.invoke(app, [alias, rdu_source, "--host", "0.0.0.0"])
    assert result.exit_code == 2 and "io.allowed_roots" in result.output and served == []
    assert "netstead app" not in result.output.split("error:", 1)[1]  # the hint names no one command


def test_app_refuses_public_bind_from_project_file_without_allowed_roots(served, monkeypatch, tmp_path):
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    (tmp_path / "netstead.toml").write_text('[app]\nhost = "0.0.0.0"\n')
    result = runner.invoke(app, ["app"])
    assert result.exit_code == 2 and "io.allowed_roots" in result.output and served == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_app_loopback_hosts_are_not_refused_with_empty_roots(served, monkeypatch, host):
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    result = runner.invoke(app, ["app", "--host", host])
    assert result.exit_code == 0, result.output
    assert served[0].settings.app.host == host


@pytest.mark.parametrize("host", ["127.0.0.2", "LOCALHOST", "[::1]"])
def test_unguarded_loopback_spellings_are_refused_accurately(served, monkeypatch, host):
    """Loopback, but not a spelling the Host-header guard protects: refused, without claiming LAN exposure."""
    monkeypatch.delenv("NETSTEAD_IO__ALLOWED_ROOTS", raising=False)
    result = runner.invoke(app, ["app", "--host", host])
    assert result.exit_code == 2 and served == []
    assert "anyone on the network" not in result.output
    assert "loopback" in result.output and "127.0.0.1" in result.output and "io.allowed_roots" in result.output


def test_app_blank_allowed_roots_entry_is_refused(served, monkeypatch):
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", '[""]')
    result = runner.invoke(app, ["app", "--host", "0.0.0.0"])
    assert result.exit_code == 2 and "blank" in result.output and served == []


def test_app_loopback_bind_has_no_warning(served):
    result = runner.invoke(app, ["app"])
    assert result.exit_code == 0, result.output
    assert "no authentication" not in result.output


@pytest.mark.parametrize("alias", ["viz", "select-serve"])
def test_alias_passes_url_source_unmangled(monkeypatch, alias):
    """A ``Path`` argument would collapse ``https://`` to ``https:/``; both aliases must take a raw string.

    The URL is unresolvable (``example.invalid``), so loading it fails fast; ``socket.getaddrinfo``
    is monkeypatched to raise immediately so the test doesn't pay for a real DNS lookup/timeout.
    """
    import socket

    def _fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    url = "https://example.invalid/net"
    result = runner.invoke(app, [alias, url])
    assert result.exit_code == 1
    assert url in result.output


def test_app_resolves_relative_source_to_absolute_path(served, monkeypatch, rdu_source):
    monkeypatch.chdir(Path(rdu_source).parent)
    relative = Path(rdu_source).name
    result = runner.invoke(app, ["app", relative])
    assert result.exit_code == 0, result.output
    (session,) = served
    recorded_source = session.history[0].action["source"]
    assert Path(recorded_source).is_absolute()
    assert recorded_source == str(Path(rdu_source).resolve())


def test_cli_sources_outside_allowed_roots_are_trusted_for_the_session(served, monkeypatch, rdu_source, tmp_path):
    """A path typed on the command line is the user's explicit choice: the CLI allows exactly that path."""
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(tmp_path / "elsewhere")]))
    result = runner.invoke(app, ["app", rdu_source])
    assert result.exit_code == 0, result.output
    (session,) = served
    roots = session.settings.io.allowed_roots
    assert str(Path(rdu_source).resolve()) in roots and str((tmp_path / "elsewhere").resolve()) in roots
    assert session.loaded.sources["io.allowed_roots"] == "session" and "allowing" in result.output
    assert session.registry.ids() == ["rdu-i40"]


def test_cli_does_not_widen_roots_when_already_allowed(served, monkeypatch, rdu_source):
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(Path(rdu_source).parent)]))
    result = runner.invoke(app, ["app", rdu_source])
    assert result.exit_code == 0 and "allowing" not in result.output
    assert served[0].loaded.sources["io.allowed_roots"] == "env"


def test_cli_expands_user_in_trusted_sources(served, monkeypatch, rdu_source, tmp_path):
    # HOME is a throwaway dir holding a copy of the network, never the committed fixture tree:
    # anything else that writes under ~ in this process (e.g. DuckDB installing its spatial
    # extension into ~/.duckdb) must not land in netstead/fixtures.
    home = tmp_path / "home"
    network = Path(shutil.copytree(rdu_source, home / "rdu_i40" / "parquet"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(tmp_path / "elsewhere")]))
    result = runner.invoke(app, ["app", "~/rdu_i40/parquet"])
    assert result.exit_code == 0, result.output
    assert served[0].registry.ids() == ["rdu-i40"]
    assert str(network.resolve()) in served[0].settings.io.allowed_roots


def test_cli_never_trusts_unsupported_schemes(served, monkeypatch, tmp_path):
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(tmp_path)]))
    result = runner.invoke(app, ["app", "ftp://example.invalid/net"])
    assert result.exit_code == 1 and "unsupported URL scheme" in result.output
    assert "allowing" not in result.output


def test_app_provider_alias_and_model_flag(served):
    result = runner.invoke(app, ["app", "--provider", "claude", "--model", "claude-haiku-4-5-20251001"])
    assert result.exit_code == 0, result.output
    select = served[0].settings.select
    assert (select.provider, select.model) == ("anthropic", "claude-haiku-4-5-20251001")


def test_widening_roots_builds_one_session_so_plugins_load_once(served, monkeypatch, rdu_source, tmp_path):
    """Plugins are third-party code with ``on_load`` side effects: discover and install them exactly once."""
    from netstead.workbench import session as session_module

    calls = []
    real_discover = session_module.discover

    def counting_discover(*args, **kwargs):
        calls.append(args)
        return real_discover(*args, **kwargs)

    monkeypatch.setattr(session_module, "discover", counting_discover)
    monkeypatch.setenv("NETSTEAD_IO__ALLOWED_ROOTS", json.dumps([str(tmp_path / "elsewhere")]))
    result = runner.invoke(app, ["app", rdu_source])
    assert result.exit_code == 0, result.output
    assert "allowing" in result.output and len(calls) == 1
    assert str(Path(rdu_source).resolve()) in served[0].settings.io.allowed_roots
