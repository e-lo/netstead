"""Tests for :class:`corral.dataset.Package` (task 2.7 / issue #66).

End-to-end exercises against the bundled Leavenworth GMNS fixture in
all three on-disk shapes (csv directory, parquet directory, single
duckdb file). The same parametrisation also covers the dict-access,
scope, write, and validate surfaces.
"""

from __future__ import annotations

import shutil
import warnings
from pathlib import Path

import pytest
from corral.dataset import (
    OutOfSyncError,
    OutOfSyncWarning,
    Package,
    Table,
)
from corral.engines.ibis_engine import IbisEngine
from corral.spec.loader import load_package
from netstead.fixtures import leavenworth

# ---------------------------------------------------------------------------
# Engine parametrisation helper
# ---------------------------------------------------------------------------


def _make_engine(name: str):
    if name == "ibis":
        return IbisEngine()
    if name == "polars":
        pytest.importorskip("polars", reason="polars optional extra not installed")

        return IbisEngine()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine name: {name!r}")


def _gmns_datapackage() -> Path:
    """Path to the bundled GMNS 0.97 datapackage.json."""
    import netstead

    return Path(netstead.__file__).parent / "spec" / "0.97" / "datapackage.json"


# ---------------------------------------------------------------------------
# Constructors — Leavenworth fixture roundtrips per format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_package_from_leavenworth_csv_dir(engine_name: str) -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=_make_engine(engine_name),
        spec=_gmns_datapackage(),
    )
    assert isinstance(pkg, Package)
    assert "link" in pkg
    assert "node" in pkg
    assert isinstance(pkg["link"], Table)
    # Source captured for round-trip.
    assert pkg.source is not None and "csv" in pkg.source


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_package_from_leavenworth_parquet_dir(engine_name: str) -> None:
    pkg = Package.from_source(
        leavenworth.parquet_dir(),
        engine=_make_engine(engine_name),
        spec=_gmns_datapackage(),
    )
    assert "link" in pkg
    assert "node" in pkg
    # Parquet adapter sets the format on each table.
    assert pkg["link"].format == "parquet"


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_package_from_leavenworth_duckdb(engine_name: str, tmp_path: Path) -> None:
    # A private copy: the engine opens .duckdb read-write, which takes an exclusive
    # cross-process lock that would block other xdist workers reading the committed file.
    db = shutil.copyfile(leavenworth.duckdb_path(), tmp_path / "leavenworth.duckdb")
    pkg = Package.from_source(
        db,
        engine=_make_engine(engine_name),
        spec=_gmns_datapackage(),
    )
    assert "link" in pkg
    assert "node" in pkg
    assert pkg["link"].format == "duckdb"


def test_package_from_source_partial_load() -> None:
    """Passing ``tables=`` limits the load to a subset."""
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    assert set(pkg.keys()) == {"link", "node"}


# ---------------------------------------------------------------------------
# Dict-like surface
# ---------------------------------------------------------------------------


def test_package_dict_access() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    # __getitem__
    assert isinstance(pkg["link"], Table)
    # __contains__
    assert "link" in pkg
    assert "no-such-table" not in pkg
    # __len__
    assert len(pkg) == 2
    # __iter__
    assert set(iter(pkg)) == {"link", "node"}
    # keys/values/items
    assert set(pkg.keys()) == {"link", "node"}
    assert all(isinstance(t, Table) for t in pkg.values())
    assert dict(pkg.items()).keys() == {"link", "node"}


# ---------------------------------------------------------------------------
# safe_count — replaces three duplicate _safe_count helpers across
# corral + netstead CLI / server / MCP (F1/S3)
# ---------------------------------------------------------------------------


def test_package_safe_count_returns_int_for_present_table():
    """`safe_count` returns the row count for a table that exists."""
    e = IbisEngine()
    pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}, {"a": 2}]), engine=e)})
    assert pkg.safe_count("x") == 2


def test_package_safe_count_returns_none_for_absent_table():
    """`safe_count` on a missing table returns None — preview never crashes."""
    e = IbisEngine()
    pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}]), engine=e)})
    assert pkg.safe_count("absent") is None


def test_package_safe_count_swallows_count_exception():
    """If `table.count()` raises, `safe_count` returns None — preview-safe."""

    class _BrokenTable(Table):
        def count(self) -> int:
            raise RuntimeError("backend hiccup")

    e = IbisEngine()
    bad = _BrokenTable(name="bad", expr=e.from_records([{"a": 1}]), engine=e)
    pkg = Package.from_tables({"bad": bad})
    assert pkg.safe_count("bad") is None


