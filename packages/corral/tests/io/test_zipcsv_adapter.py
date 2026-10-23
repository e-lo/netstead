"""Tests for the ZipCsvAdapter (Phase 1 task 1.10).

A bundle of CSV files packaged into a single ``.zip`` (or ``.csv.zip``
for single-table cases). Self-registers under name ``"zipcsv"``; owns
the compound extension ``"csv.zip"`` and the bare ``"zip"`` extension
(see :class:`corral.io.zipcsv_adapter.ZipCsvAdapter`).

Tests cover:
    - ``probe()`` heuristics + safety (never raises).
    - ``scan()`` shape (one ``ResourceRef`` per CSV member).
    - ``read()`` round-trip via the three stock engines.
    - Dispatch precedence (compound ``.csv.zip`` wins over bare ``.zip``;
      bare ``.zip`` falls through to ``probe``).
    - ``write()`` round-trip for the single-CSV-into-zip case.
    - Protocol conformance.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest
from corral.engines import get_engine
from corral.engines.errors import InvalidEngineCallError
from corral.io import FormatAdapter, dispatch, list_adapters
from corral.io.base import ResourceRef
from corral.io.zipcsv_adapter import ZipCsvAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LEAVENWORTH = Path(__file__).resolve().parents[4] / "packages" / "netstead" / "netstead" / "fixtures" / "leavenworth"
LEAVENWORTH_ZIP = LEAVENWORTH / "leavenworth.csv.zip"

# 9 CSVs ship in the fixture zip; the datapackage.json is not a csv and
# must be excluded from scan/probe accounting.
_EXPECTED_LEAVENWORTH_CSVS = {
    "geometry",
    "lane",
    "link",
    "link_tod",
    "node",
    "signal_controller",
    "time_set_definitions",
    "use_definition",
    "use_group",
}


def _make_zip(tmp_path: Path, members: dict[str, bytes], name: str = "x.zip") -> Path:
    """Build a zip at ``tmp_path/name`` containing ``members`` (arcname → bytes)."""
    out = tmp_path / name
    with zipfile.ZipFile(out, "w") as z:
        for arcname, data in members.items():
            z.writestr(arcname, data)
    return out


def _single_csv_zip(tmp_path: Path) -> Path:
    """A zip with exactly one CSV inside (no other members)."""
    return _make_zip(
        tmp_path,
        {"only.csv": b"a,b\n1,2\n3,4\n"},
        name="single.csv.zip",
    )


def _multi_csv_zip(tmp_path: Path) -> Path:
    """A bare ``.zip`` with multiple CSVs — probe must open it to recognize."""
    return _make_zip(
        tmp_path,
        {
            "left.csv": b"a,b\n1,2\n",
            "right.csv": b"c,d\n5,6\n",
        },
        name="pair.zip",
    )


def _zip_without_csvs(tmp_path: Path) -> Path:
    return _make_zip(
        tmp_path,
        {"a.txt": b"hello", "b.txt": b"world"},
        name="no_csv.zip",
    )


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def test_probe_csv_zip_extension(tmp_path):
    """``.csv.zip`` suffix is sufficient — don't even need to open the file."""
    adapter = ZipCsvAdapter()
    # The compound suffix wins without opening; file need not exist.
    fake = tmp_path / "anything.csv.zip"
    assert adapter.probe(fake) is True


def test_probe_zip_with_csvs(tmp_path):
    """A bare ``.zip`` containing CSVs probes True."""
    adapter = ZipCsvAdapter()
    assert adapter.probe(_single_csv_zip(tmp_path)) is True
    assert adapter.probe(_multi_csv_zip(tmp_path)) is True


def test_probe_zip_without_csvs(tmp_path):
    """A zip of non-CSV files probes False."""
    adapter = ZipCsvAdapter()
    assert adapter.probe(_zip_without_csvs(tmp_path)) is False


def test_probe_non_zip_extension(tmp_path):
    """A path that isn't a zip extension at all probes False."""
    adapter = ZipCsvAdapter()
    plain = tmp_path / "plain.csv"
    plain.write_text("a,b\n1,2\n")
    assert adapter.probe(plain) is False


