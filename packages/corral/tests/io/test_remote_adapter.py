"""Tests for the RemoteAdapter (URL layer over fsspec).

The RemoteAdapter is the second of two dispatch layers: it accepts any
URL with a known scheme, resolves credentials, opens the bytes via
fsspec, then **re-dispatches** to the inner-format adapter (csv,
parquet, ...) based on the URL's extension or an explicit ``format=``
kwarg.

These tests register fake inner adapters for csv/parquet so they don't
depend on the sibling tasks (1.7-1.10) landing first.
"""

from __future__ import annotations

import sys
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from corral.io import (
    FormatAdapter,
    _clear_registry,
    dispatch,
    list_adapters,
    register_adapter,
)

from ._fakes import FakeCsvAdapter, FakeParquetAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_registry():
    """Registry seeded with the RemoteAdapter + fake inner adapters.

    The fake csv / parquet adapters echo their arguments through ``read``
    so tests can assert that ``storage_options`` flowed through.
    """
    from corral.io.remote import RemoteAdapter

    _clear_registry()

    class _EchoCsv(FakeCsvAdapter):
        last_kwargs: ClassVar[dict] = {}

        def read(self, source, engine, schema=None, **kwargs):  # type: ignore[override]
            type(self).last_kwargs = dict(kwargs)
            return ("csv-read", str(source), kwargs)

    class _EchoParquet(FakeParquetAdapter):
        last_kwargs: ClassVar[dict] = {}

        def read(self, source, engine, schema=None, **kwargs):  # type: ignore[override]
            type(self).last_kwargs = dict(kwargs)
            return ("parquet-read", str(source), kwargs)

    csv = _EchoCsv()
    parquet = _EchoParquet()
    remote = RemoteAdapter()

    register_adapter(csv)
    register_adapter(parquet)
    register_adapter(remote)

    yield {"csv": csv, "parquet": parquet, "remote": remote}
    _clear_registry()


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def test_probe_http_url(remote_registry) -> None:
    remote = remote_registry["remote"]
    assert remote.probe("https://example.com/foo.parquet") is True
    assert remote.probe("http://example.com/foo.csv") is True
    assert remote.probe("local/path.parquet") is False


def test_probe_s3_url(remote_registry) -> None:
    remote = remote_registry["remote"]
    assert remote.probe("s3://bucket/key.csv") is True
    assert remote.probe("gs://bucket/key.parquet") is True
    assert remote.probe("az://container/blob") is True


def test_probe_never_raises(remote_registry) -> None:
    """probe must be total -- malformed input returns False, not raises."""
    remote = remote_registry["remote"]
    for source in (None, 42, b"bytes", {"not": "a string"}, "", "://oops"):
        try:
            result = remote.probe(source)  # type: ignore[arg-type]
        except Exception as exc:
            pytest.fail(f"probe raised on {source!r}: {exc}")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------


def test_self_registers_on_import(monkeypatch) -> None:
    """Importing remote module installs the adapter."""
    _clear_registry()
    # Force a fresh import so the module-level register_adapter call fires
    # against the just-cleared registry; monkeypatch puts the original
    # module object (and the package attribute pointing at it) back so later
    # tests keep the stock RemoteAdapter class.
    import corral.io

    monkeypatch.setattr(corral.io, "remote", corral.io.remote)
    monkeypatch.delitem(sys.modules, "corral.io.remote")
    import corral.io.remote

    assert "remote" in list_adapters()


def test_protocol_conformance() -> None:
    """RemoteAdapter satisfies the FormatAdapter Protocol."""
    from corral.io.remote import RemoteAdapter

    assert isinstance(RemoteAdapter(), FormatAdapter)


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


def test_dispatch_routes_http_url(remote_registry) -> None:
    """An http URL resolves to the RemoteAdapter via scheme match."""
    assert dispatch("https://example.com/foo.parquet") is remote_registry["remote"]


def test_dispatch_routes_s3_url(remote_registry) -> None:
    assert dispatch("s3://bucket/key.csv") is remote_registry["remote"]


def test_inner_format_detection(remote_registry) -> None:
    """The remote adapter's inner-format lookup picks the right sibling."""
    from corral.io.remote import RemoteAdapter

    remote: RemoteAdapter = remote_registry["remote"]

    assert remote._inner_adapter("https://example.com/foo.parquet").name == "parquet"
    assert remote._inner_adapter("s3://bucket/key.csv").name == "csv"
    # Explicit override beats the URL extension.
    assert remote._inner_adapter("s3://bucket/key.csv", format="parquet").name == "parquet"


# ---------------------------------------------------------------------------
# read() — credentials and dispatch
# ---------------------------------------------------------------------------


