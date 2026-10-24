#!/usr/bin/env python3
r"""Rebuild the bundled Market Street, San Francisco GMNS fixture from Overture Maps.

Builds a small, topologically busy street network around Lotta's Fountain
(Market / Kearny / Geary / 3rd Street) with netstead's own Overture builder,
pinned to a fixed Overture release, and writes the storage variants below
(Leavenworth also ships a .duckdb; skipped here because DuckDB block overhead
alone is about 1 MB for this tiny network):

    csv/                       - canonical
    parquet/                   - columnar
    market_street.csv.zip      - zipped CSV bundle
    datapackage.json           - Frictionless descriptor (copy of the one the writer emits)

The equivalent CLI call is::

    netstead build --source overture --overture-release 2026-09-23.1 \
        --bbox -122.4056,37.7866,-122.4016,37.7894 --network-type roads <dest>

Overture releases are immutable, so a re-run against the same release gives
the same rows. Data (c) OpenStreetMap contributors, Overture Maps Foundation,
licensed under the Open Database License (ODbL); see README.md.

Usage::

    uv run --all-extras python packages/netstead/netstead/fixtures/market_street/scripts/build_market_street.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from netstead.overture import build_network_from_overture

FIXTURE_DIR = Path(__file__).resolve().parent.parent

# west, south, east, north (EPSG:4326): about 350 m x 310 m around Lotta's Fountain.
BBOX = (-122.4056, 37.7866, -122.4016, 37.7894)
OVERTURE_RELEASE = "2026-09-23.1"
# Add "paths" for sidewalks and crossings (about 4x as many links, mostly footways).
NETWORK_TYPE = "roads"


def main() -> None:
    """Build the network from Overture and rewrite every stored form of the fixture."""
    net = build_network_from_overture(BBOX, network_type=NETWORK_TYPE, overture_release=OVERTURE_RELEASE)
    for stale in ("csv", "parquet", "market_street.csv.zip", "datapackage.json"):
        target = FIXTURE_DIR / stale
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    net.write(FIXTURE_DIR / "csv", format="csv")
    net.write(FIXTURE_DIR / "parquet", format="parquet")
    net.write(FIXTURE_DIR / "market_street.csv.zip", format="zip")
    shutil.copyfile(FIXTURE_DIR / "parquet" / "datapackage.json", FIXTURE_DIR / "datapackage.json")
    print(f"wrote {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
