"""``storage_options`` on the IbisEngine read/write primitives.

A URL source read with ``storage_options`` (the fsspec options the
credentials cascade produces) must be served through an fsspec
filesystem built from those options — not handed to duckdb's own
``read_csv`` / ``read_parquet`` table functions, which reject
``storage_options`` as an unknown named parameter.

The tests use fsspec's in-process ``memory://`` filesystem as the
store, and a spy on the engine's fsspec entry point to prove that the
exact dict the caller passed is what the filesystem was built from —
for ``s3://``, ``gs://`` and ``az://`` URLs alike, with no network.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import fsspec
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from corral.engines import ibis_engine as engine_mod
from corral.engines.errors import InvalidEngineCallError
from corral.engines.ibis_engine import IbisEngine
from fsspec.implementations.memory import MemoryFileSystem

_CSV = b"a,b\n1,2\n3,4\n"


@pytest.fixture
def engine() -> Iterator[IbisEngine]:
    e = IbisEngine()
    yield e
    e.close()


@pytest.fixture
def mem() -> Iterator[MemoryFileSystem]:
    """A ``memory://`` filesystem holding ``bucket/pkg/link.{csv,parquet}``."""
    fs = fsspec.filesystem("memory")
    fs.pipe("/bucket/pkg/link.csv", _CSV)
    buf = io.BytesIO()
    pq.write_table(pa.table({"a": [1, 2, 3]}), buf)
    fs.pipe("/bucket/pkg/link.parquet", buf.getvalue())
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


@pytest.fixture
def fsspec_spy(monkeypatch, mem):
    """Record the ``(url, storage_options)`` the engine builds its filesystem from.

    The spy answers every URL with the memory store: ``s3://bucket/...``
    is served from ``memory://bucket/...``. That keeps the cloud-URL
    tests offline while still exercising the whole duckdb-through-fsspec
    read path.
    """
    calls: list[tuple[str, dict]] = []
    real = engine_mod.url_to_fs

    def spy(url: str, **storage_options):
        calls.append((url, dict(storage_options)))
        _, path = real("memory://bucket")
        return mem, path

    monkeypatch.setattr(engine_mod, "url_to_fs", spy)
    return calls


def _count(expr) -> int:
    return int(expr.count().to_pyarrow().as_py())


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def test_read_csv_with_storage_options_is_served_through_fsspec(engine, mem) -> None:
    expr = engine.read_csv("memory://bucket/pkg/link.csv", storage_options={"token": "t"})
    assert _count(expr) == 2


def test_read_parquet_with_storage_options_is_served_through_fsspec(engine, mem) -> None:
    expr = engine.read_parquet("memory://bucket/pkg/link.parquet", storage_options={"token": "t"})
    assert _count(expr) == 3


@pytest.mark.parametrize(
    ("url", "creds"),
    [
        ("s3://bucket/pkg/link.csv", {"key": "AKIA-explicit", "secret": "shh"}),
        ("gs://bucket/pkg/link.csv", {"token": "/path/to/service-account.json"}),
        ("az://bucket@acct/pkg/link.csv", {"account_name": "acct", "account_key": "a2V5"}),
    ],
)
def test_explicit_storage_options_reach_the_cloud_filesystem(engine, fsspec_spy, url, creds) -> None:
    """The caller's dict — unchanged — is what the s3/gs/az filesystem is built from."""
    expr = engine.read_csv(url, storage_options=creds)

    assert _count(expr) == 2
    assert len(fsspec_spy) == 1
    built_url, built_opts = fsspec_spy[0]
    assert built_url.startswith(url.split("/pkg/")[0])
    assert built_opts == creds


def test_storage_options_are_not_forwarded_to_duckdb(engine, mem) -> None:
    """Regression: duckdb's table functions reject ``storage_options`` as a named parameter."""
    import duckdb

    try:
        engine.read_csv("memory://bucket/pkg/link.csv", storage_options={"token": "t"}).to_pyarrow()
    except duckdb.BinderException as exc:  # pragma: no cover - the bug
        pytest.fail(f"storage_options leaked into duckdb: {exc}")


def test_different_storage_options_for_one_url_are_isolated(engine, monkeypatch, mem) -> None:
    """Two reads of the same URL with different credentials never share a filesystem.

    Each credential set is served from its own store; a lazy expression
    from the first read keeps answering from *its* store after the second
    read registered another.
    """
    mem.pipe("/tenant-a/pkg/link.csv", b"a\n1\n")
    mem.pipe("/tenant-b/pkg/link.csv", b"a\n1\n2\n3\n")

    def by_credentials(url: str, **storage_options):
        root = "/tenant-a" if storage_options["key"] == "A" else "/tenant-b"
        return mem, root

    monkeypatch.setattr(engine_mod, "url_to_fs", by_credentials)

    first = engine.read_csv("s3://bucket/pkg/link.csv", storage_options={"key": "A"})
    second = engine.read_csv("s3://bucket/pkg/link.csv", storage_options={"key": "B"})

    assert _count(first) == 1
    assert _count(second) == 3
    assert _count(first) == 1


def test_identical_storage_options_reuse_one_filesystem(engine, fsspec_spy) -> None:
    engine.read_csv("s3://bucket/pkg/link.csv", storage_options={"key": "k", "secret": "s"})
    engine.read_parquet("s3://bucket/pkg/link.parquet", storage_options={"key": "k", "secret": "s"})

    assert len(fsspec_spy) == 1


def test_storage_options_on_a_local_path_is_an_error(engine, tmp_path) -> None:
    """Credentials for a local file are a caller bug; say so instead of silently dropping them."""
    p = tmp_path / "t.csv"
    p.write_bytes(_CSV)

    with pytest.raises(InvalidEngineCallError, match="storage_options"):
        engine.read_csv(p, storage_options={"token": "t"})


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def test_write_csv_with_storage_options_writes_through_fsspec(engine, mem) -> None:
    expr = engine.read_csv("memory://bucket/pkg/link.csv", storage_options={"token": "t"})

    engine.write_csv(expr, "memory://bucket/out/link.csv", storage_options={"token": "t"})

    assert mem.cat("/bucket/out/link.csv").startswith(b"a,b\n")


def test_write_parquet_with_storage_options_writes_through_fsspec(engine, mem) -> None:
    expr = engine.read_csv("memory://bucket/pkg/link.csv", storage_options={"token": "t"})

    engine.write_parquet(expr, "memory://bucket/out/link.parquet", storage_options={"token": "t"})

    assert pq.read_table(io.BytesIO(mem.cat("/bucket/out/link.parquet"))).num_rows == 2
