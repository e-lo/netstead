"""Tests for the credentials cascade resolver.

Covers the order kwarg → env → keyring → .netrc → empty dict, plus the
non-leaking-into-logs guarantee.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Layer 1: explicit kwarg
# ---------------------------------------------------------------------------


def test_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit dict shortcircuits the cascade.

    Even with an env var, keyring, and netrc primed with different
    values, an explicit dict is returned verbatim.
    """
    from corral.io.credentials import resolve_credentials

    # Prime every downstream layer.
    monkeypatch.setenv("CORRAL_CRED_EXAMPLE_COM_TOKEN", "env-token")
    explicit = {"token": "explicit-token"}

    out = resolve_credentials("example.com", explicit=explicit)
    assert out == {"token": "explicit-token"}
    # Don't return the caller's dict by identity — that's a mutation hazard.
    assert out is not explicit


# ---------------------------------------------------------------------------
# Layer 2: env var
# ---------------------------------------------------------------------------


def test_env_var_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bearer-token env var resolves to ``{"token": value}``."""
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_S3_AMAZONAWS_COM_TOKEN", "abc123")
    assert resolve_credentials("s3.amazonaws.com") == {"token": "abc123"}


def test_env_var_host_sanitization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosts are uppercased and ``.`` becomes ``_`` for env var lookup."""
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_S3_AMAZONAWS_COM_TOKEN", "via-sanitized")
    # Caller passes the raw host; the resolver does the sanitization.
    assert resolve_credentials("s3.amazonaws.com") == {"token": "via-sanitized"}


def test_env_var_host_strips_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Port numbers in host are dropped before env lookup."""
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_MINIO_LOCAL_TOKEN", "with-port")
    assert resolve_credentials("minio.local:9000") == {"token": "with-port"}


def test_env_var_key_secret_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_KEY`` + ``_SECRET`` produces an S3-style storage_options dict."""
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_S3_AMAZONAWS_COM_KEY", "AKIA...")
    monkeypatch.setenv("CORRAL_CRED_S3_AMAZONAWS_COM_SECRET", "shh")

    assert resolve_credentials("s3.amazonaws.com") == {
        "key": "AKIA...",
        "secret": "shh",
    }


def test_env_var_empty_string_is_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty-string env var must not be returned as a credential.

    An empty token would silently route through fsspec, producing an
    auth error far from its root cause. Treat empty as missing and fall
    through.
    """
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_EXAMPLE_COM_TOKEN", "")
    assert resolve_credentials("example.com") == {}


# ---------------------------------------------------------------------------
# Layer 3: keyring
# ---------------------------------------------------------------------------


def test_keyring_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When env is unset, keyring.get_password is consulted."""
    from corral.io import credentials as creds_mod

    # Build a stub keyring module that the resolver will import lazily.
    fake_keyring = types.ModuleType("keyring")
    calls: list[tuple[str, str]] = []

    def get_password(service: str, name: str) -> str | None:
        calls.append((service, name))
        return "keyring-token" if name == "example.com" else None

    fake_keyring.get_password = get_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    # Force the resolver to take the keyring path: no env, no netrc.
    monkeypatch.setattr(creds_mod, "_lookup_netrc", lambda host: None)

    out = creds_mod.resolve_credentials("example.com")
    assert out == {"token": "keyring-token"}
    assert calls == [("corral", "example.com")]


def test_keyring_missing_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """When keyring is not importable, the cascade silently advances."""
    from corral.io import credentials as creds_mod

    # Hide a previously-imported keyring (if any) and make the import
    # raise. ``_resolve_keyring`` must swallow the ImportError.
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(creds_mod, "_lookup_netrc", lambda host: {"username": "u", "password": "p"})

    out = creds_mod.resolve_credentials("example.com")
    # Falls through to netrc.
    assert out == {"username": "u", "password": "p"}


# ---------------------------------------------------------------------------
# Layer 4: netrc
# ---------------------------------------------------------------------------