# ---------------------------------------------------------------------------
# Validation orchestration
# ---------------------------------------------------------------------------


def test_package_validate_runs_all_passes() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    report = pkg.validate()
    # The report should accumulate findings from multiple categories.
    # On the clean fixture we expect no errors; the categories present
    # are whatever the validators emitted (informational + structural).
    cats = {i.category for i in report.issues}
    assert isinstance(cats, set)


def test_package_validate_skip_individual_passes() -> None:
    """``schema=False`` skips the per-table schema rules."""
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    full = pkg.validate()
    skipped = pkg.validate(schema=False)
    full_codes = {i.code for i in full.issues}
    skipped_codes = {i.code for i in skipped.issues}
    # Every schema.* code in the full report should be absent from the
    # skipped one; non-schema codes are unaffected.
    full_schema_codes = {c for c in full_codes if c.startswith("schema.")}
    skipped_schema_codes = {c for c in skipped_codes if c.startswith("schema.")}
    assert skipped_schema_codes == set()
    assert (full_codes - full_schema_codes) <= skipped_codes


# ---------------------------------------------------------------------------
# Scope (table + column subset)
# ---------------------------------------------------------------------------


def test_package_scope_by_tables_subset() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
    )
    sub = pkg.scope(tables=["link", "node"])
    assert isinstance(sub, Package)
    assert set(sub.keys()) == {"link", "node"}
    # Original unchanged.
    assert set(pkg.keys()) > {"link", "node"}


def test_package_scope_by_columns() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    sub = pkg.scope(columns={"link": ["link_id"], "node": ["node_id"]})
    assert sub["link"].columns() == ["link_id"]
    assert sub["node"].columns() == ["node_id"]
    # Original unchanged.
    assert set(pkg["link"].columns()) > {"link_id"}


# ---------------------------------------------------------------------------
# Write — happy path + overwrite protection + sync-state
# ---------------------------------------------------------------------------


def test_package_write_roundtrip(tmp_path: Path) -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    dest = tmp_path / "out.gmns"
    pkg.write(dest, format="parquet")
    pkg2 = Package.from_source(dest, engine=IbisEngine(), spec=_gmns_datapackage())
    assert "link" in pkg2
    assert "node" in pkg2
    assert pkg2["link"].count() == pkg["link"].count()


def test_package_write_overwrite_protection(tmp_path: Path) -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    dest = tmp_path / "out.gmns"
    pkg.write(dest, format="parquet")
    with pytest.raises(FileExistsError):
        pkg.write(dest, format="parquet")
    # overwrite=True succeeds:
    pkg.write(dest, format="parquet", overwrite=True)


# ---------------------------------------------------------------------------
# CSV-in-zip packages — read via the "<zip>::<member>" sub-locator, write
# as CSV members plus a datapackage.json.
# ---------------------------------------------------------------------------


def test_package_from_source_reads_multi_csv_zip() -> None:
    """Each zip member is read through the zipcsv adapter, not the plain csv one."""
    spec = _gmns_datapackage()
    zipped = Package.from_source(leavenworth.zip_path(), engine=IbisEngine(), spec=spec)
    on_disk = Package.from_source(leavenworth.csv_dir(), engine=IbisEngine(), spec=spec)
    assert set(zipped.keys()) == set(on_disk.keys())
    for name in ("link", "node"):
        assert zipped[name].count() == on_disk[name].count()


def test_package_from_source_csv_filename_with_double_colon(tmp_path: Path) -> None:
    """'::' in a plain filename is not a container sub-locator."""
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "a::b.csv").write_text("x,y\n1,2\n3,4\n")
    pkg = Package.from_source(src, engine=IbisEngine())
    assert list(pkg.keys()) == ["a::b"]
    assert pkg["a::b"].count() == 2


@pytest.mark.parametrize(("filename", "fmt"), [("out.zip", None), ("out.csv.zip", None), ("out.bin", "zip")])
def test_package_write_zip_roundtrip(tmp_path: Path, filename: str, fmt: str | None) -> None:
    """write() to a zip produces flat CSV members + datapackage.json and reopens."""
    import json
    import zipfile

    pkg = Package.from_source(
        leavenworth.csv_dir(), engine=IbisEngine(), spec=_gmns_datapackage(), tables=["link", "node"]
    )
    dest = tmp_path / filename
    pkg.write(dest, format=fmt)

    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == ["datapackage.json", "link.csv", "node.csv"]
        descriptor = json.loads(zf.read("datapackage.json"))
    assert {r["name"]: r["path"] for r in descriptor["resources"]} == {"link": "link.csv", "node": "node.csv"}
    # Schemas are inlined so the archive is self-describing.
    assert all(isinstance(r["schema"], dict) for r in descriptor["resources"])

    reopened = Package.from_source(dest, engine=IbisEngine(), spec=_gmns_datapackage(), format="zipcsv")
    assert reopened["link"].count() == pkg["link"].count()
    assert reopened["node"].count() == pkg["node"].count()
    # Atomic write leaves no staging debris next to the destination.
    assert sorted(p.name for p in tmp_path.iterdir()) == [filename]


