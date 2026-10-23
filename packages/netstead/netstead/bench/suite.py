"""Macro benchmark runner: network creation, selections, and viz packing.

The three operation families from the benchmarking-suite scope, measured for
**time and peak memory**. DuckDB (via ibis) is the single compute engine — the
old per-engine axis is gone — so this measures the DuckDB path and the *format*
of the binary network store (CSV / Parquet / DuckDB today; GeoParquet slots in
as another format once its writer lands, with no reshaping of the output).

All heavy or optional imports (``netstead.osm``, ``netstead.select``,
``netstead.viz``) are deferred into the functions that need them, so importing
this module is cheap and never requires an extra a given run does not use. The
OSM path uses :func:`importlib.import_module` specifically — the
architecture-blessed way to reach the ``[osm]`` extra without a static import
that would trip the ``netstead.cli -> netstead.osm`` import-linter contract.
"""

from __future__ import annotations

import importlib
import json
import struct
from typing import Any

from .fixtures import FixtureSpec, resolve_fixtures
from .measure import (
    frame_memory_mb,
    memory_call,
    repeat_wall_seconds,
    summarize_seconds,
    time_call,
)
from .report import BenchResult, build_document

__all__ = [
    "bench_network_create",
    "bench_selection",
    "bench_viz_pack",
    "run_suite",
]

#: DuckDB's own buffer-manager memory is not in the Python heap and can spill to
#: disk; tracemalloc under-reports it. Emitted alongside create memory numbers.
_DUCKDB_MEM_NOTE = "duckdb buffer-manager memory is out-of-heap; peak_rss_mb is partial"


def _to_pandas(table: Any) -> Any:
    """Materialize a corral table (or pass through a frame) to pandas."""
    if hasattr(table, "to_pandas"):
        return table.to_pandas()
    if hasattr(table, "execute"):
        return table.execute()
    return table


def _load_frames(source: Any) -> tuple[Any, Any]:
    """Read a GMNS network from ``source`` and return (links_df, nodes_df)."""
    netstead = importlib.import_module("netstead")
    net = netstead.read(source)
    return _to_pandas(net.links), _to_pandas(net.nodes)


def _count_network(source: Any) -> tuple[int, int]:
    """Read ``source`` and force materialization, returning (links, nodes) counts."""
    netstead = importlib.import_module("netstead")
    net = netstead.read(source)
    return int(net.links.count()), int(net.nodes.count())


def bench_network_create(spec: FixtureSpec, *, repeats: int = 3, run_osm: bool = False) -> list[BenchResult]:
    """Benchmark loading a network from each committed storage format (+ optional OSM).

    Time and memory are captured in separate passes. The result counts are a
    correctness co-assertion: a "fast" run that dropped rows is a failure, not
    a win.

    Args:
        spec: The fixture to build.
        repeats: Macro timing repetitions (reported as median + min).
        run_osm: Also build live from OSM when the fixture carries a bbox
            (hits Overpass; off by default).

    Returns:
        One :class:`BenchResult` per format (and one for OSM when requested).
    """
    results: list[BenchResult] = []
    for fmt, source in spec.source_dirs.items():
        samples = repeat_wall_seconds(lambda src=source: _count_network(src), repeats)
        (links, nodes), tsample = time_call(lambda src=source: _count_network(src))
        _, msample = memory_call(lambda src=source: _load_frames(src))
        link_df, node_df = _load_frames(source)
        frame_mb = (frame_memory_mb(link_df) or 0.0) + (frame_memory_mb(node_df) or 0.0)
        results.append(
            BenchResult(
                operation="network_create",
                size=spec.tier,
                label=f"{spec.id}:{fmt}",
                metrics={
                    **summarize_seconds(samples),
                    "cpu_seconds": tsample.cpu_seconds,
                },
                mem={
                    "py_heap_peak_mb": msample.py_heap_peak_mb,
                    "peak_rss_mb": msample.peak_rss_mb,
                    "frame_mb": round(frame_mb, 3),
                    "note": _DUCKDB_MEM_NOTE,
                },
                counts=_count_assertion(spec, links, nodes),
            )
        )

    if run_osm and spec.bbox is not None:
        results.append(_bench_osm_create(spec, repeats=repeats))
    return results


def _count_assertion(spec: FixtureSpec, links: int, nodes: int) -> dict[str, Any]:
    """Counts dict with an ``ok`` flag against the fixture's expected counts."""
    counts: dict[str, Any] = {"links": links, "nodes": nodes}
    if spec.expected_links is not None:
        counts["expected_links"] = spec.expected_links
        counts["ok"] = links == spec.expected_links and (spec.expected_nodes is None or nodes == spec.expected_nodes)
    return counts


def _bench_osm_create(spec: FixtureSpec, *, repeats: int) -> BenchResult:
    """Build the fixture live from OSM; split fetch (I/O) from assemble (engine)."""
    osm_build = importlib.import_module("netstead.osm.build")

    def _build() -> tuple[int, int]:
        net = osm_build.build_network_from_osm(spec.bbox, network_type="roads")
        return int(net.links.count()), int(net.nodes.count())

    (links, nodes), tsample = time_call(_build)
    return BenchResult(
        operation="network_create",
        size=spec.tier,
        label=f"{spec.id}:osm",
        metrics={"total_seconds": tsample.wall_seconds, "cpu_seconds": tsample.cpu_seconds},
        counts={"links": links, "nodes": nodes},
        extra={"note": "includes live Overpass fetch (network I/O); run with repeats=1"},
    )


