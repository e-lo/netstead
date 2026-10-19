---
title: Run the bundled benchmarks
audience: users
kind: howto
summary: netstead bench measures load + validate + connectivity timings per phase on your network + hardware.
---

# Run the bundled benchmarks

## When to use this

You want a fast read on how `netstead` performs on your hardware against your network — sizing a server, comparing hardware or storage formats, or tracking CI regressions over time.

## Quick example

Run the bench against the bundled Leavenworth fixture. Add `--json` to any `netstead` (or `corral`) CLI command and the output becomes a single machine-readable JSON document on stdout — pipe into `jq`, save to a file, feed to a script or AI agent. Default output is human-readable rich panels:

```bash
netstead bench packages/netstead/netstead/fixtures/leavenworth/csv --json
```

Expected:

```json
{
  "source": "packages/netstead/netstead/fixtures/leavenworth/csv",
  "engine": "IbisEngine",
  "total_seconds": 0.555,
  "phases": [
    {"phase": "load",         "seconds": 0.112},
    {"phase": "validate",     "seconds": 0.130},
    {"phase": "links_count",  "seconds": 0.002},
    {"phase": "nodes_count",  "seconds": 0.001},
    {"phase": "is_connected", "seconds": 0.309}
  ]
}
```

## Step-by-step

### 1. Run against the bundled reference

The Leavenworth fixture is small (339 links / 121 nodes) — total runtime is under a second. It's a baseline, not a benchmark; useful for confirming nothing's wrong with the install:

```bash
netstead bench packages/netstead/netstead/fixtures/leavenworth/csv
```

### 2. Run against your own network

Same command, different path. The bench accepts the same source surface as `Network.from_source` — local dirs, `s3://`, `duckdb://`, etc.:

```bash
netstead bench /path/to/my/network --json > bench.json
```

### 3. Compare storage formats

DuckDB (via ibis) is the single compute engine, so there is no engine to sweep — `--engine` is accepted only for compatibility. What *does* move the numbers is the on-disk format you load from. Convert the same network to CSV and Parquet and bench each:

```bash
netstead bench /path/to/net-csv --json > csv.json
netstead bench /path/to/net-parquet --json > parquet.json
```

### 4. Capture a baseline for CI

For regression tracking, save a baseline JSON in-repo and compare on every PR. A 30% tolerance is reasonable for the Leavenworth fixture given timing noise; tighten on larger networks:

```bash
netstead bench packages/netstead/netstead/fixtures/leavenworth/csv --json > bench-baseline.json
# in CI:
netstead bench packages/netstead/netstead/fixtures/leavenworth/csv --json > bench-current.json
python -c "
import json
b = json.load(open('bench-baseline.json'))['total_seconds']
c = json.load(open('bench-current.json'))['total_seconds']
assert c < b * 1.3, f'regression: {c:.2f}s vs baseline {b:.2f}s (+{(c/b-1)*100:.0f}%)'
"
```

### 5. Read the JSON

The shape is stable inside a major version. Each phase is one logical operation: `load` opens the package; `validate` runs the structural + schema passes only (`net.validate(foreign_keys=False, sync_state=False)` — no FK or sync-state checks); `links_count` / `nodes_count` force a count on each table; `is_connected` builds the scipy `GMNSGraph` and checks that there is a single weakly-connected component:

```json
{
  "source": "packages/netstead/netstead/fixtures/leavenworth/csv",
  "engine": "IbisEngine",
  "total_seconds": 0.555,
  "phases": [
    {"phase": "load",         "seconds": 0.112},
    {"phase": "validate",     "seconds": 0.130},
    {"phase": "links_count",  "seconds": 0.002},
    {"phase": "nodes_count",  "seconds": 0.001},
    {"phase": "is_connected", "seconds": 0.309}
  ]
}
```

## Common variations

???+ note "Default — Leavenworth fixture, JSON out"
    Quickest smoke-test that the install works and the engine is wired up.

    ```bash
    netstead bench packages/netstead/netstead/fixtures/leavenworth/csv --json
    ```

??? note "Just the total seconds (for shell pipelines)"
    Pipe through `jq` to extract a single number.

    ```bash
    netstead bench ./my-net --json | jq -r .total_seconds
    ```

??? note "Phase breakdown as CSV"
    For charting or copying into a spreadsheet.

    ```bash
    netstead bench ./my-net --json | jq -r '.phases[] | [.phase,.seconds] | @csv'
    ```

??? note "CI regression check (one-liner)"
    Fails the CI step if the total exceeds your budget.

    ```bash
    netstead bench ./my-net --json | jq -e '.total_seconds < 5.0'
    ```

??? note "Format sweep"
    Loop over copies of the same network in different storage formats.

    ```bash
    for d in ./my-net-csv ./my-net-parquet; do
      netstead bench "$d" --json > "bench-$(basename "$d").json"
    done
    ```

## Pitfalls

* **Micro-network timing is noisy.** On Leavenworth the absolute numbers fluctuate by ±30% between runs. Use it to confirm correctness, not for sizing decisions — switch to a regional-scale network before drawing conclusions.
* **Every CLI run is a cold start.** Each `netstead bench` invocation is a fresh process, so the `is_connected` phase always includes building the scipy `GMNSGraph` — there is no cache to warm by running the command twice. The graph is cached on the `Network` object only within one Python process (a second `is_connected(net)` call on the same object is faster).
* **Connectivity requires `[graph]`.** Without scipy the `is_connected` phase is skipped and reported as `{"phase": "is_connected", "seconds": null, "skipped": "scipy not installed"}`; `total_seconds` sums the phases that ran. Data-quality rules are not part of `netstead bench`.

