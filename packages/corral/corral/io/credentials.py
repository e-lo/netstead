"""Credentials cascade resolver for remote (URL) sources.

Resolves an fsspec-shaped ``storage_options`` dict for a given host by
walking a fixed cascade:

    1. ``explicit`` kwarg (caller's dict)         -- highest precedence
    2. ``CORRAL_CRED_<HOST_UPPER>_TOKEN`` env var
       (or ``_KEY`` + ``_SECRET`` for S3-style creds)
    3. ``keyring.get_password("corral", host)`` (optional dep)
    4. ``netrc`` lookup (stdlib)
    5. ``{}``                                     -- lowest precedence

The resolver **never raises on missing credentials** -- the underlying
storage backend will raise its own auth error when the request actually
fails. That keeps "no credentials available" silent and "wrong
credentials" loud, which is what users want.

**Security:**

- Credential *values* are never logged. Only host names are logged at
  debug level, and only when explicitly enabled. See ``test_no_creds_logged``.
- ``explicit`` is shallow-copied before return to avoid the caller
  later mutating what they handed us (which would mutate our return).
"""

from __future__ import annotations

import logging
import netrc
import os
from typing import Any, Final

__all__ = ["credential_source", "resolve_credentials", "system_keyring"]

_logger: Final = logging.getLogger(__name__)

# Service name used as the keyring "service" key. Single namespace for the
# whole project so users don't have to deal with per-format keys.
_KEYRING_SERVICE: Final = "corral"


def resolve_credentials(host: str, *, explicit: dict | None = None) -> dict:
    """Resolve fsspec ``storage_options`` for ``host`` via the cascade.

    Args:
        host: Network host (e.g. ``"s3.amazonaws.com"``,
            ``"example.com:8080"``). Port suffixes are stripped.
        explicit: Caller-provided storage_options. Takes precedence over
            every other layer. ``None`` (default) means consult the
            cascade.

    Returns:
        A dict suitable for ``fsspec.open(..., **storage_options)``.
        Typical shapes:

        - Bearer token: ``{"token": "..."}`` (env ``_TOKEN``, keyring)
        - S3-style:     ``{"key": "...", "secret": "..."}`` (env
          ``_KEY`` + ``_SECRET``)
        - HTTP basic:   ``{"username": "...", "password": "..."}`` (netrc)
        - Empty:        ``{}`` when no layer produced anything --
          the underlying backend will surface its own auth error.

    Examples:
        Explicit creds beat the env var:

        >>> import os
        >>> os.environ["CORRAL_CRED_DOCTEST_HOST_TOKEN"] = "from-env"
        >>> resolve_credentials(
        ...     "doctest.host", explicit={"token": "from-arg"}
        ... )
        {'token': 'from-arg'}
        >>> del os.environ["CORRAL_CRED_DOCTEST_HOST_TOKEN"]

        A host with no credentials anywhere returns an empty dict --
        not an exception:

        >>> resolve_credentials("no.such.host.example") == {}
        True
    """
    # Layer 1: explicit kwarg wins.
    if explicit is not None:
        # Shallow copy so caller mutations don't bleed into our return.
        return dict(explicit)

    sanitized = _sanitize_host(host)

    # Layer 2: env vars.
    creds = _lookup_env(sanitized)
    if creds:
        return creds

    # Layer 3: keyring (optional).
    creds = _lookup_keyring(host)
    if creds:
        return creds

    # Layer 4: netrc.
    creds = _lookup_netrc(host)
    if creds:
        return creds

    # Layer 5: nothing.
    return {}


def credential_source(host: str) -> str:
    """Name the cascade layer that would supply credentials for ``host``, never the values.

    Walks the same env → keyring → netrc order as :func:`resolve_credentials`
    (there is no ``explicit`` layer: callers that pass one already know).
    Safe to show in a UI ("credentials from: env").

    Args:
        host: Network host (port suffixes are ignored, as in the resolver).

    Returns:
        ``"env"``, ``"keyring"``, ``"netrc"``, or ``"none"``.

    Examples:
        >>> credential_source("no.such.host.example")
        'none'
    """
    if _lookup_env(_sanitize_host(host)):
        return "env"
    if _lookup_keyring(host):
        return "keyring"
    if _lookup_netrc(host):
        return "netrc"
    return "none"


#: Modules of the real OS-backed keyring backends this function trusts. Anything else —
#: notably ``keyrings.alt.*`` (plaintext/obfuscated file backends, e.g.
#: ``keyrings.alt.file.PlaintextKeyring`` at priority 0.5) — is rejected even if a hostile
#: ``PYTHON_KEYRING_BACKEND`` env var or ``keyringrc.cfg`` selects it and gives it a
#: priority above the fail backend's.
_REAL_BACKEND_MODULES: Final = frozenset(
    {
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
        "keyring.backends.kwallet",
    }
)
_CHAINER_MODULE: Final = "keyring.backends.chainer"