def test_read_with_explicit_credentials(remote_registry) -> None:
    """``credentials=`` kwarg is resolved and threaded into storage_options.

    No env vars: the explicit dict must win and reach the inner adapter
    untouched.
    """
    remote = remote_registry["remote"]
    csv_inner = remote_registry["csv"]

    explicit = {"key": "AKIA-explicit", "secret": "shh"}

    engine = MagicMock(name="engine")
    result = remote.read(
        "s3://bucket/data.csv",
        engine=engine,
        credentials=explicit,
    )

    # Inner adapter was invoked.
    assert result[0] == "csv-read"
    # storage_options surfaced into the inner adapter kwargs.
    storage = type(csv_inner).last_kwargs.get("storage_options")
    assert storage == {"key": "AKIA-explicit", "secret": "shh"}
    # ``credentials`` itself was consumed and not forwarded as a separate kwarg.
    assert "credentials" not in type(csv_inner).last_kwargs


def test_read_with_env_credentials(remote_registry, monkeypatch) -> None:
    """Env-var creds flow through when no explicit dict is provided."""
    remote = remote_registry["remote"]
    csv_inner = remote_registry["csv"]

    monkeypatch.setenv("CORRAL_CRED_BUCKET_SERVER_TOKEN", "env-bearer")

    engine = MagicMock(name="engine")
    remote.read("s3://bucket.server/data.csv", engine=engine)

    storage = type(csv_inner).last_kwargs.get("storage_options")
    assert storage == {"token": "env-bearer"}


def test_read_explicit_format_override(remote_registry) -> None:
    """``format=`` kwarg overrides the URL extension."""
    remote = remote_registry["remote"]
    parquet_inner = remote_registry["parquet"]

    engine = MagicMock(name="engine")
    # URL ends in .csv but caller forces parquet routing.
    result = remote.read(
        "s3://bucket/data.csv",
        engine=engine,
        format="parquet",
    )
    assert result[0] == "parquet-read"
    # Ensure the override kwarg was consumed, not forwarded.
    assert "format" not in type(parquet_inner).last_kwargs


def test_read_unknown_inner_format_raises(remote_registry) -> None:
    """A URL with no resolvable inner format yields a clear error."""
    from corral.io import FormatNotDetected

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    with pytest.raises(FormatNotDetected):
        remote.read("https://example.com/no-extension", engine=engine)


def test_read_does_not_recurse_into_self(remote_registry) -> None:
    """Inner dispatch must never resolve back to RemoteAdapter (infinite loop guard)."""
    from corral.io.remote import RemoteAdapter

    remote = remote_registry["remote"]
    # If the inner resolution were to pick remote again, calling read
    # would recurse forever. The internal _inner_adapter must filter
    # itself out.
    inner = remote._inner_adapter("s3://bucket/data.csv")
    assert not isinstance(inner, RemoteAdapter)


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


def test_write_to_http_raises_clear_error(remote_registry) -> None:
    """HTTP(S) is read-only -- writes must error with a clear message."""
    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    with pytest.raises(NotImplementedError) as exc:
        remote.write(MagicMock(), "https://example.com/foo.parquet", engine=engine)
    msg = str(exc.value).lower()
    assert "http" in msg


def test_write_to_s3_dispatches_to_inner(remote_registry, monkeypatch) -> None:
    """S3 writes thread through to the inner adapter with storage_options.

    Inner adapter doesn't actually touch S3 in this test -- it's our
    echo fake. We just verify the dispatch flowed through.
    """
    remote = remote_registry["remote"]
    parquet_inner = remote_registry["parquet"]

    write_calls: list[tuple] = []

    def fake_write(expr, dest, engine, **kwargs):
        write_calls.append((dest, kwargs))

    monkeypatch.setattr(parquet_inner, "write", fake_write)

    monkeypatch.setenv("CORRAL_CRED_BUCKET_TOKEN", "wtoken")
    engine = MagicMock(name="engine")
    remote.write(MagicMock(name="expr"), "s3://bucket/out.parquet", engine=engine)

    assert len(write_calls) == 1
    dest, kwargs = write_calls[0]
    assert dest == "s3://bucket/out.parquet"
    assert kwargs.get("storage_options") == {"token": "wtoken"}


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


def test_scan_single_resource(remote_registry) -> None:
    """A URL pointing at a single file yields a one-element ResourceListing."""
    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    listing = remote.scan("https://example.com/data.parquet", engine=engine)
    assert isinstance(listing, list)
    assert len(listing) == 1
    assert listing[0].name == "data"
    assert listing[0].format == "parquet"


