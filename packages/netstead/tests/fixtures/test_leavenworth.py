"""Tests for the bundled Leavenworth, WA GMNS fixture.

These exercise:
  * the four storage variants (csv / parquet / duckdb / zipcsv) hold
    identical data,
  * cross-table foreign keys are intact,
  * shared_categories enum values respect the v0.97 spec,
  * total bundled footprint stays under the 5 MB ceiling so wheels
    don't bloat.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from corral.spec import load_package, load_schema
from netstead.fixtures import leavenworth
from netstead.spec import get_spec_path

# ---------------------------------------------------------------------------
# Spec - mirrored from packages/netstead/netstead/spec/0.97/shared_categories.json
# ---------------------------------------------------------------------------

CTRL_TYPE_VALUES = {
    "no_control",
    "yield",
    "stop",
    "stop_2_way",
    "stop_4_way",
    "signal_with_RTOR",
    "signal",
}
BIKE_FACILITY_VALUES = {
    "unseparated bike lane",
    "buffered bike lane",
    "separated bike lane",
    "counter-flow bike lane",
    "paved shoulder",
    "shared lane",
    "shared use path",
    "off-road unpaved trail",
    "other",
    "none",
}
PED_FACILITY_VALUES = {"unknown", "none", "shoulder", "sidewalk", "offstreet_path", "crosswalk"}
PARKING_VALUES = {"unknown", "none", "parallel", "angle", "other"}

EXPECTED_TABLES = [
    "node",
    "link",
    "geometry",
    "lane",
    "use_definition",
    "use_group",
    "time_set_definitions",
    "link_tod",
]
# signal_controller is conditionally included if any signalized intersection
# was tagged in the OSM extract; it should be present in the bundled snapshot.
OPTIONAL_TABLES = ["signal_controller"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(leavenworth.csv_dir() / f"{name}.csv")


def _read_parquet(name: str) -> pd.DataFrame:
    return pd.read_parquet(leavenworth.parquet_dir() / f"{name}.parquet")


def _read_duckdb(name: str) -> pd.DataFrame:
    con = duckdb.connect(str(leavenworth.duckdb_path()), read_only=True)
    try:
        return con.execute(f"SELECT * FROM {name}").df()
    finally:
        con.close()


def _all_tables() -> list[str]:
    """Tables actually present on disk (filters out optional tables not built)."""
    out = list(EXPECTED_TABLES)
    for name in OPTIONAL_TABLES:
        if (leavenworth.csv_dir() / f"{name}.csv").exists():
            out.append(name)
    return out


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by primary key, reset index, stringify with normalized missings.

    Cross-format equality has to absorb a representational gap: pandas
    reads an empty CSV cell as NaN, parquet/duckdb hold "" as a real
    empty string under pyarrow-string dtype (which keeps an NA mask).
    Both are semantically the GMNS spec's `missingValues` ``""``. We
    convert every column to plain Python ``str`` with ``""`` for missing
    so the assertion compares values rather than NA-masks or dtype
    extensions.
    """
    pk = df.columns[0]
    df = df.sort_values(pk).reset_index(drop=True).copy()
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        # convert to plain object then stringify; missing -> ""
        s = df[col].astype(object)
        s = s.where(df[col].notna(), "")
        out[col] = s.map(lambda v: "" if v == "" else str(v))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_accessor_paths_exist() -> None:
    """The accessor returns paths that all resolve to existing files."""
    assert leavenworth.csv_dir().is_dir()
    assert leavenworth.parquet_dir().is_dir()
    assert leavenworth.duckdb_path().is_file()
    assert leavenworth.zip_path().is_file()
    assert leavenworth.DATAPACKAGE.is_file()