def _is_real_os_backend(backend: Any) -> bool:
    """Whether ``backend`` (or, for a chainer, at least one of its sub-backends) is OS-trusted."""
    module = type(backend).__module__
    if module == _CHAINER_MODULE:
        return any(_is_real_os_backend(b) for b in getattr(backend, "backends", ()))
    return module in _REAL_BACKEND_MODULES


def system_keyring() -> Any | None:
    """Return the ``keyring`` module when a real OS keyring backend is active, else ``None``.

    ``None`` when the optional ``keyring`` package is missing (or disabled with
    ``sys.modules['keyring'] = None``), when its active backend is the fail/null backend
    (priority <= 0: headless CI, containers, WSL without a secret service), or when the
    active backend is not one of the trusted OS-native backends (:data:`_REAL_BACKEND_MODULES`) —
    this rejects plaintext/file-based backends such as ``keyrings.alt.file.PlaintextKeyring``
    even though their priority (0.5) clears the fail-backend check. Callers use this one
    answer to decide whether secrets can be stored in a keyring at all.
    """
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    if keyring is None:  # type: ignore[unreachable]
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # boundary: a broken backend configuration means "no usable keyring", never a crash
        return None
    if getattr(backend, "priority", 0) <= 0:
        return None
    if not _is_real_os_backend(backend):
        return None
    return keyring


# ---------------------------------------------------------------------------
# Layer helpers (module-level so tests can monkeypatch them individually).
# ---------------------------------------------------------------------------


def _sanitize_host(host: str) -> str:
    """Normalize ``host`` into the env-var-fragment form.

    Drops any port suffix (``:9000``), lowercases, then replaces ``.``
    and ``-`` with ``_``. The result is upper-cased by callers when used
    in env var lookups.
    """
    # Strip user/auth that some URLs carry; we only care about the host:port tail.
    bare = host.split("@")[-1]
    # Drop port.
    bare = bare.split(":")[0]
    bare = bare.strip().lower()
    return bare.replace(".", "_").replace("-", "_")


def _lookup_env(sanitized_host: str) -> dict:
    """Read ``CORRAL_CRED_<HOST>_TOKEN`` (or ``_KEY`` + ``_SECRET``).

    Returns ``{}`` if no relevant env var is set or all are empty.
    Empty-string env values are treated as "missing", not "empty
    credential" -- handing fsspec ``token=""`` would silently produce
    a confusing auth error.
    """
    prefix = f"CORRAL_CRED_{sanitized_host.upper()}"

    token = os.environ.get(f"{prefix}_TOKEN", "")
    if token:
        return {"token": token}

    key = os.environ.get(f"{prefix}_KEY", "")
    secret = os.environ.get(f"{prefix}_SECRET", "")
    if key and secret:
        return {"key": key, "secret": secret}

    return {}


def _lookup_keyring(host: str) -> dict:
    """Look up a bearer token in the system keyring.

    Optional dependency: ``[keyring]`` extra. When the ``keyring``
    package is missing (or has been disabled with
    ``sys.modules['keyring'] = None``), this returns ``{}`` silently.

    Headless environments (CI, containers, WSL without an unlocked
    secret service) have ``keyring`` installed but no usable backend.
    The library raises ``NoKeyringError`` from a fail-backend; treat
    that as "no credentials" rather than letting it propagate — the
    cascade contract is "never raise on missing".
    """
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return {}
    # ``sys.modules['keyring'] = None`` (used by tests to simulate "not
    # installed") makes the import succeed but the binding is None.
    if keyring is None:  # type: ignore[unreachable]
        return {}

    try:
        value = keyring.get_password(_KEYRING_SERVICE, host)
    except keyring.errors.KeyringError:
        return {}
    if value:
        return {"token": value}
    return {}


def _lookup_netrc(host: str) -> dict:
    """Read ``~/.netrc`` (or ``$NETRC``) for ``host``.

    A missing or unparsable file means "no credentials" rather than an
    error, because the cascade's contract is "never raise on missing". A
    file that exists but cannot be read (permissions, a directory) is
    skipped too, but with a warning naming the file: the user put it there
    and will want to know why it is ignored. The parse error's text is not
    logged; it can quote the file's tokens.
    """
    netrc_path = os.environ.get("NETRC")
    try:
        rc = netrc.netrc(netrc_path) if netrc_path else netrc.netrc()
    except (FileNotFoundError, netrc.NetrcParseError):
        return {}
    except OSError as exc:
        _logger.warning(
            "Could not read netrc file %s (%s); skipping the netrc layer for host=%s",
            netrc_path or "~/.netrc",
            exc.strerror or type(exc).__name__,
            host,
        )
        return {}

    auth = rc.authenticators(host)
    if not auth:
        return {}

    login, _account, password = auth
    out: dict = {}
    if login:
        out["username"] = login
    if password:
        out["password"] = password
    return out
