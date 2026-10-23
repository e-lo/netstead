"""Tests for the generic corral FastAPI app (task 4.10 / issue #91).

End-to-end via :class:`fastapi.testclient.TestClient` so the auth +
endpoint contracts are exercised together. Auth tested in both modes
(``none`` and ``bearer``); endpoints tested against the Leavenworth
fixture mounted under a synthetic public id.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from corral.api import (
    AuthSettings,
    PackageRef,
    ServerSettings,
    build_app,
    generate_dev_token,
    load_settings,
)
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth


def _settings(*, auth_kind: str = "none", token: str | None = None) -> ServerSettings:
    """Build a test ServerSettings with the Leavenworth fixture mounted as ``demo``."""
    return ServerSettings(
        bind="127.0.0.1",
        port=8000,
        auth=AuthSettings(kind=auth_kind, token=token),
        packages=[PackageRef(id="demo", source=str(leavenworth.csv_dir()), description="Leavenworth fixture")],
    )


# ---------------------------------------------------------------------------
# Health (always open)
# ---------------------------------------------------------------------------


def test_health_always_open_with_bearer_auth():
    """`/health` does not require auth (load-balancer probe)."""
    settings = _settings(auth_kind="bearer", token="t0p-s3cret")
    client = TestClient(build_app(settings))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth — none
# ---------------------------------------------------------------------------


def test_endpoints_open_when_auth_none():
    """auth.kind='none' lets unauthenticated requests through."""
    client = TestClient(build_app(_settings(auth_kind="none")))
    assert client.get("/packages").status_code == 200
    assert client.get("/packages/demo").status_code == 200


# ---------------------------------------------------------------------------
# Auth — bearer
# ---------------------------------------------------------------------------


def test_endpoints_require_token_when_bearer():
    """No Bearer header -> 401 with WWW-Authenticate."""
    client = TestClient(build_app(_settings(auth_kind="bearer", token="abc")))
    r = client.get("/packages")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_endpoints_accept_correct_token():
    """Correct Bearer token -> 200."""
    client = TestClient(build_app(_settings(auth_kind="bearer", token="abc")))
    r = client.get("/packages", headers={"Authorization": "Bearer abc"})
    assert r.status_code == 200


def test_endpoints_reject_wrong_token():
    """Wrong Bearer token -> 401."""
    client = TestClient(build_app(_settings(auth_kind="bearer", token="abc")))
    r = client.get("/packages", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_bearer_without_token_in_settings_fails_fast():
    """AuthSettings(kind='bearer', token=None) is a misconfiguration."""
    settings = ServerSettings(auth=AuthSettings(kind="bearer", token=None))
    with pytest.raises(ValueError, match="requires `token`"):
        build_app(settings)


# ---------------------------------------------------------------------------
# Endpoint shapes
# ---------------------------------------------------------------------------


def test_list_packages_returns_configured_ids():
    """`/packages` returns the configured ids."""
    client = TestClient(build_app(_settings()))
    r = client.get("/packages")
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == "demo"
    assert "source" in body[0]


def test_get_package_returns_metadata():
    """`/packages/{id}` returns table list + row counts."""
    client = TestClient(build_app(_settings()))
    r = client.get("/packages/demo")
    body = r.json()
    assert body["id"] == "demo"
    assert body["table_count"] >= 1
    assert all({"name", "rows", "columns"} <= t.keys() for t in body["tables"])


def test_get_package_404_on_unknown_id():
    """Unknown package id -> 404."""
    client = TestClient(build_app(_settings()))
    r = client.get("/packages/nope")
    assert r.status_code == 404


def test_get_spec_returns_resolved_datapackage():
    """`/packages/{id}/spec` returns the Frictionless DataPackage as JSON."""
    client = TestClient(build_app(_settings()))
    r = client.get("/packages/demo/spec")
    body = r.json()
    assert "name" in body
    assert "resources" in body


def test_validate_package_api_returns_canonical_to_dict_shape():
    """`POST /packages/{id}/validate` returns the full ValidationReport.to_dict shape.

    Canonical shape (was ``{issues, spec_version}`` before; now the full
    ``ValidationReport.to_dict()`` document) — the wire shape is shared
    across api + mcp + netstead (F1, schema parity).
    """
    client = TestClient(build_app(_settings()))
    r = client.post("/packages/demo/validate")
    body = r.json()
    assert {"report_version", "spec_version", "source", "created_at", "metadata", "summary", "issues"} <= body.keys()
    assert isinstance(body["issues"], list)
    assert body["report_version"] == "1"


# ---------------------------------------------------------------------------
# Security warnings
# ---------------------------------------------------------------------------


def test_warn_on_unsafe_combination_emits_log(caplog):
    """auth=none + public bind logs a WARNING."""
    settings = ServerSettings(bind="0.0.0.0", auth=AuthSettings(kind="none"))
    with caplog.at_level("WARNING"):
        settings.warn_on_unsafe_combinations()
    assert any("NO authentication" in r.message for r in caplog.records)


def test_localhost_with_no_auth_does_not_warn(caplog):
    """auth=none + localhost is fine — no warning."""
    settings = ServerSettings(bind="127.0.0.1", auth=AuthSettings(kind="none"))
    with caplog.at_level("WARNING"):
        settings.warn_on_unsafe_combinations()
    assert not any("NO authentication" in r.message for r in caplog.records)


def test_is_public_bind_detects_known_loopback_aliases():
    """127.0.0.1 / localhost / ::1 are NOT public; everything else is."""
    for loopback in ("127.0.0.1", "localhost", "::1"):
        assert ServerSettings(bind=loopback).is_public_bind() is False
    assert ServerSettings(bind="0.0.0.0").is_public_bind() is True


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_settings_json(tmp_path):
    """JSON config round-trips to ServerSettings."""
    cfg = tmp_path / "server.json"
    cfg.write_text(
        '{"bind": "127.0.0.1", "port": 9000, "auth": {"kind": "bearer", "token": "abc"}, '
        '"packages": [{"id": "demo", "source": "/tmp/foo"}]}'
    )
    settings = load_settings(cfg)
    assert settings.port == 9000
    assert settings.auth.token == "abc"
    assert settings.packages[0].id == "demo"


def test_generate_dev_token_returns_url_safe_string():
    """Dev token helper returns a 40+ char URL-safe string."""
    t = generate_dev_token()
    assert isinstance(t, str) and len(t) >= 40


# ---------------------------------------------------------------------------
# PackageRegistry: require, source_for, loader injection (F2, F4) — replaces
# three duplicate _safe_get-style helpers that had drifted across callers.
# ---------------------------------------------------------------------------


def test_registry_require_returns_package_for_known_id():
    """`registry.require(id)` returns the loaded package — public version of _safe_get."""
    from corral.api import PackageRegistry

    settings = _settings()
    registry = PackageRegistry(settings)
    pkg = registry.require("demo")
    assert pkg.tables  # non-empty load


def test_registry_require_raises_http_404_for_unknown_id():
    """`registry.require(missing)` raises fastapi.HTTPException(404)."""
    from corral.api import PackageRegistry
    from fastapi import HTTPException

    settings = ServerSettings(packages=[])
    registry = PackageRegistry(settings)
    with pytest.raises(HTTPException) as exc_info:
        registry.require("nope")
    assert exc_info.value.status_code == 404
    assert "'nope'" in exc_info.value.detail


def test_registry_source_for_returns_string_without_loading():
    """`registry.source_for(id)` returns the configured source path without materializing."""
    from corral.api import PackageRegistry

    settings = ServerSettings(packages=[PackageRef(id="demo", source="/tmp/some/path/")])
    registry = PackageRegistry(settings)
    assert registry.source_for("demo") == "/tmp/some/path/"


def test_registry_source_for_raises_keyerror_for_unknown_id():
    from corral.api import PackageRegistry

    settings = ServerSettings(packages=[])
    registry = PackageRegistry(settings)
    with pytest.raises(KeyError):
        registry.source_for("nope")


def test_registry_package_loader_injection_caches_subclass_instances():
    """Passing a custom loader makes the cache hold the domain-typed instance."""
    from corral.api import PackageRegistry
    from netstead import Network

    settings = _settings()
    registry = PackageRegistry(settings, loader=Network.from_source)
    loaded = registry.require("demo")
    # The cache now holds a Network (subclass of Package), not a bare Package.
    assert isinstance(loaded, Network)
    assert loaded.spec_version  # GMNS-specific attribute available directly