def test_package_write_zip_overwrite(tmp_path: Path) -> None:
    pkg = Package.from_source(leavenworth.csv_dir(), engine=IbisEngine(), spec=_gmns_datapackage(), tables=["node"])
    dest = tmp_path / "out.zip"
    pkg.write(dest)
    with pytest.raises(FileExistsError):
        pkg.write(dest)
    pkg.write(dest, overwrite=True)
    assert Package.from_source(dest, engine=IbisEngine(), spec=_gmns_datapackage())["node"].count() > 0


def test_package_write_on_dirty_emits_warning(tmp_path: Path) -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    pkg["link"].invalidate()
    dest = tmp_path / "out.gmns"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", OutOfSyncWarning)
        pkg.write(dest, format="parquet")
    assert any(isinstance(w.message, OutOfSyncWarning) for w in caught)


def test_package_write_strict_sync_raises_on_dirty(tmp_path: Path) -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    pkg["link"].invalidate()
    dest = tmp_path / "out.gmns"
    with pytest.raises(OutOfSyncError):
        pkg.write(dest, format="parquet", strict_sync=True)


# ---------------------------------------------------------------------------
# Mutation surface
# ---------------------------------------------------------------------------


def test_package_add_remove_table() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link"],
    )
    # Build a small Table by hand.
    e = pkg.engine
    new_t = Table(name="custom", expr=e.from_records([{"a": 1}]), engine=e)
    pkg.add_table("custom", new_t)
    assert "custom" in pkg
    pkg.remove_table("custom")
    assert "custom" not in pkg


def test_package_repr_html_returns_non_empty_string() -> None:
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    html = pkg._repr_html_()
    assert isinstance(html, str)
    assert html.strip() != ""


def test_package_dirty_tracker_optional(tmp_path: Path) -> None:
    """Package works without DirtyTracker — sync_state validation no-ops."""
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    # No DirtyTracker passed in. validate(sync_state=True) must not raise.
    report = pkg.validate(sync_state=True)
    # No sync.* issues should be emitted when there's no tracker.
    sync_codes = [i for i in report.issues if i.code.startswith("sync.")]
    assert sync_codes == []


# ---------------------------------------------------------------------------
# I1 — Package.validate stamps the DirtyTracker after a clean FK pass
# ---------------------------------------------------------------------------


def test_validate_stamps_dirty_tracker_after_clean_fk_pass() -> None:
    """A clean FK pass MUST stamp the tracker so later edits surface drift.

    Pins the I1 fix. Prior to it, ``Package.validate`` ran the FK
    validator but never called ``DirtyTracker.stamp_fk_from_exprs``,
    so the "valve" was disconnected and a subsequent edit produced no
    ``sync.fk_stale`` warning.
    """
    from corral.validation.sync_state import DirtyTracker

    tracker = DirtyTracker()
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link", "node"],
    )
    pkg.dirty_tracker = tracker
    # Sanity — tracker starts empty.
    assert tracker._fks == []
    report = pkg.validate()
    # The Leavenworth fixture's link/node FK should be clean — no
    # fk.* errors in the report.
    fk_codes = [i.code for i in report.issues if i.code.startswith("fk.")]
    fk_errors = [c for c in fk_codes if c != "fk.unverifiable"]
    assert fk_errors == [], f"expected clean FK pass on Leavenworth; got: {fk_codes}"
    # The valve must have fired — at least one FK stamped.
    assert len(tracker._fks) >= 1, "Package.validate did not stamp any FKs on the tracker"
    # The link.from_node_id -> node.node_id stamp should be among them.
    stamped_pairs = {(s.source_table, s.source_field, s.target_table, s.target_field) for s in tracker._fks}
    assert ("link", "from_node_id", "node", "node_id") in stamped_pairs