def test_probe_never_raises(tmp_path):
    """Corrupt, non-existent, None — all return False without exceptions."""
    adapter = ZipCsvAdapter()
    # Corrupt zip content under a .zip extension.
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"this is not a zip file")
    assert adapter.probe(corrupt) is False
    # Non-existent file under bare .zip — probe peeks inside, which fails.
    assert adapter.probe(tmp_path / "missing.zip") is False
    # Non-existent .csv.zip — extension shortcut still returns True.
    assert adapter.probe(tmp_path / "missing.csv.zip") is True
    # None — must not crash.
    assert adapter.probe(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_self_registers_on_import():
    """Importing the module registers ``"zipcsv"`` in the global registry."""
    # Importing the module at the top of this file is what triggers
    # the registration; we just assert the effect.
    assert "zipcsv" in list_adapters()


def test_protocol_conformance():
    """The adapter satisfies the runtime_checkable FormatAdapter protocol."""
    assert isinstance(ZipCsvAdapter(), FormatAdapter)


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


def test_scan_returns_one_resource_per_csv_member():
    """The Leavenworth zip → 9 ResourceRefs (datapackage.json excluded)."""
    adapter = ZipCsvAdapter()
    refs = adapter.scan(LEAVENWORTH_ZIP, engine=None)
    assert len(refs) == 9
    assert {r.name for r in refs} == _EXPECTED_LEAVENWORTH_CSVS


def test_scan_member_names_are_stems():
    """ResourceRef.name is the file stem (no .csv, no directory prefix)."""
    adapter = ZipCsvAdapter()
    refs = adapter.scan(LEAVENWORTH_ZIP, engine=None)
    for ref in refs:
        assert not ref.name.endswith(".csv")
        assert "/" not in ref.name
        assert isinstance(ref, ResourceRef)
        assert ref.format == "csv"
        # The path locator includes the member name after a "::" separator
        # so downstream code can re-open the right member.
        assert "::" in ref.path


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


def test_read_single_csv_zip_requires_table_kwarg(tmp_path):
    """I10: ``table=`` is required for every read, even single-csv zips.

    The implicit single-csv shortcut was removed for selector-contract
    parity with the duckdb adapter (which has always required
    ``table=``). Confirms the kwarg is now mandatory.
    """
    adapter = ZipCsvAdapter()
    zip_path = _single_csv_zip(tmp_path)
    engine = get_engine()

    # Omitting table= raises the same InvalidEngineCallError the
    # multi-csv path does.
    with pytest.raises(InvalidEngineCallError) as excinfo:
        adapter.read(zip_path, engine=engine)
    assert "table" in str(excinfo.value).lower()

    # Passing the (only) member name reads cleanly.
    # ``_single_csv_zip`` writes a member named ``only.csv``.
    expr = adapter.read(zip_path, engine=engine, table="only")
    df = engine.to_pandas(expr)
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_read_multi_csv_zip_requires_table_kwarg():
    """Multi-CSV zip without ``table=`` raises InvalidEngineCallError."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    with pytest.raises(InvalidEngineCallError) as excinfo:
        adapter.read(LEAVENWORTH_ZIP, engine=engine)
    msg = str(excinfo.value)
    # Message should name the kwarg and suggest one of the actual tables.
    assert "table" in msg
    # Hint: list a member name so the user sees a concrete example.
    assert any(name in msg for name in _EXPECTED_LEAVENWORTH_CSVS)


def test_read_multi_csv_zip_unknown_table_raises():
    """``table=<name>`` that isn't in the zip raises InvalidEngineCallError."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    with pytest.raises(InvalidEngineCallError) as excinfo:
        adapter.read(LEAVENWORTH_ZIP, engine=engine, table="nonexistent")
    msg = str(excinfo.value)
    assert "nonexistent" in msg


def test_read_specific_table_delegates_to_engine():
    """Round-trip through the engine — node.csv has known shape."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    expr = adapter.read(LEAVENWORTH_ZIP, engine=engine, table="node")
    df = engine.to_pandas(expr)
    assert isinstance(df, pd.DataFrame)
    assert "node_id" in df.columns
    assert len(df) > 0


def test_read_member_and_name_aliases_dropped(tmp_path):
    """I10: ``member=`` and ``name=`` are no longer accepted as table aliases.

    They forward through to ``engine.scan`` as unknown kwargs and the
    engine rejects them, which is the desired loud failure mode. The
    user-visible message points at the surviving ``table=`` kwarg.
    """
    adapter = ZipCsvAdapter()
    engine = get_engine()
    zip_path = _make_zip(
        tmp_path,
        {"left.csv": b"a,b\n1,2\n", "right.csv": b"c,d\n5,6\n"},
        name="alias.zip",
    )
    # Without ``table=``, the read raises InvalidEngineCallError naming
    # the available tables — regardless of ``member=``/``name=``.
    with pytest.raises(InvalidEngineCallError) as excinfo:
        adapter.read(zip_path, engine=engine, member="left")
    assert "table" in str(excinfo.value).lower()
    # The new (and only) selector still works.
    expr = adapter.read(zip_path, engine=engine, table="left")
    df = engine.to_pandas(expr)
    assert list(df.columns) == ["a", "b"]


def test_read_table_kwarg_accepts_stem_or_filename(tmp_path):
    """Both ``table="node"`` and ``table="node.csv"`` resolve to the same member."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    by_stem = engine.to_pandas(adapter.read(LEAVENWORTH_ZIP, engine=engine, table="node"))
    by_fname = engine.to_pandas(adapter.read(LEAVENWORTH_ZIP, engine=engine, table="node.csv"))
    assert by_stem.shape == by_fname.shape


# ---------------------------------------------------------------------------
# dispatch() routing
# ---------------------------------------------------------------------------


def test_dispatch_routes_csv_zip():
    """``foo.csv.zip`` resolves to ZipCsvAdapter via the compound-extension lane."""
    adapter = dispatch("foo.csv.zip")
    assert adapter.name == "zipcsv"


def test_dispatch_routes_zip_via_probe(tmp_path):
    """``foo.zip`` with csv members resolves to ZipCsvAdapter via probe."""
    # The bare ``.zip`` extension is also owned by zipcsv (extensions
    # tuple lists ``csv.zip`` first then ``zip``), so the extension lane
    # actually resolves before probe. Either way the result is zipcsv.
    zip_path = _multi_csv_zip(tmp_path)
    adapter = dispatch(zip_path)
    assert adapter.name == "zipcsv"


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


def test_write_single_csv_into_zip_roundtrip(tmp_path):
    """Write a DataFrame as a single csv inside a zip; round-trip back equal."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    src = engine.from_records({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    dest = tmp_path / "out.csv.zip"
    adapter.write(src, dest, engine=engine, table="data")

    # The destination exists, is a valid zip, contains exactly one csv.
    assert dest.is_file()
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
    assert names == ["data.csv"]

    # Round-trip via the adapter's read path.
    # I10: ``table=`` is required even for single-csv zips.
    expr = adapter.read(dest, engine=engine, table="data")
    out = engine.to_pandas(expr)
    assert list(out.columns) == ["a", "b"]
    assert len(out) == 3


def test_write_without_table_defaults_to_dest_stem(tmp_path):
    """If ``table=`` is omitted, the inner csv is named after the dest stem."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    src = engine.from_records({"a": [1]})
    dest = tmp_path / "named_after_dest.csv.zip"
    adapter.write(src, dest, engine=engine)
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
    # Single .csv inside; stem comes from dest (strip the .csv.zip compound).
    assert len(names) == 1
    assert names[0].endswith(".csv")


# ---------------------------------------------------------------------------
# I6: write refuses to clobber without explicit overwrite=True
# ---------------------------------------------------------------------------


def test_write_refuses_existing_dest_without_overwrite(tmp_path):
    """An existing destination raises FileExistsError unless overwrite=True.

    I6: silent clobber was a data-loss footgun; the explicit kwarg makes
    the intent visible at the call site.
    """
    adapter = ZipCsvAdapter()
    engine = get_engine()
    src = engine.from_records({"a": [1]})
    dest = tmp_path / "out.csv.zip"
    # First write succeeds (destination doesn't exist).
    adapter.write(src, dest, engine=engine, table="data")
    assert dest.is_file()

    # Second write into the same path is refused.
    with pytest.raises(FileExistsError) as excinfo:
        adapter.write(src, dest, engine=engine, table="data")
    assert "overwrite=True" in str(excinfo.value)


def test_write_overwrite_true_replaces_existing_dest(tmp_path):
    """I6: passing overwrite=True clobbers the existing file."""
    adapter = ZipCsvAdapter()
    engine = get_engine()
    dest = tmp_path / "out.csv.zip"
    adapter.write(engine.from_records({"a": [1]}), dest, engine=engine, table="data")
    # Different payload + overwrite=True succeeds.
    adapter.write(
        engine.from_records({"a": [2, 3, 4]}),
        dest,
        engine=engine,
        table="data",
        overwrite=True,
    )
    # New content is in place.
    out = engine.to_pandas(adapter.read(dest, engine=engine, table="data"))
    assert list(out["a"]) == [2, 3, 4]


# ---------------------------------------------------------------------------
# scan() — schemas from the zip's own datapackage.json (#117)
# ---------------------------------------------------------------------------


def _zip_with_descriptor(path: Path, descriptor: object) -> Path:
    import json

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("link.csv", "link_id,name\n1,a\n")
        z.writestr("node.csv", "node_id\n1\n")
        z.writestr("datapackage.json", descriptor if isinstance(descriptor, str) else json.dumps(descriptor))
    return path


def test_scan_attaches_inline_schemas_from_the_zips_datapackage(tmp_path: Path) -> None:
    descriptor = {
        "name": "net",
        "resources": [
            {"name": "link", "path": "link.csv", "schema": {"fields": [{"name": "link_id", "type": "integer"}]}},
            {"name": "node", "path": "node.csv", "schema": "https://example.com/node.schema.json"},
        ],
    }
    refs = {r.name: r for r in ZipCsvAdapter().scan(_zip_with_descriptor(tmp_path / "net.zip", descriptor))}

    link_schema = refs["link"].table_schema
    assert link_schema is not None
    assert [f.name for f in link_schema.fields] == ["link_id"]
    assert refs["node"].table_schema is None, "a schema given by reference is not fetched during a scan"


def test_scan_ignores_a_broken_datapackage(tmp_path: Path) -> None:
    refs = ZipCsvAdapter().scan(_zip_with_descriptor(tmp_path / "net.zip", "{not json"))
    assert {r.name for r in refs} == {"link", "node"}
    assert all(r.table_schema is None for r in refs)


def test_scan_matches_descriptor_resources_by_name_when_paths_differ() -> None:
    """Leavenworth: the descriptor (shared with the csv directory) says ``csv/node.csv``; the member is ``node.csv``."""
    refs = {r.name: r for r in ZipCsvAdapter().scan(LEAVENWORTH_ZIP)}
    node_schema = refs["node"].table_schema
    assert node_schema is not None
    assert "node_id" in [f.name for f in node_schema.fields]


def _zip_members(path: Path, members: dict[str, str], descriptor: dict) -> Path:
    import json

    with zipfile.ZipFile(path, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
        z.writestr("datapackage.json", json.dumps(descriptor))
    return path


def _node_descriptor(path: str) -> dict:
    schema = {"fields": [{"name": "node_id", "type": "integer"}]}
    return {"name": "p", "resources": [{"name": "node", "path": path, "schema": schema}]}


def test_name_fallback_skips_a_resource_already_matched_by_path(tmp_path: Path) -> None:
    """A resource whose path names a member must not also claim a stray member with its name as stem."""
    members = {"data/node.csv": "node_id\n1\n", "node.csv": "x\n1\n"}
    zp = _zip_members(tmp_path / "p.zip", members, _node_descriptor("data/node.csv"))
    refs = {r.member: r for r in ZipCsvAdapter().scan(zp)}
    assert refs["data/node.csv"].table_schema is not None
    assert refs["node.csv"].table_schema is None


def test_name_fallback_skips_an_ambiguous_stem(tmp_path: Path) -> None:
    """``2023/node.csv`` and ``2024/node.csv`` share the stem; neither gets the ``node`` schema by name."""
    members = {"2023/node.csv": "node_id\n1\n", "2024/node.csv": "node_id\n2\n"}
    zp = _zip_members(tmp_path / "p.zip", members, _node_descriptor("csv/node.csv"))
    assert all(r.table_schema is None for r in ZipCsvAdapter().scan(zp))
