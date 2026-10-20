"""End-to-end: explicit ``credentials`` reach the listing *and* the reads of a remote package.

Regression tests for the credentials plumbing behind
``RemoteAdapter`` / ``Package.from_source(url, credentials=...)``. The
remote store is fsspec's in-process ``memory://`` filesystem, read
through the real csv / parquet adapters and the real IbisEngine — no
fakes between the credentials dict and the bytes.

Before these tests, a non-empty credentials dict made every csv and
parquet read fail (``Binder Error: Invalid named parameter
"storage_options"``), prefix listings dropped the bucket from the path
and the caller's credentials, and listed children came back without
their URL scheme.
"""

from __future__ import annotations

import importlib
import io
from collections.abc import Iterator
from types import ModuleType

# Self-registering inner adapters the remote adapter delegates to.
import corral.io.csv_adapter
import corral.io.parquet_adapter  # noqa: F401
import fsspec
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from corral.dataset import Package
from corral.engines import ibis_engine as engine_mod
from corral.engines.ibis_engine import IbisEngine
from corral.io import register_adapter
from fsspec.implementations.memory import MemoryFileSystem

_CREDS = {"key": "AKIA-explicit", "secret": "shh"}


@pytest.fixture
def engine() -> Iterator[IbisEngine]:
    e = IbisEngine()
    yield e
    e.close()


@pytest.fixture
def mem() -> Iterator[MemoryFileSystem]:
    """``memory://bucket/pkg/`` holding link.csv, node.csv, zone.parquet and listing junk."""
    fs = fsspec.filesystem("memory")
    fs.pipe("/bucket/pkg/link.csv", b"a,b\n1,2\n3,4\n")
    fs.pipe("/bucket/pkg/node.csv", b"a,b\n5,6\n")
    buf = io.BytesIO()
    pq.write_table(pa.table({"a": [1, 2, 3]}), buf)
    fs.pipe("/bucket/pkg/zone.parquet", buf.getvalue())
    fs.pipe("/bucket/pkg/_SUCCESS", b"")
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


@pytest.fixture
def remote_mod() -> ModuleType:
    """The live ``corral.io.remote`` module.

    Resolved per test rather than imported at collection time: a sibling
    test module re-imports ``corral.io.remote`` to check its
    self-registration, which leaves a module-level import here pointing
    at a stale copy that the package loader no longer reads.
    """
    return importlib.import_module("corral.io.remote")


@pytest.fixture
def no_ambient_credentials(monkeypatch, remote_mod):
    """The cascade finds nothing, so only an explicit dict can supply credentials."""
    monkeypatch.setattr(remote_mod, "resolve_credentials", lambda host, explicit=None: dict(explicit or {}))


@pytest.fixture
def memory_is_remote(monkeypatch, remote_mod):
    """Let the ``memory`` scheme stand in for ``s3`` everywhere the remote route is decided."""
    schemes = (*remote_mod.REMOTE_SCHEMES, "memory")
    monkeypatch.setattr(remote_mod, "REMOTE_SCHEMES", schemes)
    adapter = remote_mod.RemoteAdapter()
    adapter.schemes = schemes
    register_adapter(adapter)
    return adapter


def _count(expr) -> int:
    return int(expr.count().to_pyarrow().as_py())


# ---------------------------------------------------------------------------
# RemoteAdapter.read — the credentials dict must reach the bytes
# ---------------------------------------------------------------------------


def test_read_csv_with_explicit_credentials(engine, mem, no_ambient_credentials, remote_mod) -> None:
    expr = remote_mod.RemoteAdapter().read("memory://bucket/pkg/link.csv", engine, credentials=_CREDS)
    assert _count(expr) == 2


def test_read_parquet_with_explicit_credentials(engine, mem, no_ambient_credentials, remote_mod) -> None:
    expr = remote_mod.RemoteAdapter().read("memory://bucket/pkg/zone.parquet", engine, credentials=_CREDS)
    assert _count(expr) == 3


# ---------------------------------------------------------------------------
# RemoteAdapter.scan — prefix listings
# ---------------------------------------------------------------------------


def test_scan_prefix_lists_children_as_full_urls(mem, no_ambient_credentials, remote_mod) -> None:
    """Children keep the scheme and bucket, so each one routes back through the remote adapter."""
    listing = remote_mod.RemoteAdapter().scan("memory://bucket/pkg/", credentials=_CREDS)

    assert {r.path for r in listing} == {
        "memory://bucket/pkg/link.csv",
        "memory://bucket/pkg/node.csv",
        "memory://bucket/pkg/zone.parquet",
    }
    assert {r.name: r.format for r in listing} == {"link": "csv", "node": "csv", "zone": "parquet"}


def test_scan_prefix_uses_the_explicit_credentials(mem, monkeypatch, no_ambient_credentials, remote_mod) -> None:
    calls: list[dict] = []
    real = remote_mod.url_to_fs

    def spy(url, **storage_options):
        calls.append(dict(storage_options))
        return real(url, **storage_options)

    monkeypatch.setattr(remote_mod, "url_to_fs", spy)

    remote_mod.RemoteAdapter().scan("memory://bucket/pkg/", credentials=_CREDS)

    assert calls == [_CREDS]


# ---------------------------------------------------------------------------
# Package.from_source — listing + reads, one credentials dict
# ---------------------------------------------------------------------------


def test_package_from_remote_prefix_with_explicit_credentials(
    engine, mem, monkeypatch, no_ambient_credentials, memory_is_remote
) -> None:
    read_opts: list[dict] = []
    real = engine_mod.url_to_fs

    def spy(url, **storage_options):
        read_opts.append(dict(storage_options))
        return real(url, **storage_options)

    monkeypatch.setattr(engine_mod, "url_to_fs", spy)

    pkg = Package.from_source("memory://bucket/pkg/", engine=engine, credentials=_CREDS)

    assert set(pkg.keys()) == {"link", "node", "zone"}
    assert _count(pkg["link"].expr) == 2
    assert _count(pkg["zone"].expr) == 3
    # Every table read was served from a filesystem built with the caller's dict.
    assert read_opts and all(opts == _CREDS for opts in read_opts)