## Run the benchmark suite (`netstead bench-suite`)

`netstead bench` times one network end to end. `netstead bench-suite` sweeps *size tiers* over three operation families — `network_create` (load the fixture from each committed storage format, with row counts asserted as a correctness check), `selection` (resolve the versioned natural-language selection cases through the stub parser, cold and warm) and `viz_pack` (pack the network into the browser payload, timing plus byte counts) — and emits one structured document with wall time and peak memory per case. It needs the `[bench]` extra (`psutil`, for process RSS); the `selection` family also wants `[nl]` and returns nothing without it, and live OSM builds want `[osm]`:

```bash
pip install 'netstead[bench]'
netstead bench-suite --sizes S,M --format md --output bench.md
```

| Flag | Default | Meaning |
|---|---|---|
| `--sizes` | `S,M` | Size tiers to sweep, comma-separated from `S`, `M`, `L`. |
| `--operations` | `network_create,selection,viz_pack` | Operation families to run, comma-separated. |
| `--format` | `json` | `json` (the canonical regression document), `csv` (flattened results) or `md` (a table with an environment stamp). |
| `--output` / `-o` | stdout | Write the rendered document to this file. |
| `--repeats` | `3` | Macro timing repeats (reported as median + min). |
| `--warm-repeats` | `20` | Repeats for the warm selection number. |
| `--run-osm` | off | Include live OSM builds (hits Overpass). |
| `--fixtures` | `benchmarks/fixtures.toml` | Override the fixture registry file. |
| `--commit` | — | Git SHA to stamp into the environment block. |

Fixture definitions live in `benchmarks/fixtures.toml` at the repo root (a built-in default covering the bundled fixtures is used when the file isn't found). The `S` tier (Leavenworth, 339 links) and `M` tier (`rdu_i40`) ship inside the wheel. The `L` tier is never committed: point `NETSTEAD_BENCH_LARGE` at a GMNS package directory or a `.duckdb` file, or set `NETSTEAD_BENCH_LARGE_BBOX="west,south,east,north"` and pass `--run-osm` to build it live from OpenStreetMap:

```bash
export NETSTEAD_BENCH_LARGE=/path/to/large-network
netstead bench-suite --sizes S,M,L --format json --output bench.json
```

## Bench the OSM build path (`scripts/bench_osm_build.py`)

A separate harness measures the *construction* of a network from OpenStreetMap (convert + `Network` assembly on the DuckDB engine) and, optionally, compares it against `osmnx` and `osm2gmns` as directional baselines. It lives at `scripts/bench_osm_build.py`. Examples:

```bash
# Synthetic grids (no network I/O — reproducible, fast).
uv run python scripts/bench_osm_build.py --grids 10,40,100

# Real bbox (one Overpass fetch, reused by the netstead build + the osmnx baseline).
uv run python scripts/bench_osm_build.py --bbox=-120.6794,47.5751,-120.6411,47.6082 --baselines
```

Representative numbers (M1 / macOS; absolute numbers are noisy ±30%). The first dataset in a run also pays one-time import / DuckDB warm-up, so treat it as a cold-start figure:

| Dataset | tool | build | peak mem | result (nodes / links) |
|---|---|---|---|---|
| grid 10×10 (100 nodes / 20 ways) | netstead | 0.17 s | 2.6 MB | 100 / 360 |
| grid 40×40 (1.6k nodes / 80 ways) | netstead | 0.05 s | 1.2 MB | 1600 / 6240 |
| grid 100×100 (10k nodes / 200 ways) | netstead | 0.08 s | 5.3 MB | 10000 / 39600 |
| Leavenworth bbox (8.5k OSM nodes → 1.9k GMNS nodes / 4.3k directed links) | netstead | 0.13 s | 2.6 MB | 1871 / 4250 |
| Leavenworth bbox (same area) | osmnx | 1.11 s | 20.3 MB | 1871 / ~4250 (directional) |

Takeaways:

* The **osmnx baseline** is intentionally directional, not apples-to-apples — `osmnx` performs its own simplification and uses a different directed-link convention. Use it as a sanity check that we're in the same order of magnitude, not as a strict speed comparison.
* The `osm2gmns` baseline needs a local `--osm-file` and a C/C++ toolchain to install; when it is missing, the harness prints the install command and skips that baseline cleanly.
* There is no engine axis: DuckDB (via ibis) is the single compute engine. For the broader create / selection / viz-pack timing suite, use `netstead bench-suite`.

## See also

* [Convert CSV ↔ Parquet ↔ DuckDB](https://e-lo.github.io/netstead/corral/cookbook/convert-formats/) — the format you load from dominates `load` time.
* [API reference](../reference/api.md) — `Network.from_source()` + `.validate()` for programmatic equivalents. The timing harness is also importable: `netstead.bench` exports the capture primitives (`time_call`, `memory_call`, `repeat_wall_seconds`, …) and report renderers (`build_document`, `render_json` / `render_csv` / `render_markdown`), and `netstead.bench.suite.run_suite(...)` runs the same suite as `netstead bench-suite`.
