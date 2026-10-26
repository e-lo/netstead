#!/usr/bin/env python
"""Benchmark the netstead OSM->GMNS build (and optional osmnx / osm2gmns baselines).

This is dev tooling, not a CI gate. It measures the *build* path (convert +
Network assembly on the DuckDB engine), holding fetch constant by either
generating a synthetic grid or fetching a small live bbox once and reusing the
parsed elements. It reports build wall-clock, peak memory, and the resulting
node/link counts, optionally alongside directional osmnx / osm2gmns baselines.
For the per-operation timing suite, use ``netstead bench`` instead.

Usage examples::

    # Synthetic small/medium/large grids (no network):
    uv run python scripts/bench_osm_build.py --grids 10,40,120 --json

    # A small live bbox (hits Overpass once):
    uv run python scripts/bench_osm_build.py --bbox -122.30,37.86,-122.25,37.88 --json

    # Add directional osmnx / osm2gmns baselines (best-effort, see caveats):
    uv run python scripts/bench_osm_build.py --bbox -122.30,37.86,-122.25,37.88 \
        --baselines --osm-file bayarea.osm --json

Baseline caveat: osmnx and osm2gmns use different topology rules (simplification,
directed-link convention) and attribute sets than netstead, so node/link counts
and timings are *directional*, not apples-to-apples.

A synthetic grid of side N has N*N nodes and 2*N ways; suggested sizes:
small=10 (100 nodes), medium=40 (~1.6k nodes), large=120 (~14k nodes).
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import tracemalloc

from netstead.osm import build, convert


def synthetic_grid(n: int):
    """Return (nodes, ways) for an N-by-N grid of two-way residential streets."""
    nodes = {row * n + col + 1: (-71.0 + 0.001 * col, 42.0 + 0.001 * row) for row in range(n) for col in range(n)}
    ways = []
    way_id = 0
    for row in range(n):
        way_id += 1
        ways.append({"id": way_id, "nodes": [row * n + c + 1 for c in range(n)], "tags": {"highway": "residential"}})
    for col in range(n):
        way_id += 1
        ways.append({"id": way_id, "nodes": [r * n + col + 1 for r in range(n)], "tags": {"highway": "residential"}})
    return nodes, ways


def _timed(fn):
    """Run fn(), returning (result, seconds, peak_mb)."""
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    seconds = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, round(seconds, 4), round(peak / 1e6, 2)


def bench_build(node_recs, link_recs):
    """Time the records->Network build on the (DuckDB) default engine."""

    def _run():
        net = build.network_from_records(node_recs, link_recs)
        # Force materialization so timing reflects real work, not laziness.
        return net.nodes.count(), net.links.count()

    (nodes, links), seconds, peak_mb = _timed(_run)
    return {"build_seconds": seconds, "peak_mb": peak_mb, "nodes": nodes, "links": links}


def baseline_osmnx(bbox):
    """Directional baseline: osmnx graph_from_bbox -> graph_to_gdfs.

    Baselines are ad-hoc only — osmnx / osm2gmns are deliberately NOT declared
    extras. If the library is absent, returns an install prompt instead of a
    timing so the comparison degrades per-tool rather than all-or-nothing.
    """
    try:
        import osmnx as ox
    except ImportError:
        return {"tool": "osmnx", "needs_install": "pip install osmnx"}

    def _run():
        graph = ox.graph_from_bbox(bbox=bbox, network_type="drive")
        gdf_nodes, gdf_edges = ox.graph_to_gdfs(graph)
        return len(gdf_nodes), len(gdf_edges)

    (nodes, edges), seconds, peak_mb = _timed(_run)
    return {"tool": "osmnx", "seconds": seconds, "peak_mb": peak_mb, "nodes": nodes, "edges": edges}


def baseline_osm2gmns(osm_file):
    """Directional baseline: osm2gmns getNetFromFile (needs a local --osm-file)."""
    try:
        import osm2gmns as og
    except ImportError:
        return {"tool": "osm2gmns", "needs_install": "pip install osm2gmns (needs a C/C++ toolchain)"}
    if not osm_file:
        return {"tool": "osm2gmns", "skipped": "pass --osm-file PATH to a local .osm/.pbf to compare osm2gmns"}

    def _run():
        return og.getNetFromFile(osm_file, network_types=("auto",))

    _, seconds, peak_mb = _timed(_run)
    return {"tool": "osm2gmns", "seconds": seconds, "peak_mb": peak_mb}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark the netstead OSM->GMNS build.")
    parser.add_argument("--grids", default="10,40,120", help="Comma-separated synthetic grid sides (NxN).")
    parser.add_argument("--bbox", default=None, help="Live bbox 'west,south,east,north' (fetched once).")
    parser.add_argument("--baselines", action="store_true", help="Run osmnx / osm2gmns directional baselines.")
    parser.add_argument("--osm-file", default=None, help="Local .osm/.pbf file for the osm2gmns baseline.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    args = parser.parse_args(argv)

    results = {"datasets": []}

    cases = []
    if args.bbox:
        west, south, east, north = (float(x) for x in args.bbox.split(","))
        from netstead.osm import query

        nodes, ways = query.fetch_network_elements((west, south, east, north), network_type="drive")
        cases.append((f"bbox:{args.bbox}", nodes, ways))
    else:
        for side in (int(s) for s in args.grids.split(",")):
            nodes, ways = synthetic_grid(side)
            cases.append((f"grid:{side}x{side}", nodes, ways))

    for label, nodes, ways in cases:
        node_recs, link_recs = convert.build_node_link_tables(nodes, ways)
        entry = {
            "dataset": label,
            "input_nodes": len(nodes),
            "input_ways": len(ways),
            "netstead": bench_build(node_recs, link_recs),
        }
        if args.baselines:
            entry["baselines"] = [baseline_osmnx(tuple(float(x) for x in args.bbox.split(",")))] if args.bbox else []
            entry["baselines"].append(baseline_osm2gmns(args.osm_file))
        results["datasets"].append(entry)

    if args.json:
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        for entry in results["datasets"]:
            print(f"\n{entry['dataset']}  (in: {entry['input_nodes']} nodes, {entry['input_ways']} ways)")
            row = entry["netstead"]
            print(
                f"  {'netstead':>8}: {row['build_seconds']:>8.4f}s  "
                f"{row['peak_mb']:>8.2f} MB  -> {row['nodes']} nodes / {row['links']} links"
            )
            for base in entry.get("baselines", []):
                if base.get("needs_install"):
                    print(f"  {base['tool']:>8}: not installed — {base['needs_install']}")
                elif base.get("skipped"):
                    print(f"  {base['tool']:>8}: skipped ({base['skipped']})")
                else:
                    print(f"  {base['tool']:>8}: {base.get('seconds')}s  {base.get('peak_mb')} MB (directional)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
