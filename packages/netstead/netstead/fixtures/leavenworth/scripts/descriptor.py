#!/usr/bin/env python3
"""Write the Leavenworth fixture's ``datapackage.json`` and the zip that embeds it.

Split out of ``build_leavenworth.py`` so a change to the vendored GMNS spec
can be picked up without the ``dev-fixtures`` extra (osmnx, shapely) or an
OpenStreetMap fetch: it reads only the existing ``csv/`` tree and the spec.
``build_leavenworth.py`` imports these helpers for the full rebuild.

Usage::

    uv run --frozen python packages/netstead/netstead/fixtures/leavenworth/scripts/descriptor.py
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from corral.spec import load_schema
from netstead.spec import get_spec_path

FIXTURE_DIR = Path(__file__).resolve().parent.parent
CSV_DIR = FIXTURE_DIR / "csv"
ZIP_PATH = FIXTURE_DIR / "leavenworth.csv.zip"
DATAPACKAGE_PATH = FIXTURE_DIR / "datapackage.json"
SPEC_VERSION = "0.97"

# Resource order in datapackage.json (deterministic); also the order the
# full build assembles its tables in. signal_controller is written only
# when the OSM extract has signals.
TABLE_ORDER = [
    "node",
    "link",
    "geometry",
    "lane",
    "use_definition",
    "use_group",
    "time_set_definitions",
    "link_tod",
    "signal_controller",
]


def write_zip() -> None:
    """Zip the canonical csv/ tree, plus datapackage.json, into leavenworth.csv.zip."""
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    members = sorted(CSV_DIR.glob("*.csv"))
    # ZipFile honours fixed dates so we set ZipInfo manually for byte-identity
    with zipfile.ZipFile(ZIP_PATH, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            info = zipfile.ZipInfo(filename=member.name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, member.read_bytes())
    # Also include datapackage.json for self-contained zip use
    with zipfile.ZipFile(ZIP_PATH, mode="a", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(filename="datapackage.json", date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, DATAPACKAGE_PATH.read_bytes())


def write_datapackage(table_names: list[str]) -> None:
    """Write a self-contained Frictionless datapackage.json for the csv/ tree.

    Each resource carries its GMNS 0.97 schema inline, resolved from the
    vendored spec (``$ref``s and shared categories expanded), rather than a
    relative path into ``netstead/spec``. The descriptor therefore loads
    wherever it is copied, including from inside the zip, and matches the
    self-describing descriptors corral writes for directory packages.
    ``tests/fixtures/test_leavenworth.py`` checks each inlined schema
    against the vendored one, so a spec change shows up as a test failure
    (fix: rerun this script).
    """
    spec_dir = get_spec_path(SPEC_VERSION)
    resources = []
    for name in table_names:
        schema = load_schema(spec_dir / f"{name}.schema.json")
        resources.append(
            {
                "name": name,
                "path": f"csv/{name}.csv",
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "schema": schema.model_dump(mode="json", by_alias=True, exclude_none=True),
            }
        )
    package = {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": "leavenworth-wa",
        "title": "Leavenworth, WA - example GMNS network",
        "version": "0.1.0",
        "spec": {"name": "gmns", "version": SPEC_VERSION},
        "description": (
            "Tiny example General Modeling Network Specification (GMNS) network "
            "synthesized from OpenStreetMap for the historic downtown core of "
            "Leavenworth, Washington. Bundled with netstead for tests and the "
            "5-minute getting-started example."
        ),
        "homepage": "https://github.com/e-lo/netstead",
        "licenses": [
            {
                "name": "ODbL-1.0",
                "title": "Open Database License",
                "path": "https://opendatacommons.org/licenses/odbl/1-0/",
            }
        ],
        "sources": [
            {
                "title": "OpenStreetMap",
                "path": "https://www.openstreetmap.org/",
            }
        ],
        "resources": resources,
    }
    DATAPACKAGE_PATH.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n")


def refresh_descriptor() -> int:
    """Rewrite datapackage.json and the zip from the existing csv/ tree. No OSM fetch."""
    names = [n for n in TABLE_ORDER if (CSV_DIR / f"{n}.csv").is_file()]
    write_datapackage(names)
    write_zip()
    print(f"[leavenworth] rewrote {DATAPACKAGE_PATH.name} and {ZIP_PATH.name} for {len(names)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(refresh_descriptor())