def test_scan_directory_via_fsspec_ls(remote_registry, monkeypatch) -> None:
    """An S3 prefix expands to the listing returned by fsspec.ls()."""
    from corral.io import remote as remote_mod

    fake_fs = MagicMock(name="fs")
    fake_fs.isdir.return_value = True
    fake_fs.ls.return_value = [
        "bucket/prefix/link.csv",
        "bucket/prefix/node.csv",
    ]

    monkeypatch.setattr(remote_mod, "url_to_fs", lambda url, **storage_options: (fake_fs, "bucket/prefix"))

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    listing = remote.scan("s3://bucket/prefix/", engine=engine)
    names = {r.name for r in listing}
    assert names == {"link", "node"}
    formats = {r.format for r in listing}
    assert formats == {"csv"}
    # Children are full URLs, so each one routes back through the remote adapter.
    assert {r.path for r in listing} == {"s3://bucket/prefix/link.csv", "s3://bucket/prefix/node.csv"}


# ---------------------------------------------------------------------------
# Optional: real-network integration test
# ---------------------------------------------------------------------------


def test_read_skips_storage_options_when_empty(remote_registry) -> None:
    """I1: ``storage_options`` is omitted entirely when credentials resolve to ``{}``.

    The ibis duckdb backend (and some polars paths) refuse an
    unexpected ``storage_options`` kwarg even when it's empty, so we
    don't synthesize an empty dict — we just leave the key out.
    """
    remote = remote_registry["remote"]
    csv_inner = remote_registry["csv"]

    engine = MagicMock(name="engine")
    # No env vars, no explicit creds → resolve_credentials() returns {}.
    remote.read("s3://no-host-without-creds/data.csv", engine=engine)

    forwarded = type(csv_inner).last_kwargs
    assert "storage_options" not in forwarded, (
        f"Expected empty storage_options to be omitted; got keys={list(forwarded)!r}"
    )


def test_write_to_http_uses_typed_exception(remote_registry) -> None:
    """I7: HTTP-write refusal raises :class:`WriteUnsupportedForSchemeError`.

    The typed exception subclasses ``NotImplementedError`` (for back-compat
    with existing catch-builtins callers) AND ``FormatError`` so downstream
    code can pattern-match the I/O-layer refusal.
    """
    from corral.io import FormatError, WriteUnsupportedForSchemeError

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    with pytest.raises(WriteUnsupportedForSchemeError) as exc:
        remote.write(MagicMock(), "https://example.com/foo.parquet", engine=engine)
    # Back-compat with the prior bare-NotImplementedError branch.
    assert isinstance(exc.value, NotImplementedError)
    assert isinstance(exc.value, FormatError)


def test_inner_adapter_loop_guard_hard_error(remote_registry, monkeypatch) -> None:
    """I5: a registry that loops back to RemoteAdapter raises FormatNotDetected.

    Implementation guard: the previous code had an implicit recursion guard
    (an ``adapter is self`` check that silently strip-and-retried). When
    that retry also resolved back to ``self``, the function returned ``self``
    and ``read`` would recurse infinitely. We now hard-raise instead.
    """
    from corral.io import _BY_EXT, FormatNotDetected
    from corral.io.remote import RemoteAdapter

    remote: RemoteAdapter = remote_registry["remote"]

    # Poison the registry: force every extension to resolve to ``remote``.
    _BY_EXT["csv"] = "remote"
    try:
        with pytest.raises(FormatNotDetected) as exc:
            remote._inner_adapter("s3://bucket/data.csv")
        assert "scheme strip" in str(exc.value).lower() or "cannot resolve" in str(exc.value).lower()
    finally:
        _BY_EXT.pop("csv", None)


def test_scan_directory_listing_filters_unknown_extensions(remote_registry, monkeypatch) -> None:
    """S1: ls output is filtered against the registered-extension map.

    Junk entries (``_SUCCESS``, ``manifest.json`` when JSON isn't
    registered) are dropped; entries owned by a registered adapter
    survive.
    """
    from corral.io import remote as remote_mod

    fake_fs = MagicMock(name="fs")
    fake_fs.isdir.return_value = True
    fake_fs.ls.return_value = [
        "bucket/prefix/link.csv",  # csv adapter is registered → keep
        "bucket/prefix/node.parquet",  # parquet adapter is registered → keep
        "bucket/prefix/_SUCCESS",  # no dot → drop
        "bucket/prefix/manifest.json",  # extension not registered → drop
    ]

    monkeypatch.setattr(remote_mod, "url_to_fs", lambda url, **storage_options: (fake_fs, "bucket/prefix"))

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")
    listing = remote.scan("s3://bucket/prefix/", engine=engine)
    formats = {r.format for r in listing}
    # Only adapter-owned extensions survive.
    assert formats == {"csv", "parquet"}


