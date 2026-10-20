"""Tests for the Parquet FormatAdapter (single-file + Hive-partitioned dirs).

The architecture (docs/architecture.md §6.1) calls partitioned parquet the
recommended persistent layout and requires partition-pruning to be
verified via duckdb ``EXPLAIN`` snapshot tests. The
``test_partition_pruning_via_explain`` case in this file is the
load-bearing check for that requirement; if it regresses, the
partition-prune story is broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from corral.engines import get_engine, list_engines
from corral.io import FormatAdapter, dispatch, get_adapter, list_adapters
from corral.io.base import ResourceRef
from corral.io.parquet_adapter import ParquetAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LEAVENWORTH_PARQUET_DIR = Path(__file__).parents[3] / "netstead" / "netstead" / "fixtures" / "leavenworth" / "parquet"
LEAVENWORTH_LINK_PARQUET = LEAVENWORTH_PARQUET_DIR / "link.parquet"


@pytest.fixture()
def adapter() -> ParquetAdapter:
    return ParquetAdapter()


@pytest.fixture()
def synthetic_partitioned_dir(tmp_path: Path) -> Path:
    """A small Hive-partitioned dataset: bbox-style + facility_type partition.

    Layout::

        dataset/
          facility_type=primary/part-0.parquet      (3 rows, x in [0,5))
          facility_type=residential/part-0.parquet  (3 rows, x in [10,15))
          facility_type=secondary/part-0.parquet    (3 rows, x in [20,25))

    The non-overlapping x ranges per partition let bbox filters prune by
    facility_type cleanly. Each partition has 3 rows, total 9.
    """
    root = tmp_path / "dataset"
    for ft, x_start in [("primary", 0), ("residential", 10), ("secondary", 20)]:
        part_dir = root / f"facility_type={ft}"
        part_dir.mkdir(parents=True)
        table = pa.table(
            {
                "link_id": [x_start, x_start + 1, x_start + 2],
                "x": [float(x_start), float(x_start + 1), float(x_start + 2)],
                "y": [0.0, 1.0, 2.0],
            }
        )
        pq.write_table(table, part_dir / "part-0.parquet")
    return root


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def test_probe_parquet_extension(adapter: ParquetAdapter, tmp_path: Path) -> None:
    """Extension sniff: ``.parquet`` is True, ``.csv`` is False."""
    p = tmp_path / "foo.parquet"
    p.write_bytes(b"")  # contents irrelevant to extension check
    assert adapter.probe(p) is True
    assert adapter.probe(tmp_path / "foo.csv") is False


def test_probe_partitioned_directory(adapter: ParquetAdapter, tmp_path: Path) -> None:
    """A Hive-partitioned dir probes True; empty/non-parquet dirs probe False."""
    # Hive-style partitioned dir
    hive_dir = tmp_path / "dataset"
    (hive_dir / "h3=abc").mkdir(parents=True)
    (hive_dir / "h3=abc" / "part-0.parquet").write_bytes(b"")
    assert adapter.probe(hive_dir) is True

    # Empty dir
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter.probe(empty) is False

    # Dir with only non-parquet files
    csv_only = tmp_path / "csv_only"
    csv_only.mkdir()
    (csv_only / "data.csv").write_text("a,b\n1,2\n")
    assert adapter.probe(csv_only) is False


def test_probe_directory_with_metadata_file(adapter: ParquetAdapter, tmp_path: Path) -> None:
    """Pyarrow-style ``_metadata`` sidecar at the root also counts as parquet."""
    d = tmp_path / "ds"
    d.mkdir()
    (d / "_metadata").write_bytes(b"")
    (d / "part-0.parquet").write_bytes(b"")
    assert adapter.probe(d) is True


def test_probe_never_raises(adapter: ParquetAdapter) -> None:
    """probe() must be total — even on missing paths or weird inputs."""
    assert adapter.probe(Path("/this/path/does/not/exist")) is False
    assert adapter.probe("not_a_file.unknownext") is False


# ---------------------------------------------------------------------------
# Registration + dispatch
# ---------------------------------------------------------------------------


def test_self_registers_on_import() -> None:
    """Importing the adapter module registers a parquet adapter globally."""
    assert "parquet" in list_adapters()
    assert get_adapter("parquet").name == "parquet"


def test_dispatch_routes_parquet() -> None:
    """``dispatch("foo.parquet")`` resolves to the parquet adapter."""
    assert dispatch("foo.parquet").name == "parquet"


def test_protocol_conformance(adapter: ParquetAdapter) -> None:
    """ParquetAdapter satisfies the runtime_checkable FormatAdapter protocol."""
    assert isinstance(adapter, FormatAdapter)


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


def test_scan_single_file(adapter: ParquetAdapter) -> None:
    """A single .parquet file yields exactly one ResourceRef."""
    engine = get_engine("ibis")
    listing = adapter.scan(LEAVENWORTH_LINK_PARQUET, engine)
    assert len(listing) == 1
    ref = listing[0]
    assert isinstance(ref, ResourceRef)
    assert ref.name == "link"
    assert ref.format == "parquet"
    assert ref.path == str(LEAVENWORTH_LINK_PARQUET)


def test_scan_partitioned_directory(adapter: ParquetAdapter, synthetic_partitioned_dir: Path) -> None:
    """A partitioned dir is one logical table — name is the dir basename."""
    engine = get_engine("ibis")
    listing = adapter.scan(synthetic_partitioned_dir, engine)
    assert len(listing) == 1
    ref = listing[0]
    assert ref.name == synthetic_partitioned_dir.name  # "dataset"
    assert ref.format == "parquet"
    assert ref.path == str(synthetic_partitioned_dir)


# ---------------------------------------------------------------------------
# read() — single-file, cross-engine
# ---------------------------------------------------------------------------


def _engines_available() -> list[str]:
    """Engines actually registered in this test environment."""
    return [n for n in ("ibis", "polars", "pandas") if n in list_engines()]


@pytest.mark.parametrize("engine_name", _engines_available())
def test_read_single_file_delegates_to_engine(adapter: ParquetAdapter, engine_name: str) -> None:
    """Adapter.read on a .parquet file produces a frame with the link schema.

    Cross-engine: ibis returns Table, polars returns LazyFrame, pandas
    returns DataFrame. We funnel all three through ``engine.to_pandas``
    to compare row counts and required columns uniformly.
    """
    engine = get_engine(engine_name)
    expr = adapter.read(LEAVENWORTH_LINK_PARQUET, engine)
    df = engine.to_pandas(expr)
    assert len(df) == 339  # Leavenworth fixture link count
    assert "link_id" in df.columns
    assert "from_node_id" in df.columns


# ---------------------------------------------------------------------------
# read() — partitioned, Hive partition columns must surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", _engines_available())
def test_read_partitioned_dir_delegates_with_hive_partitioning(
    adapter: ParquetAdapter,
    synthetic_partitioned_dir: Path,
    engine_name: str,
) -> None:
    """Reading a partitioned dir must inject the Hive partition column."""
    engine = get_engine(engine_name)
    expr = adapter.read(synthetic_partitioned_dir, engine)
    df = engine.to_pandas(expr)
    assert len(df) == 9  # 3 partitions * 3 rows
    assert "facility_type" in df.columns
    # All three partition values present
    values = set(str(v) for v in df["facility_type"].unique())
    assert values == {"primary", "residential", "secondary"}


# ---------------------------------------------------------------------------
# Partition-pruning — the architecture-mandated check
# ---------------------------------------------------------------------------


def test_partition_pruning_via_explain(synthetic_partitioned_dir: Path) -> None:
    """duckdb's EXPLAIN plan on a filtered Hive-partitioned read must show
    fewer files scanned than the total partition count.

    This is the architecture §6.1 requirement: ``"For partitioned parquet,
    bbox scope becomes true partition prune via duckdb pushdown — verified
    via EXPLAIN snapshot tests."`` If duckdb ever stops pruning, this test
    is how we find out.

    The EXPLAIN syntax is fussy: we use the raw duckdb python client
    (rather than ibis) because ibis's ``.explain()`` strips the file-list
    portion of the plan. duckdb's text-mode ``EXPLAIN`` summarizes the
    READ_PARQUET node with a line of the form ``Scanning Files: N/M``
    where ``N`` is the post-prune count and ``M`` is the total. We assert
    on both that line and the ``File Filters`` line that lists the
    pushed-down predicate — together they prove pruning actually
    happened.
    """
    import re

    import duckdb

    con = duckdb.connect(":memory:")
    glob = f"{synthetic_partitioned_dir}/**/*.parquet"
    # Total partition count baseline (no filter, no pruning)
    row = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{glob}', hive_partitioning=true)"  # pragma: allow-sql
    ).fetchone()
    assert row is not None
    assert row[0] == 9

    # Filtered EXPLAIN plan — duckdb's READ_PARQUET node summarizes the
    # file scan with ``Scanning Files: N/M``.
    explain = con.execute(
        f"EXPLAIN SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "  # pragma: allow-sql
        f"WHERE facility_type = 'primary'"  # pragma: allow-sql
    ).fetchall()
    # EXPLAIN returns rows of (plan_type, plan_text); concatenate plan text.
    plan_text = "\n".join(row[1] for row in explain)

    # The plan must show the filter pushed down to the read.
    assert "File Filters" in plan_text, f"no File Filters line in plan:\n{plan_text}"
    assert "facility_type" in plan_text and "'primary'" in plan_text, f"predicate not visible in plan:\n{plan_text}"

    # ``Scanning Files: N/M`` — must be N < M to prove the prune.
    match = re.search(r"Scanning Files:\s*(\d+)\s*/\s*(\d+)", plan_text)
    assert match is not None, f"no 'Scanning Files: N/M' line in plan:\n{plan_text}"
    scanned, total_files = int(match.group(1)), int(match.group(2))
    assert total_files == 3, f"expected 3 total partition files; got {total_files}"
    assert scanned < total_files, (
        f"expected partition prune (scanned < {total_files}); got scanned={scanned}. Plan text:\n{plan_text}"
    )


# ---------------------------------------------------------------------------
# write() roundtrips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", _engines_available())
def test_write_single_file_roundtrip(
    adapter: ParquetAdapter,
    tmp_path: Path,
    engine_name: str,
) -> None:
    """scan → write to tmp/foo.parquet → re-scan → equal row count + columns."""
    engine = get_engine(engine_name)
    expr = adapter.read(LEAVENWORTH_LINK_PARQUET, engine)
    dest = tmp_path / "out.parquet"
    adapter.write(expr, dest, engine)
    assert dest.exists()

    reread = adapter.read(dest, engine)
    df = engine.to_pandas(reread)
    assert len(df) == 339
    assert set(["link_id", "from_node_id", "to_node_id"]).issubset(df.columns)


def test_write_partitioned_roundtrip(adapter: ParquetAdapter, tmp_path: Path) -> None:
    """scan → write to tmp/dir/ with partition_by=['facility_type'] → re-scan.

    pyarrow re-injects the partition column on read, so we compare the
    non-partition columns and the total row count.
    """
    engine = get_engine("ibis")
    expr = adapter.read(LEAVENWORTH_LINK_PARQUET, engine)
    orig_df = engine.to_pandas(expr)

    dest_dir = tmp_path / "partitioned"
    adapter.write(expr, dest_dir, engine, partition_by=["facility_type"])
    assert dest_dir.is_dir()
    # At least one Hive-style subdir was created
    subdirs = [p for p in dest_dir.iterdir() if p.is_dir() and "=" in p.name]
    assert len(subdirs) > 0

    reread = adapter.read(dest_dir, engine)
    rt_df = engine.to_pandas(reread)
    assert len(rt_df) == len(orig_df)
    # Same logical columns (pyarrow re-injects facility_type from path).
    assert "facility_type" in rt_df.columns
    assert "link_id" in rt_df.columns


def test_write_partitioned_streams_without_pandas(adapter: ParquetAdapter, tmp_path: Path, monkeypatch) -> None:
    """#135: a partitioned write streams Arrow record batches; it never collects the table through pandas."""
    engine = get_engine("ibis")
    expr = adapter.read(LEAVENWORTH_LINK_PARQUET, engine)
    expected_rows = int(expr.count().to_pyarrow().as_py())

    def no_pandas(*_args, **_kwargs):
        raise AssertionError("partitioned write materialized through pandas")

    monkeypatch.setattr(engine, "to_pandas", no_pandas)
    monkeypatch.setattr(type(expr), "to_pandas", no_pandas)
    monkeypatch.setattr(type(expr), "to_pyarrow", no_pandas)  # the whole table at once is just as bad

    dest_dir = tmp_path / "partitioned"
    adapter.write(expr, dest_dir, engine, partition_by=["facility_type"])

    reread = adapter.read(dest_dir, engine)
    assert int(reread.count().to_pyarrow_batches().read_all().column(0)[0].as_py()) == expected_rows