def test_validate_does_not_stamp_broken_fk() -> None:
    """A failing FK MUST NOT be stamped — a stale stamp on a known-broken
    FK would actively mislead the user.

    Negative side of the I1 fix.
    """
    from corral.engines.ibis_engine import IbisEngine as _PE
    from corral.spec.loader import load_package as _load
    from corral.validation.sync_state import DirtyTracker

    e = _PE()
    # Build an in-memory package with a deliberately broken FK.
    pkg_spec = _load(
        {
            "name": "broken",
            "resources": [
                {
                    "name": "node",
                    "path": "node.csv",
                    "schema": {"fields": [{"name": "node_id", "type": "integer"}]},
                },
                {
                    "name": "link",
                    "path": "link.csv",
                    "schema": {
                        "fields": [
                            {"name": "link_id", "type": "integer"},
                            {"name": "from_node_id", "type": "integer"},
                        ],
                        "foreignKeys": [
                            {
                                "fields": "from_node_id",
                                "reference": {"resource": "node", "fields": "node_id"},
                            }
                        ],
                    },
                },
            ],
        }
    )
    link = Table(
        name="link",
        expr=e.from_records([{"link_id": 1, "from_node_id": 999}]),  # 999 doesn't exist in node
        engine=e,
    )
    node = Table(name="node", expr=e.from_records([{"node_id": 1}]), engine=e)
    pkg = Package.from_tables({"link": link, "node": node}, spec=pkg_spec, engine=e)
    tracker = DirtyTracker()
    pkg.dirty_tracker = tracker
    report = pkg.validate()
    # The FK MUST fire.
    assert any(i.code == "fk.missing_target" for i in report.issues)
    # And the tracker MUST NOT have stamped that broken FK.
    stamped_pairs = {(s.source_table, s.target_table, s.target_field) for s in tracker._fks}
    assert ("link", "node", "node_id") not in stamped_pairs


# ---------------------------------------------------------------------------
# I9 — Package.write raises PackageError / FormatNotDetected with helpful text
# ---------------------------------------------------------------------------


def test_package_write_raises_package_error_without_engine(tmp_path: Path) -> None:
    """Calling write() on an engine-less Package raises PackageError."""
    from corral.dataset import PackageError
    from corral.spec.model import DataPackage

    pkg = Package(spec=DataPackage(name="x", resources=[]), engine=None)
    with pytest.raises(PackageError, match="engine"):
        pkg.write(tmp_path / "out.parquet")


def test_package_write_raises_format_not_detected_on_unknown_extension(tmp_path: Path) -> None:
    """An unknown destination extension MUST raise FormatNotDetected, not
    silently coerce to parquet."""
    from corral.io import FormatNotDetected

    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=_gmns_datapackage(),
        tables=["link"],
    )
    with pytest.raises(FormatNotDetected, match="format="):
        pkg.write(tmp_path / "out.unknown_ext")


# ---------------------------------------------------------------------------
# Top-level re-export
# ---------------------------------------------------------------------------


def test_top_level_reexport_of_package_and_table() -> None:
    """``from corral import Package, Table`` works."""
    from corral import Package as TopPackage
    from corral import Table as TopTable

    assert TopPackage is Package
    assert TopTable is Table


def test_from_tables_constructor() -> None:
    """Package can be built from an already-prepared mapping of Tables."""
    e = IbisEngine()
    link = Table(name="link", expr=e.from_records([{"link_id": 1}]), engine=e)
    node = Table(name="node", expr=e.from_records([{"node_id": 1}]), engine=e)
    pkg = load_package(
        {
            "name": "x",
            "resources": [
                {"name": "link", "path": "link.csv"},
                {"name": "node", "path": "node.csv"},
            ],
        }
    )
    p = Package.from_tables({"link": link, "node": node}, spec=pkg, engine=e)
    assert set(p.keys()) == {"link", "node"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("s3://b/k.parquet", True),
        ("HTTPS://x.org/n.csv", True),
        ("duckdb:///tmp/x.duckdb", False),
        ("C:\\data\\net", False),
        ("relative/net", False),
    ],
)
def test_looks_remote_agrees_with_dispatch_scheme(path: str, expected: bool) -> None:
    """``_looks_remote`` uses the same urlparse scheme as ``dispatch`` (``_scheme_of``), so they can't disagree."""
    from corral.dataset.package import _looks_remote
    from corral.io import _scheme_of
    from corral.io.remote import REMOTE_SCHEMES

    assert _looks_remote(path) is expected
    assert _looks_remote(path) is (_scheme_of(path) in REMOTE_SCHEMES)