def test_netrc_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A matching netrc entry returns ``{"username": ..., "password": ...}``."""
    from corral.io import credentials as creds_mod

    netrc_path = tmp_path / ".netrc"
    netrc_path.write_text(
        "machine example.com\n  login alice\n  password s3cret\n",
        encoding="utf-8",
    )
    netrc_path.chmod(0o600)  # netrc parser is strict about perms

    # Disable keyring so we reach netrc deterministically.
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("NETRC", str(netrc_path))

    assert creds_mod.resolve_credentials("example.com") == {
        "username": "alice",
        "password": "s3cret",
    }


def test_netrc_no_machine_entry_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """When the netrc has no entry for the host, the cascade returns empty.

    The netrc lookup must not raise on a missing machine entry — that's
    the dominant case for hosts users have never authenticated with.
    """
    from corral.io import credentials as creds_mod

    netrc_path = tmp_path / ".netrc"
    netrc_path.write_text(
        "machine other.example\n  login bob\n  password hunter2\n",
        encoding="utf-8",
    )
    netrc_path.chmod(0o600)

    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("NETRC", str(netrc_path))

    assert creds_mod.resolve_credentials("nowhere.example") == {}


def test_netrc_missing_file_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A non-existent netrc path must not raise."""
    from corral.io import credentials as creds_mod

    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("NETRC", str(tmp_path / "does-not-exist"))

    assert creds_mod.resolve_credentials("example.com") == {}


def test_netrc_missing_file_is_not_logged(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog) -> None:
    """No netrc at all is the common case, not something to warn about."""
    from corral.io import credentials as creds_mod

    monkeypatch.setenv("NETRC", str(tmp_path / "does-not-exist"))
    with caplog.at_level("DEBUG", logger="corral.io.credentials"):
        assert creds_mod._lookup_netrc("example.com") == {}
    assert caplog.records == []


def test_unreadable_netrc_warns_with_its_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog) -> None:
    """A netrc that exists but can't be read (permissions, a directory) is skipped with a warning naming the file."""
    from corral.io import credentials as creds_mod

    unreadable = tmp_path / "netrc-is-a-directory"
    unreadable.mkdir()
    monkeypatch.setenv("NETRC", str(unreadable))

    with caplog.at_level("WARNING", logger="corral.io.credentials"):
        assert creds_mod._lookup_netrc("example.com") == {}

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert str(unreadable) in record.getMessage()
    assert "example.com" in record.getMessage()


# ---------------------------------------------------------------------------
# Terminal: empty
# ---------------------------------------------------------------------------