# ---------------------------------------------------------------------------
# kwarg pass-through
# ---------------------------------------------------------------------------


def test_read_passes_extra_kwargs_through(adapter: ParquetAdapter) -> None:
    """Caller kwargs reach ``engine.read_parquet`` unmodified.

    Uses a stub engine that records the primitive invocation rather
    than a real engine, because each engine has its own kwarg-allowlist
    for parquet reads — we want to assert pass-through, not assert
    against duckdb's specific kwarg vocabulary.
    """
    captured: dict[str, Any] = {}

    class _RecordingEngine:
        name = "recording"

        def read_parquet(
            self,
            source: Any,
            schema: Any | None = None,
            *,
            hive_partitioning: bool = False,
            **kw: Any,
        ) -> str:
            captured.update(
                {
                    "source": source,
                    "schema": schema,
                    "hive_partitioning": hive_partitioning,
                    **kw,
                }
            )
            return "ok"

    result = adapter.read(LEAVENWORTH_LINK_PARQUET, _RecordingEngine(), my_custom_kwarg=42)
    assert result == "ok"
    assert captured["my_custom_kwarg"] == 42
    assert captured["source"] == str(LEAVENWORTH_LINK_PARQUET)
    # Single-file parquet: adapter did not auto-enable hive partitioning.
    assert captured["hive_partitioning"] is False


