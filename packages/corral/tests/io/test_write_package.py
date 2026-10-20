"""Tests for whole-package writes: ``corral.io.write_package`` and each adapter's ``write_package`` (#142).

The layout a package takes on disk (a directory of files, one multi-table
file, a zip) is the adapter's decision, not ``Package.write``'s.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import pytest
from corral.dataset import Package, Table
from corral.engines.ibis_engine import IbisEngine
from corral.io import get_adapter, register_adapter, write_package

from ._fakes import _BaseFake


@pytest.fixture
def engine() -> IbisEngine:
    return IbisEngine()


@pytest.fixture
def tables(engine: IbisEngine) -> dict[str, Any]:
    return {
        "link": engine.from_records({"link_id": [1, 2], "name": ["a", "b"]}),
        "node": engine.from_records({"node_id": [10, 20, 30]}),
    }


def _describe(fmt: str) -> dict[str, Any]:
    return {"name": "net", "resources": [{"name": "link", "path": f"link.{fmt}", "format": fmt}]}


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_flat_formats_write_a_directory_with_a_descriptor(fmt, tables, engine, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    write_package(get_adapter(fmt), tables, dest, engine, describe=_describe)

    assert sorted(p.name for p in dest.iterdir()) == ["datapackage.json", f"link.{fmt}", f"node.{fmt}"]
    assert json.loads((dest / "datapackage.json").read_text()) == _describe(fmt)


def test_duckdb_writes_one_file_with_a_table_each(tables, engine, tmp_path: Path) -> None:
    dest = tmp_path / "out.duckdb"
    write_package(get_adapter("duckdb"), tables, dest, engine, describe=_describe)

    assert dest.is_file()
    with duckdb.connect(str(dest), read_only=True) as con:
        names = {row[0] for row in con.table_function("duckdb_tables").project("table_name").fetchall()}
    assert names == {"link", "node"}


def test_zipcsv_writes_csv_members_and_a_csv_descriptor(tables, engine, tmp_path: Path) -> None:
    dest = tmp_path / "out.zip"
    write_package(get_adapter("zipcsv"), tables, dest, engine, describe=_describe)

    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == ["datapackage.json", "link.csv", "node.csv"]
        assert json.loads(zf.read("datapackage.json")) == _describe("csv")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.zip"], "no staging debris beside the zip"


def test_an_adapter_without_write_package_gets_one_location_per_table(tables, engine, tmp_path: Path) -> None:
    written: list[str] = []

    class _PlainAdapter(_BaseFake):
        name = "plain"

        def write(self, expr, dest, engine, **kwargs):
            written.append(Path(dest).name)

    write_package(_PlainAdapter(), tables, tmp_path / "out", engine, describe=_describe)
    assert written == ["link", "node"]


def test_package_write_hands_the_layout_to_the_adapter(engine, tmp_path: Path) -> None:
    """``Package.write`` stages, calls the adapter's ``write_package``, then swaps the result into place."""
    calls: list[tuple[list[str], Path, dict]] = []

    class _BundleAdapter(_BaseFake):
        name = "bundle"
        extensions = ("bundle",)

        def write_package(self, tables, dest, engine, *, describe):
            calls.append((list(tables), Path(dest), describe("csv")))
            Path(dest).write_text("bundled")

    register_adapter(_BundleAdapter())
    a = Table(name="a", expr=engine.from_records({"x": [1]}), engine=engine)
    pkg = Package.from_tables({"a": a}, engine=engine)
    dest = tmp_path / "out.bundle"
    pkg.write(dest, format="bundle")

    [(names, staged, descriptor)] = calls
    assert names == ["a"]
    assert staged != dest and staged.parent.parent == tmp_path, "the adapter writes into a staging area beside dest"
    assert [r["name"] for r in descriptor["resources"]] == ["a"]
    assert dest.read_text() == "bundled"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.bundle"]