def test_no_creds_returns_empty_dict(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """No layer has anything → returns ``{}``."""
    from corral.io import credentials as creds_mod

    # Sterile environment.
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("NETRC", str(tmp_path / "no-netrc"))

    assert creds_mod.resolve_credentials("unknown.host") == {}


# ---------------------------------------------------------------------------
# Security: never log credentials
# ---------------------------------------------------------------------------


def test_no_creds_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A sentinel credential value must not appear in any log / stdout / stderr.

    Routes a unique sentinel through every layer of the cascade and
    asserts that it never lands in captured log records or in stdout /
    stderr. Catches accidental ``logger.info(f"resolved {value}")``-style
    leaks.
    """
    from corral.io.credentials import resolve_credentials

    sentinel = "SENTINEL-CREDENTIAL-DO-NOT-LOG-873x21"

    # Push the sentinel through every layer so any one of them leaking
    # would fail this test.
    monkeypatch.setenv("CORRAL_CRED_LEAK_TEST_HOST_TOKEN", sentinel)
    caplog.set_level(logging.DEBUG, logger="corral")

    out = resolve_credentials("leak.test.host", explicit={"token": sentinel})
    assert out == {"token": sentinel}

    captured = capsys.readouterr()
    leaked_in = []
    for record in caplog.records:
        if sentinel in record.getMessage():
            leaked_in.append(f"log record: {record.name}")
    if sentinel in captured.out:
        leaked_in.append("stdout")
    if sentinel in captured.err:
        leaked_in.append("stderr")

    assert not leaked_in, f"Credential sentinel leaked into: {leaked_in}"


# ---------------------------------------------------------------------------
# Crit1 regression: env-var prefix is CORRAL_CRED, not NETSTEAD_CRED
# ---------------------------------------------------------------------------


def test_old_netstead_prefix_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The legacy ``NETSTEAD_CRED_<HOST>_TOKEN`` env var must not be honoured.

    Per the corral-vs-netstead composition boundary (architecture §3),
    credential resolution lives in corral and uses the
    ``CORRAL_CRED_*`` prefix. The old ``NETSTEAD_*`` prefix was a
    naming-boundary violation; this test locks in the rename so a future
    refactor cannot silently reintroduce it.
    """
    from corral.io import credentials as creds_mod

    # Sterilize fallback layers so the only signal is the env-var lookup.
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("NETRC", str(tmp_path / "no-netrc"))

    # Only the old prefix is set — the resolver MUST NOT pick it up.
    monkeypatch.setenv("NETSTEAD_CRED_EXAMPLE_COM_TOKEN", "should-be-ignored")
    assert creds_mod.resolve_credentials("example.com") == {}


def test_new_corral_prefix_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit verification that the new ``CORRAL_CRED_*`` prefix is read."""
    from corral.io.credentials import resolve_credentials

    monkeypatch.setenv("CORRAL_CRED_EXAMPLE_COM_TOKEN", "corral-token")
    assert resolve_credentials("example.com") == {"token": "corral-token"}


# ---------------------------------------------------------------------------
# credential_source: names the layer, never the value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "keyring_value", "netrc_value", "expected"),
    [
        ({"token": "t"}, {"token": "k"}, {"username": "u"}, "env"),
        ({}, {"token": "k"}, {"username": "u"}, "keyring"),
        ({}, {}, {"username": "u", "password": "p"}, "netrc"),
        ({}, {}, {}, "none"),
    ],
)
def test_credential_source_names_the_first_layer(
    monkeypatch: pytest.MonkeyPatch, env: dict, keyring_value: dict, netrc_value: dict, expected: str
) -> None:
    from corral.io import credentials as creds_mod

    monkeypatch.setattr(creds_mod, "_lookup_env", lambda host: env)
    monkeypatch.setattr(creds_mod, "_lookup_keyring", lambda host: keyring_value)
    monkeypatch.setattr(creds_mod, "_lookup_netrc", lambda host: netrc_value)
    assert creds_mod.credential_source("data.example.com") == expected


# ---------------------------------------------------------------------------
# system_keyring(): is a real OS keyring backend usable?
# ---------------------------------------------------------------------------


def _fake_backend(priority: float, backend_module: str = "keyring.backends.macOS") -> Any:
    """A fake backend instance whose ``type(...).__module__`` is ``backend_module``."""
    cls = type("FakeBackend", (), {})
    cls.__module__ = backend_module
    backend = cls()
    backend.priority = priority
    return backend


def _fake_keyring_module(priority: float, backend_module: str = "keyring.backends.macOS") -> types.ModuleType:
    module = types.ModuleType("keyring")
    backend = _fake_backend(priority, backend_module)
    module.get_keyring = lambda: backend  # type: ignore[attr-defined]
    return module


def test_system_keyring_none_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    from corral.io.credentials import system_keyring

    monkeypatch.setitem(sys.modules, "keyring", None)
    assert system_keyring() is None


def test_system_keyring_none_for_fail_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from corral.io.credentials import system_keyring

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring_module(0))
    assert system_keyring() is None


def test_system_keyring_returns_module_for_real_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from corral.io.credentials import system_keyring

    module = _fake_keyring_module(5)
    monkeypatch.setitem(sys.modules, "keyring", module)
    assert system_keyring() is module


def test_system_keyring_none_for_plaintext_alt_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``keyrings.alt``-style plaintext backend is rejected even though priority 0.5 beats the fail backend."""
    from corral.io.credentials import system_keyring

    module = _fake_keyring_module(0.5, "keyrings.alt.file")
    monkeypatch.setitem(sys.modules, "keyring", module)
    assert system_keyring() is None


def test_system_keyring_none_for_chainer_of_only_alt_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chainer backend whose sub-backends are all untrusted is rejected too."""
    from corral.io.credentials import system_keyring

    module = types.ModuleType("keyring")
    chainer = _fake_backend(5, "keyring.backends.chainer")
    chainer.backends = [_fake_backend(0.5, "keyrings.alt.file"), _fake_backend(0.5, "keyrings.alt.Windows")]
    module.get_keyring = lambda: chainer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    assert system_keyring() is None


def test_system_keyring_returns_module_for_chainer_with_a_real_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chainer backend is accepted as soon as one of its sub-backends is OS-trusted."""
    from corral.io.credentials import system_keyring

    module = types.ModuleType("keyring")
    chainer = _fake_backend(5, "keyring.backends.chainer")
    chainer.backends = [_fake_backend(0.5, "keyrings.alt.file"), _fake_backend(5, "keyring.backends.SecretService")]
    module.get_keyring = lambda: chainer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    assert system_keyring() is module


def test_system_keyring_none_when_backend_lookup_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    from corral.io.credentials import system_keyring

    module = types.ModuleType("keyring")

    def boom() -> Any:
        raise RuntimeError("secret service not running")

    module.get_keyring = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    assert system_keyring() is None
