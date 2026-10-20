"""A zip of csvs at a URL is opened through fsspec, never as a local path.

``zipfile.ZipFile("https://h/net.csv.zip")`` opens the relative local path
``<cwd>/https:/h/net.csv.zip``. Every test here runs from a ``tmp_path``
cwd that holds a *decoy* zip at exactly that local path, so a regression
reads the decoy's ``decoy`` table instead of the remote ``link`` table.
The remote side is fsspec's in-memory filesystem, reached through a fake
``fsspec.open`` that records each call.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import fsspec
import pytest
from corral.dataset import Package
from corral.engines import get_engine
from corral.io import WriteUnsupportedForSchemeError
from corral.io import remote as remote_mod
from corral.io import zipcsv_adapter as zipcsv_mod
from corral.io.zipcsv_adapter import ZipCsvAdapter


def _zip_bytes(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(f"{name}.csv", body)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _adapters_without_ambient_credentials(monkeypatch):
    """An empty credential cascade (the real one may consult the OS keyring)."""
    for mod in (zipcsv_mod, remote_mod):
        monkeypatch.setattr(mod, "resolve_credentials", lambda host, explicit=None: dict(explicit or {}))


@pytest.fixture
def remote_zip(tmp_path, monkeypatch):
    """Serve a ``link`` zip at an ``s3://`` URL from memory; plant a decoy at its local look-alike path.

    Returns ``(url, calls)``; ``calls`` collects ``(url, storage_options)`` per ``fsspec.open``.
    """
    url = f"s3://bucket-{uuid.uuid4().hex[:8]}/net.csv.zip"
    mem_url = url.replace("s3://", "memory://", 1)
    with fsspec.open(mem_url, "wb") as f:
        f.write(_zip_bytes(link="link_id,from_node_id\n1,10\n2,20\n"))

    decoy = tmp_path / Path(url.replace("s3://", "s3:/", 1))
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(_zip_bytes(decoy="x\n1\n"))
    monkeypatch.chdir(tmp_path)

    calls: list[tuple[str, dict]] = []
    real_open = fsspec.open

    def fake_open(path, mode="rb", **storage_options):
        calls.append((path, storage_options))
        return real_open(path.replace("s3://", "memory://", 1), mode)

    monkeypatch.setattr(zipcsv_mod.fsspec, "open", fake_open)
    yield url, calls
    fsspec.filesystem("memory").rm(mem_url, recursive=True)


def test_scan_url_lists_remote_members(remote_zip) -> None:
    url, calls = remote_zip
    refs = ZipCsvAdapter().scan(url)
    assert [r.name for r in refs] == ["link"]
    assert refs[0].container == url
    assert [c[0] for c in calls] == [url]


def test_read_url_uses_storage_options_for_fsspec_only(remote_zip) -> None:
    url, calls = remote_zip
    engine = get_engine()
    # storage_options must reach fsspec, not engine.read_csv (which would reject it).
    expr = ZipCsvAdapter().read(url, engine, table="link", storage_options={"token": "t"})
    assert list(engine.to_pandas(expr)["link_id"]) == [1, 2]
    assert calls == [(url, {"token": "t"})]


def test_read_url_without_storage_options_uses_credential_cascade(remote_zip, monkeypatch) -> None:
    url, calls = remote_zip
    hosts: list[str] = []

    def spy(host, explicit=None):
        hosts.append(host)
        return {"token": "from-cascade"}

    monkeypatch.setattr(zipcsv_mod, "resolve_credentials", spy)
    ZipCsvAdapter().read(url, get_engine(), table="link")
    assert hosts == [url.split("/")[2]]
    assert calls[0][1] == {"token": "from-cascade"}


def test_probe_never_opens_a_bare_zip_url(tmp_path, monkeypatch) -> None:
    """A bare ``.zip`` URL is not peeked at (no network, no local look-alike)."""
    decoy = tmp_path / "https:" / "h" / "net.zip"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(_zip_bytes(decoy="x\n1\n"))
    monkeypatch.chdir(tmp_path)
    assert ZipCsvAdapter().probe("https://h/net.zip") is False


@pytest.mark.parametrize("dest", ["s3://b/out.csv.zip", "https://h/out.zip"])
def test_write_to_url_is_refused_and_writes_nothing(dest, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = get_engine()
    with pytest.raises(WriteUnsupportedForSchemeError):
        ZipCsvAdapter().write(engine.from_records({"a": [1]}), dest, engine=engine)
    assert list(tmp_path.iterdir()) == []


def test_package_from_remote_zip_reads_each_member(remote_zip) -> None:
    url, calls = remote_zip
    pkg = Package.from_source(url, engine=get_engine(), credentials={"token": "t"})
    assert set(pkg.tables) == {"link"}
    assert len(pkg.tables["link"].to_pandas()) == 2
    # scan (credential cascade) then read (the caller's explicit credentials).
    assert [c[0] for c in calls] == [url, url]
    assert calls[1][1] == {"token": "t"}


def test_package_write_to_url_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = get_engine()
    pkg = Package.from_tables({}, engine=engine)
    with pytest.raises(WriteUnsupportedForSchemeError):
        pkg.write("s3://b/out.csv.zip", format="zip")
    assert list(tmp_path.iterdir()) == []