def test_csv_loads() -> None:
    """node.csv and link.csv load via pandas with no NaN in required fields."""
    nodes = _read_csv("node")
    links = _read_csv("link")
    assert len(nodes) >= 1
    assert len(links) >= 1
    for col in ("node_id", "x_coord", "y_coord"):
        assert col in nodes.columns
        assert nodes[col].notna().all(), f"NaN in required node column {col}"
    for col in ("link_id", "from_node_id", "to_node_id", "directed"):
        assert col in links.columns
        assert links[col].notna().all(), f"NaN in required link column {col}"


def test_datapackage_lists_resources() -> None:
    """datapackage.json references each bundled CSV with a schema reference."""
    pkg = json.loads(leavenworth.DATAPACKAGE.read_text())
    resource_names = {r["name"] for r in pkg["resources"]}
    for name in EXPECTED_TABLES:
        assert name in resource_names, f"datapackage missing resource {name}"
    assert pkg["spec"]["version"] == "0.97"


def test_datapackage_schemas_match_vendored_spec() -> None:
    """Each resource inlines its resolved GMNS 0.97 schema, identical to the vendored one (#127).

    The descriptor is self-contained (no path into ``netstead/spec``), so
    this is the drift guard: when ``spec/0.97/`` changes, rerun
    ``fixtures/leavenworth/scripts/descriptor.py``.
    """
    pkg = json.loads(leavenworth.DATAPACKAGE.read_text())
    spec_dir = get_spec_path("0.97")
    for resource in pkg["resources"]:
        expected = load_schema(spec_dir / f"{resource['name']}.schema.json")
        assert resource["schema"] == expected.model_dump(mode="json", by_alias=True, exclude_none=True), (
            f"{resource['name']}: schema drifted from spec/0.97; rerun fixtures/leavenworth/scripts/descriptor.py"
        )


def test_datapackage_loads_after_being_moved(tmp_path: Path) -> None:
    """The descriptor resolves on its own, away from the package tree."""
    moved = tmp_path / "datapackage.json"
    moved.write_bytes(leavenworth.DATAPACKAGE.read_bytes())
    pkg = load_package(moved)
    assert {r.name for r in pkg.resources} >= set(EXPECTED_TABLES)


def test_zip_carries_the_current_datapackage() -> None:
    """The zip's embedded descriptor is the same file as the on-disk one."""
    with zipfile.ZipFile(leavenworth.zip_path()) as zf:
        assert zf.read("datapackage.json") == leavenworth.DATAPACKAGE.read_bytes()


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_parquet_csv_roundtrip(table: str) -> None:
    """csv and parquet variants of every table must hold identical data."""
    csv_df = _normalize(_read_csv(table))
    pq_df = _normalize(_read_parquet(table))
    # Float dtypes can differ between csv (object/float64) and parquet (float64)
    # so coerce on common cols + compare values.
    pd.testing.assert_frame_equal(csv_df, pq_df, check_dtype=False, check_like=True)


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_duckdb_csv_roundtrip(table: str) -> None:
    """csv and duckdb variants of every table must hold identical data."""
    csv_df = _normalize(_read_csv(table))
    duck_df = _normalize(_read_duckdb(table))
    pd.testing.assert_frame_equal(csv_df, duck_df, check_dtype=False, check_like=True)


def test_zip_csv_extracts() -> None:
    """The zipped-CSV bundle contains every expected table + datapackage.json."""
    with zipfile.ZipFile(leavenworth.zip_path()) as zf:
        members = set(zf.namelist())
    for name in EXPECTED_TABLES:
        assert f"{name}.csv" in members, f"zip missing {name}.csv"
    assert "datapackage.json" in members


def test_zip_csv_data_matches() -> None:
    """A table read from the zip equals the same table read from csv/."""
    with zipfile.ZipFile(leavenworth.zip_path()) as zf, zf.open("link.csv") as fh:
        zipped = pd.read_csv(fh)
    on_disk = _read_csv("link")
    pd.testing.assert_frame_equal(_normalize(zipped), _normalize(on_disk))


# ---------------------------------------------------------------------------
# Foreign-key consistency (5 explicit checks)
# ---------------------------------------------------------------------------