# ---------------------------------------------------------------------------
# Adapter-level edge cases
# ---------------------------------------------------------------------------


def test_scan_partitioned_dir_with_trailing_slash(adapter: ParquetAdapter, synthetic_partitioned_dir: Path) -> None:
    """Trailing slash on the dir source should not change the resource name."""
    engine: Any = get_engine("ibis")
    with_slash = Path(str(synthetic_partitioned_dir) + "/")
    listing = adapter.scan(with_slash, engine)
    assert listing[0].name == synthetic_partitioned_dir.name


class _RecordingEngine:
    """Records what ``read_parquet`` receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read_parquet(self, path: str, **kwargs: Any) -> str:
        self.calls.append((path, kwargs))
        return "expr"


@pytest.mark.parametrize("url", ["https://h/x.parquet", "s3://bucket/dir/x.parquet", "s3://b/../../secret.parquet"])
def test_read_passes_remote_url_unmangled(adapter: ParquetAdapter, url: str) -> None:
    """``Path(url)`` collapses ``//`` into a local relative path; the URL must reach the engine verbatim."""
    engine = _RecordingEngine()
    adapter.read(url, engine)  # type: ignore[arg-type]
    assert engine.calls == [(url, {"schema": None, "hive_partitioning": False})]


def test_scan_keeps_remote_url_unmangled(adapter: ParquetAdapter) -> None:
    assert adapter.scan("https://h/data/link.parquet") == [
        ResourceRef(name="link", path="https://h/data/link.parquet", format="parquet")
    ]