def test_scan_directory_listing_propagates_unexpected_errors(remote_registry, monkeypatch) -> None:
    """I4: unexpected (non-IO) errors from fsspec propagate; they are not silently swallowed.

    Only ``(FileNotFoundError, PermissionError, OSError)`` are coerced to
    single-resource fallback; a TypeError (or any other bug) bubbles up
    so it isn't papered over.
    """
    from corral.io import remote as remote_mod

    fake_fs = MagicMock(name="fs")

    def boom(_path):
        raise TypeError("simulated bug in fsspec.isdir")

    fake_fs.isdir.side_effect = boom

    monkeypatch.setattr(remote_mod, "url_to_fs", lambda url, **kw: (fake_fs, "bucket/prefix"))

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")
    with pytest.raises(TypeError, match="simulated bug"):
        remote.scan("s3://bucket/prefix/", engine=engine)


@pytest.mark.integration
def test_read_public_http_fixture(remote_registry) -> None:
    """Smoke test against a public-good HTTP fixture.

    Default-skipped: only runs under ``pytest -m integration``. The CI
    matrix flips the flag.

    Uses ``httpbin.org/anything`` because it's permanently free and
    returns a deterministic JSON body we can use polars/duckdb on.

    We don't actually need this to round-trip a parquet file -- the goal
    is to prove the URL → fsspec → inner-adapter chain reaches the
    network. We use a CSV-shaped URL (and route through the fake CSV
    inner adapter) so we don't depend on the sibling parquet adapter.
    """
    import urllib.request

    # Sanity-check: is the network even reachable from this runner?
    try:
        urllib.request.urlopen("https://httpbin.org/status/200", timeout=5)
    except Exception as exc:  # pragma: no cover - network gating
        pytest.skip(f"no network: {exc}")

    remote = remote_registry["remote"]
    engine = MagicMock(name="engine")

    # Even though our inner CSV adapter is a fake, the URL must resolve
    # cleanly through the cascade without raising.
    result = remote.read("https://httpbin.org/anything/file.csv", engine=engine)
    assert result[0] == "csv-read"


# ---------------------------------------------------------------------------
# Keep credentials out of error / log messages
# ---------------------------------------------------------------------------


def test_credentials_not_leaked_into_error(remote_registry, monkeypatch) -> None:
    """When the inner adapter raises, the storage_options dict must NOT be
    in the resulting traceback / error message verbatim.

    This guards against future ``logger.exception(f"failed with creds {storage}")``
    -style leaks in the read path.
    """
    remote = remote_registry["remote"]
    csv_inner = remote_registry["csv"]

    sentinel = "SENTINEL-DO-NOT-LEAK-19fjs"
    monkeypatch.setenv("CORRAL_CRED_LEAK_HOST_TOKEN", sentinel)

    def boom(*args: Any, **kwargs: Any) -> None:
        # Inner adapter explodes -- the wrapper must not re-emit the
        # storage_options dict in the error chain.
        raise RuntimeError("inner adapter blew up")

    monkeypatch.setattr(csv_inner, "read", boom)

    engine = MagicMock(name="engine")
    with pytest.raises(RuntimeError) as exc:
        remote.read("s3://leak.host/data.csv", engine=engine)
    assert sentinel not in str(exc.value)
    assert sentinel not in repr(exc.value)


def test_credentials_keyed_by_bare_hostname_not_netloc(remote_registry, monkeypatch) -> None:
    """``user:pw@host:port`` looks up credentials for ``host`` only; the userinfo never reaches the lookup."""
    from corral.io import remote as remote_mod

    seen: list[str] = []
    real = remote_mod.resolve_credentials

    def spy(host, *, explicit=None):
        seen.append(host)
        return real(host, explicit=explicit)

    monkeypatch.setattr(remote_mod, "resolve_credentials", spy)
    monkeypatch.setenv("CORRAL_CRED_BUCKET_SERVER_TOKEN", "env-bearer")

    remote_registry["remote"].read("https://user:pw@bucket.server:9000/data.csv", engine=MagicMock(name="engine"))

    assert seen == ["bucket.server"]
    assert type(remote_registry["csv"]).last_kwargs.get("storage_options") == {"token": "env-bearer"}


@pytest.mark.parametrize("source", ["s3:../x", "https:x", "http:/abs", "S3:..//x"])
def test_scheme_without_slashes_is_never_routed_as_remote(remote_registry, source) -> None:
    """``s3:../x`` is a relative local path to every inner adapter, so neither dispatch nor probe calls it remote."""
    from corral.io import _scheme_of

    assert _scheme_of(source) is None
    assert remote_registry["remote"].probe(source) is False


def test_scheme_with_slashes_is_still_remote(remote_registry) -> None:
    from corral.io import _scheme_of

    assert _scheme_of("S3://b/k.csv") == "s3"
    assert remote_registry["remote"].probe("S3://b/k.csv") is True