def bench_selection(spec: FixtureSpec, links: Any, nodes: Any, *, warm_repeats: int = 20) -> list[BenchResult]:
    """Benchmark the versioned selection cases via the StubParser + resolver.

    Uses the :class:`~netstead.select.parse.StubParser` (never the Claude parser)
    so the timed path is the resolver, not an LLM round-trip. Reports both a
    cold number (first resolve) and a warm number (min + median over repeats).
    Returns ``[]`` when the ``[nl]`` extra (jsonschema) is absent.

    Args:
        spec: The fixture (supplies the selection cases).
        links: Materialized pandas link frame.
        nodes: Materialized pandas node frame.
        warm_repeats: Repetitions for the warm number.

    Returns:
        One :class:`BenchResult` per selection case.
    """
    try:
        select = importlib.import_module("netstead.select")
    except ImportError:
        return []  # [nl] extra absent — skip this operation cleanly

    parser = select.StubParser()
    resolve_frames = select.resolve_frames
    results: list[BenchResult] = []
    for case in spec.selections:
        try:
            intent = parser.parse(case.utterance)
        except Exception:
            continue  # a versioned utterance that no longer parses — skip it
        result, cold = time_call(lambda it=intent: resolve_frames(it, links, nodes))
        warm = summarize_seconds(repeat_wall_seconds(lambda it=intent: resolve_frames(it, links, nodes), warm_repeats))
        result_links = len(result.link_ids) if getattr(result, "link_ids", None) else 0
        results.append(
            BenchResult(
                operation="selection",
                size=spec.tier,
                label=f"{spec.id}:{case.case}",
                metrics={
                    "cold_seconds": cold.wall_seconds,
                    "warm_seconds_min": warm["min"],
                    "warm_seconds_median": warm["median"],
                },
                counts={"result_links": result_links, "status": result.status},
                extra={"utterance": case.utterance},
            )
        )
    return results


def _payload_breakdown(blob: bytes) -> dict[str, Any]:
    """Read the pack_network header for a per-array byte breakdown."""
    (header_len,) = struct.unpack_from("<I", blob, 0)
    header = json.loads(blob[4 : 4 + header_len].decode("utf-8"))
    return header


def bench_viz_pack(spec: FixtureSpec, links: Any, nodes: Any, *, repeats: int = 5) -> list[BenchResult]:
    """Benchmark ``viz.buffers.pack_network`` time + payload size over a network.

    Measures pack wall-time (min + median), the payload byte count and its
    per-array breakdown (what the browser downloads), and a geometry-free pass
    to separate WKT-parse cost from buffer assembly.

    Args:
        spec: The fixture.
        links: Materialized pandas link frame.
        nodes: Materialized pandas node frame.
        repeats: Pack timing repetitions.

    Returns:
        A single-element list with the viz-pack result.
    """
    buffers = importlib.import_module("netstead.viz.buffers")
    pack_network = buffers.pack_network

    blob, _ = time_call(lambda: pack_network(links, nodes))
    samples = summarize_seconds(repeat_wall_seconds(lambda: pack_network(links, nodes), repeats))

    links_no_geom = links.drop(columns=["geometry"]) if "geometry" in getattr(links, "columns", []) else links
    _, no_geom = time_call(lambda: pack_network(links_no_geom, nodes))

    return [
        BenchResult(
            operation="viz_pack",
            size=spec.tier,
            label=spec.id,
            metrics={
                "seconds_min": samples["min"],
                "seconds_median": samples["median"],
                "seconds_no_geometry": no_geom.wall_seconds,
            },
            counts={"links": len(links), "nodes": len(nodes)},
            extra={"payload_bytes": len(blob), "payload_breakdown": _payload_breakdown(blob)},
        )
    ]


def run_suite(
    *,
    sizes: tuple[str, ...] = ("S", "M"),
    operations: tuple[str, ...] = ("network_create", "selection", "viz_pack"),
    repeats: int = 3,
    warm_repeats: int = 20,
    run_osm: bool = False,
    fixtures_path: str | None = None,
    commit: str | None = None,
) -> dict[str, Any]:
    """Run the benchmark suite and return the canonical result document.

    Args:
        sizes: Size tiers to sweep (``S`` / ``M`` / ``L``).
        operations: Which operation families to run.
        repeats: Macro timing repetitions for create + viz.
        warm_repeats: Repetitions for the warm selection number.
        run_osm: Include a live OSM build for fixtures carrying a bbox.
        fixtures_path: Optional override for ``fixtures.toml``.
        commit: Git SHA stamped into the env block.

    Returns:
        A document from :func:`netstead.bench.report.build_document`.
    """
    specs = resolve_fixtures(sizes, path=fixtures_path)
    results: list[BenchResult] = []

    for spec in specs:
        if "network_create" in operations:
            results.extend(bench_network_create(spec, repeats=repeats, run_osm=run_osm))

        needs_frames = ("selection" in operations or "viz_pack" in operations) and spec.source_dirs
        if not needs_frames:
            continue
        source = spec.primary_source()
        if source is None:
            continue
        links, nodes = _load_frames(source)

        if "selection" in operations:
            results.extend(bench_selection(spec, links, nodes, warm_repeats=warm_repeats))
        if "viz_pack" in operations:
            results.extend(bench_viz_pack(spec, links, nodes, repeats=repeats))

    return build_document(results, commit=commit)