def test_fk_link_from_node() -> None:
    nodes = _read_csv("node")
    links = _read_csv("link")
    bad = set(links["from_node_id"]) - set(nodes["node_id"])
    assert not bad, f"link.from_node_id not in node: {bad}"


def test_fk_link_to_node() -> None:
    nodes = _read_csv("node")
    links = _read_csv("link")
    bad = set(links["to_node_id"]) - set(nodes["node_id"])
    assert not bad, f"link.to_node_id not in node: {bad}"


def test_fk_link_geometry() -> None:
    geom = _read_csv("geometry")
    links = _read_csv("link")
    bad = set(links["geometry_id"]) - set(geom["geometry_id"])
    assert not bad, f"link.geometry_id not in geometry: {bad}"


def test_fk_lane_link() -> None:
    lanes = _read_csv("lane")
    links = _read_csv("link")
    bad = set(lanes["link_id"]) - set(links["link_id"])
    assert not bad, f"lane.link_id not in link: {bad}"


def test_fk_link_tod() -> None:
    link_tod = _read_csv("link_tod")
    links = _read_csv("link")
    tsd = _read_csv("time_set_definitions")
    bad = set(link_tod["link_id"]) - set(links["link_id"])
    assert not bad, f"link_tod.link_id not in link: {bad}"
    bad = set(link_tod["timeday_id"]) - set(tsd["timeday_id"])
    assert not bad, f"link_tod.timeday_id not in time_set_definitions: {bad}"


# ---------------------------------------------------------------------------
# Shared_categories enum coverage
# ---------------------------------------------------------------------------


def test_shared_categories_node_ctrl_type() -> None:
    nodes = _read_csv("node")
    bad = set(nodes["ctrl_type"].dropna()) - CTRL_TYPE_VALUES
    assert not bad, f"non-spec ctrl_type values: {bad}"


def test_shared_categories_link_bike_facility() -> None:
    links = _read_csv("link")
    bad = set(links["bike_facility"].dropna()) - BIKE_FACILITY_VALUES
    assert not bad, f"non-spec bike_facility values: {bad}"


def test_shared_categories_link_ped_facility() -> None:
    links = _read_csv("link")
    bad = set(links["ped_facility"].dropna()) - PED_FACILITY_VALUES
    assert not bad, f"non-spec ped_facility values: {bad}"


def test_shared_categories_link_parking() -> None:
    links = _read_csv("link")
    bad = set(links["parking"].dropna()) - PARKING_VALUES
    assert not bad, f"non-spec parking values: {bad}"


def test_at_least_one_tod_restriction_present() -> None:
    """Spec contract: fixture must include at least one TOD restriction."""
    link_tod = _read_csv("link_tod")
    assert len(link_tod) >= 1


def test_covers_at_least_six_table_types() -> None:
    """Spec contract: fixture must cover at least 6 distinct GMNS table types."""
    assert len(_all_tables()) >= 6


# ---------------------------------------------------------------------------
# Wheel-bloat guard
# ---------------------------------------------------------------------------


def test_total_size_under_5mb() -> None:
    """Sum of bundled data files (excluding README + scripts) must be < 5 MB."""
    root = Path(leavenworth.ROOT)
    tracked: list[Path] = []
    tracked.extend(leavenworth.csv_dir().glob("*.csv"))
    tracked.extend(leavenworth.parquet_dir().glob("*.parquet"))
    tracked.append(leavenworth.duckdb_path())
    tracked.append(leavenworth.zip_path())
    tracked.append(leavenworth.DATAPACKAGE)
    total = sum(p.stat().st_size for p in tracked)
    assert total < 5 * 1024 * 1024, (
        f"fixture data is {total / (1024 * 1024):.2f} MB - over 5 MB cap. "
        f"Shrink the OSM dist=, drop optional tables, or trim per-table columns."
    )
    # silence unused-root warning
    assert root.is_dir()
