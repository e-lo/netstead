"""Build the tiny Overture-shaped GeoParquet fixture used by the overture tests.

Writes ``segment.parquet`` + ``connector.parquet`` next to this script, shaped
like a trimmed slice of the Overture **transportation** theme: a top-level
``bbox`` struct, WKB geometry, and the nested property columns the reader /
converter touch (``names``, ``speed_limits``, ``routes``,
``access_restrictions``, ``connectors``). It is NOT real Overture data — it is a
hand-built miniature so the pipeline can be exercised offline (no S3) in CI.

Run with the project's dev-fixture deps (shapely + pyarrow)::

    .venv/bin/python packages/netstead/tests/fixtures/overture/build_fixture.py

The generated parquet is committed; this script exists so the fixture is
reproducible, mirroring the Leavenworth fixture's build script.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely.wkb as swkb
from shapely.geometry import LineString, Point

HERE = Path(__file__).resolve().parent

# Connector points (lon, lat), near (0, 0) so geodesic lengths are easy to reason
# about (~111 km / degree of latitude).
CONNECTORS: dict[str, tuple[float, float]] = {
    "ca": (0.000, 0.000),
    "cb": (0.000, 0.001),
    "cc": (0.000, 0.002),
    "cd": (0.001, 0.002),
    "ce": (0.002, 0.002),
}

# Segments: (id, subtype, class, names.primary, geometry coords, connectors,
#            speed_limits, routes, access_restrictions)
SEGMENTS = [
    {
        "id": "s_main",
        "subtype": "road",
        "class": "residential",
        "names": {"primary": "Main St"},
        "coords": [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)],
        # interior connector cb at at=0.5 -> splits into two sub-segments
        "connectors": [
            {"connector_id": "ca", "at": 0.0},
            {"connector_id": "cb", "at": 0.5},
            {"connector_id": "cc", "at": 1.0},
        ],
        "speed_limits": [{"max_speed": {"value": 25, "unit": "mph"}}],
        "routes": None,
        "access_restrictions": None,
    },
    {
        "id": "s_hwy",
        "subtype": "road",
        "class": "primary",
        "names": {"primary": "Grand Ave"},
        "coords": [(0.0, 0.002), (0.001, 0.002)],
        "connectors": [
            {"connector_id": "cc", "at": 0.0},
            {"connector_id": "cd", "at": 1.0},
        ],
        # km/h unit -> normalized to mph by the reader's transform
        "speed_limits": [{"max_speed": {"value": 100, "unit": "km/h"}}],
        "routes": [{"ref": "US 1", "network": "US"}],
        # one-way forward: travelling backward is denied
        "access_restrictions": [{"access_type": "denied", "when": {"heading": "backward"}}],
    },
    {
        "id": "s_foot",
        "subtype": "road",
        "class": "footway",
        "names": {"primary": "River Path"},
        "coords": [(0.001, 0.002), (0.002, 0.002)],
        "connectors": [
            {"connector_id": "cd", "at": 0.0},
            {"connector_id": "ce", "at": 1.0},
        ],
        "speed_limits": None,
        "routes": None,
        "access_restrictions": None,
    },
]


def _bbox(xs: list[float], ys: list[float]) -> dict[str, float]:
    return {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys)}


def _write_segments(path: Path) -> None:
    rows = []
    for seg in SEGMENTS:
        xs = [c[0] for c in seg["coords"]]
        ys = [c[1] for c in seg["coords"]]
        rows.append(
            {
                "id": seg["id"],
                "subtype": seg["subtype"],
                "class": seg["class"],
                "names": seg["names"],
                "speed_limits": seg["speed_limits"],
                "routes": seg["routes"],
                "access_restrictions": seg["access_restrictions"],
                "connectors": seg["connectors"],
                "bbox": _bbox(xs, ys),
                "geometry": swkb.dumps(LineString(seg["coords"])),
            }
        )
    pq.write_table(pa.Table.from_pylist(rows), path)


def _write_connectors(path: Path) -> None:
    rows = [
        {
            "id": cid,
            "bbox": _bbox([lon], [lat]),
            "geometry": swkb.dumps(Point(lon, lat)),
        }
        for cid, (lon, lat) in CONNECTORS.items()
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)


def main() -> None:
    """Write the segment + connector parquet fixtures next to this script."""
    _write_segments(HERE / "segment.parquet")
    _write_connectors(HERE / "connector.parquet")
    print(f"wrote {HERE / 'segment.parquet'} and {HERE / 'connector.parquet'}")


if __name__ == "__main__":
    main()
