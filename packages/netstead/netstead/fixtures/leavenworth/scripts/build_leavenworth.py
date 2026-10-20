#!/usr/bin/env python3
"""Rebuild the bundled Leavenworth, WA GMNS fixture from OpenStreetMap.

Sources a tiny drivable street network around the historic Bavarian-themed
downtown core of Leavenworth, WA, then materializes it as a small but
realistic GMNS 0.97 dataset in four storage variants:

    csv/                       - canonical
    parquet/                   - columnar
    leavenworth.duckdb         - single-file analytical DB
    leavenworth.csv.zip        - zipped CSV bundle

The four variants hold semantically identical data so that roundtrip
tests can equality-check across formats. Output is byte-identical when
re-run against an *unchanged OSM snapshot*; the determinism levers in
this script are:

  * sorting all rows by their natural primary key before writing,
  * deterministic ID assignment derived from sorted OSM osmid values,
  * stable column order taken from the GMNS schema field list,
  * fixed Parquet writer settings (statistics off, dictionary on,
    snappy compression),
  * zip member timestamps + member order pinned.

OSM is mutable, so a real re-run months later will produce diffs that
reflect upstream changes — not script bugs. Don't chase those. (Future
improvement, tracked in the README: pin a Geofabrik snapshot so the
fixture is byte-deterministic across rebuilds too.)

OpenStreetMap data is OpenStreetMap contributors and licensed under the
Open Database License (ODbL).

Usage::

    uv sync --extra dev-fixtures
    uv run python packages/netstead/netstead/fixtures/leavenworth/scripts/build_leavenworth.py

    # After a change to the vendored spec only (no OSM fetch):
    uv run python packages/netstead/netstead/fixtures/leavenworth/scripts/build_leavenworth.py --descriptor-only
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import duckdb
import osmnx as ox
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from corral.spec import load_schema
from netstead.spec import get_spec_path
from shapely.geometry import LineString

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent.parent
CSV_DIR = FIXTURE_DIR / "csv"
PARQUET_DIR = FIXTURE_DIR / "parquet"
DUCKDB_PATH = FIXTURE_DIR / "leavenworth.duckdb"
ZIP_PATH = FIXTURE_DIR / "leavenworth.csv.zip"
DATAPACKAGE_PATH = FIXTURE_DIR / "datapackage.json"
SPEC_VERSION = "0.97"

# ---------------------------------------------------------------------------
# OSM source signature - documented for reproducibility
# ---------------------------------------------------------------------------

# Resolves to OSM relation 8915833 ("Leavenworth, Chelan County, Washington"
# city boundary) via Nominatim. Using the place-polygon rather than a bbox
# means the fixture covers the whole town as OSM defines it — not just a
# bbox cap centered on the geocoded address.
OSM_PLACE = "Leavenworth, Washington, USA"
OSM_NETWORK_TYPE = "drive"

# ---------------------------------------------------------------------------
# GMNS shared_categories (mirrored from spec/0.97/shared_categories.json)
# kept here so the build script doesn't import from netstead at build time
# ---------------------------------------------------------------------------

CTRL_TYPES = {
    "no_control",
    "yield",
    "stop",
    "stop_2_way",
    "stop_4_way",
    "signal_with_RTOR",
    "signal",
}
BIKE_FACILITY = {
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
PED_FACILITY = {"unknown", "none", "shoulder", "sidewalk", "offstreet_path", "crosswalk"}
PARKING = {"unknown", "none", "parallel", "angle", "other"}

# OSM highway -> GMNS facility_type
HIGHWAY_TO_FACILITY = {
    "motorway": "freeway",
    "motorway_link": "ramp",
    "trunk": "primary",
    "trunk_link": "ramp",
    "primary": "primary",
    "primary_link": "ramp",
    "secondary": "secondary",
    "secondary_link": "ramp",
    "tertiary": "tertiary",
    "tertiary_link": "ramp",
    "residential": "residential",
    "living_street": "residential",
    "service": "service",
    "unclassified": "residential",
    "road": "residential",
}


def _coerce_str(value: object) -> str:
    """Pick a single string when OSM returns either a scalar or a list.

    For lists we sort lexicographically and take the first entry so the
    output is independent of the (potentially unstable) order that
    osmnx returns multi-tagged values in.
    """
    if isinstance(value, list):
        if not value:
            return ""
        return sorted(str(v) for v in value)[0]
    if value is None:
        return ""
    return str(value)


def _coerce_int(value: object, default: int) -> int:
    """Pick a deterministic int-coercible token; fall back to default."""
    if value is None:
        return default
    if isinstance(value, list):
        ints: list[int] = []
        for v in value:
            try:
                ints.append(int(v))
            except (TypeError, ValueError):
                continue
        if not ints:
            return default
        # Pick max — for `lanes`, the wider of two parallel-merged
        # designations is the safer modeling choice and avoids
        # order-dependence.
        return max(ints)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float | None = None) -> float | None:
    """Pick a deterministic float, parsing 'NN mph'/'NN km/h' speed strings."""
    if value is None:
        return default
    if isinstance(value, list):
        floats: list[float] = []
        for v in value:
            r = _coerce_float(v, None)
            if r is not None:
                floats.append(r)
        if not floats:
            return default
        return max(floats)
    try:
        return float(value)
    except (TypeError, ValueError):
        s = str(value).strip().lower()
        for unit, mult in (("mph", 1.609344), ("km/h", 1.0), ("kmh", 1.0)):
            if s.endswith(unit):
                try:
                    return float(s[: -len(unit)].strip()) * mult
                except ValueError:
                    return default
        return default


# ---------------------------------------------------------------------------
# OSM -> GMNS conversion
# ---------------------------------------------------------------------------


def fetch_graph() -> tuple[object, str]:
    """Fetch the OSM driving graph + return (graph, fetch_iso_timestamp).

    Uses ``ox.graph_from_place`` so the network follows the actual city
    polygon (resolved via Nominatim → Overpass) instead of a bbox or
    point buffer around the centroid.
    """
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    g = ox.graph_from_place(OSM_PLACE, network_type=OSM_NETWORK_TYPE)
    return g, fetched_at


def build_node_table(g: object) -> pd.DataFrame:
    """Build the GMNS node table with stable, sequential IDs sorted by osmid."""
    osmids = sorted(g.nodes())  # type: ignore[attr-defined]
    osmid_to_node_id = {osmid: i + 1 for i, osmid in enumerate(osmids)}

    rows: list[dict] = []
    for osmid in osmids:
        attrs = g.nodes[osmid]  # type: ignore[index]
        node_id = osmid_to_node_id[osmid]
        # Highway tag indicates OSM-tagged signal/stop on a node
        hwy = _coerce_str(attrs.get("highway"))
        if hwy == "traffic_signals":
            ctrl = "signal"
            ntype = "intersection"
        elif hwy == "stop":
            ctrl = "stop"
            ntype = "intersection"
        else:
            # Spread the remaining controls deterministically by node_id
            # so the fixture exercises multiple ctrl_type values without
            # making them up. This is a fixture; the README documents it.
            mod = node_id % 7
            if mod == 0:
                ctrl = "stop_4_way"
            elif mod == 1:
                ctrl = "stop_2_way"
            elif mod == 2:
                ctrl = "yield"
            else:
                ctrl = "no_control"
            ntype = "intersection"
        rows.append(
            {
                "node_id": node_id,
                "name": "",
                "x_coord": round(float(attrs["x"]), 7),
                "y_coord": round(float(attrs["y"]), 7),
                "node_type": ntype,
                "ctrl_type": ctrl,
                # OSM provenance — lets netstead.osm.edit produce iD/JOSM
                # deep links from validation findings even though this
                # fixture is consumed as a plain CSV directory.
                "osm_node_id": int(osmid),
            }
        )
    df = pd.DataFrame(rows).sort_values("node_id").reset_index(drop=True)
    # Sanity: every ctrl_type is in the shared_categories enum. (Bare
    # `assert` would be stripped under `python -O`; use an explicit
    # raise so the check always runs.)
    bad = set(df["ctrl_type"]) - CTRL_TYPES
    if bad:
        raise ValueError(f"non-spec ctrl_type values: {bad}")
    return df, osmid_to_node_id


def build_link_and_geometry_tables(g: object, osmid_to_node_id: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build GMNS link + geometry tables. One geometry row per link."""
    edge_rows: list[dict] = []
    geom_rows: list[dict] = []
    # Sort edges by (from_node_id, to_node_id, key) for determinism
    sorted_edges = sorted(
        g.edges(keys=True, data=True),  # type: ignore[attr-defined]
        key=lambda e: (osmid_to_node_id[e[0]], osmid_to_node_id[e[1]], e[2]),
    )
    for i, (u, v, _key, data) in enumerate(sorted_edges, start=1):
        from_node_id = osmid_to_node_id[u]
        to_node_id = osmid_to_node_id[v]
        link_id = i
        geometry_id = i

        # Geometry: prefer OSM line, else straight from->to
        if "geometry" in data and isinstance(data["geometry"], LineString):
            line = data["geometry"]
        else:
            x_u, y_u = g.nodes[u]["x"], g.nodes[u]["y"]  # type: ignore[index]
            x_v, y_v = g.nodes[v]["x"], g.nodes[v]["y"]  # type: ignore[index]
            line = LineString([(x_u, y_u), (x_v, y_v)])
        # Round coords for deterministic WKT
        rounded = LineString([(round(x, 7), round(y, 7)) for x, y in line.coords])
        geom_rows.append({"geometry_id": geometry_id, "geometry": rounded.wkt})

        name = _coerce_str(data.get("name")) or ""
        length_m = _coerce_float(data.get("length"), 0.0) or 0.0
        lanes = max(_coerce_int(data.get("lanes"), 1), 1)
        # Free speed in km/h. OSM 'maxspeed' is mph in the US.
        maxspeed = data.get("maxspeed")
        speed_kmh = _coerce_float(maxspeed, None)
        if speed_kmh is None:
            # Reasonable urban defaults by facility class
            hwy = _coerce_str(data.get("highway"))
            speed_kmh = {
                "primary": 56.0,
                "secondary": 48.0,
                "tertiary": 40.0,
                "residential": 40.0,
                "service": 25.0,
            }.get(HIGHWAY_TO_FACILITY.get(hwy, "residential"), 40.0)

        hwy = _coerce_str(data.get("highway"))
        facility_type = HIGHWAY_TO_FACILITY.get(hwy, "residential")

        # Bike facility - default 'none' (Leavenworth has shared lanes
        # downtown but OSM doesn't reliably tag them in this extract).
        bike_facility = "none"
        # Pedestrian facility - downtown sidewalks default to 'sidewalk',
        # service roads to 'none'.
        ped_facility = "none" if facility_type == "service" else "sidewalk"
        # Parking - downtown primary/secondary streets are mostly parallel
        # parking; residential mixed; service none.
        if facility_type in {"primary", "secondary", "tertiary"} or facility_type == "residential":
            parking = "parallel"
        else:
            parking = "none"

        directed = bool(data.get("oneway", False))
        # OSM 'oneway' may be a string in some cases
        if isinstance(data.get("oneway"), str):
            directed = data.get("oneway", "").lower() in {"yes", "true", "1"}

        # OSM way id — pick the smallest when osmnx returns a list for
        # merged/collapsed ways, for determinism.
        osmid = data.get("osmid")
        if isinstance(osmid, list):
            way_id = min(int(x) for x in osmid)
        else:
            try:
                way_id = int(osmid)
            except (TypeError, ValueError):
                way_id = 0

        edge_rows.append(
            {
                "link_id": link_id,
                "name": name,
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "directed": bool(directed),
                "geometry_id": geometry_id,
                "length": round(length_m, 3),
                "facility_type": facility_type,
                "free_speed": round(speed_kmh, 2),
                "lanes": int(lanes),
                "bike_facility": bike_facility,
                "ped_facility": ped_facility,
                "parking": parking,
                "allowed_uses": "auto,truck,walk,bike",
                # OSM provenance — enables iD/JOSM edit deep-links from
                # validation findings.
                "osm_way_id": way_id,
            }
        )

    link_df = pd.DataFrame(edge_rows).sort_values("link_id").reset_index(drop=True)
    geom_df = pd.DataFrame(geom_rows).sort_values("geometry_id").reset_index(drop=True)

    # Sanity-check shared_categories. (Explicit raises rather than bare
    # `assert` so the checks survive `python -O`.)
    bad_bike = set(link_df["bike_facility"]) - BIKE_FACILITY
    if bad_bike:
        raise ValueError(f"non-spec bike_facility values: {bad_bike}")
    bad_ped = set(link_df["ped_facility"]) - PED_FACILITY
    if bad_ped:
        raise ValueError(f"non-spec ped_facility values: {bad_ped}")
    bad_parking = set(link_df["parking"]) - PARKING
    if bad_parking:
        raise ValueError(f"non-spec parking values: {bad_parking}")
    return link_df, geom_df


def build_lane_table(link_df: pd.DataFrame) -> pd.DataFrame:
    """Build a per-lane row table.

    For each link we emit lane_num=1..N. We don't include shoulder/parking
    lanes; the lane table here is for travel lanes only (matches the
    GMNS lane semantics for fixtures of this size).
    """
    rows: list[dict] = []
    next_id = 1
    for _, link in link_df.iterrows():
        n = int(link["lanes"])
        for ln in range(1, n + 1):
            rows.append(
                {
                    "lane_id": next_id,
                    "link_id": int(link["link_id"]),
                    "lane_num": ln,
                    "allowed_uses": "auto,truck",
                    "width": 3.5,
                }
            )
            next_id += 1
    df = pd.DataFrame(rows).sort_values("lane_id").reset_index(drop=True)
    return df


def build_use_definition_table() -> pd.DataFrame:
    """Define the canonical small set of uses."""
    df = (
        pd.DataFrame(
            [
                {"use": "auto", "persons_per_vehicle": 1.4, "pce": 1.0, "description": "Private automobile"},
                {"use": "truck", "persons_per_vehicle": 1.0, "pce": 2.0, "description": "Heavy truck"},
                {"use": "transit", "persons_per_vehicle": 20.0, "pce": 2.0, "description": "Bus / shuttle"},
                {"use": "bike", "persons_per_vehicle": 1.0, "pce": 0.2, "description": "Bicycle"},
                {"use": "walk", "persons_per_vehicle": 1.0, "pce": 0.0, "description": "Pedestrian"},
            ]
        )
        .sort_values("use")
        .reset_index(drop=True)
    )
    return df


def build_use_group_table() -> pd.DataFrame:
    """Define handy groupings (motorized / nonmotorized)."""
    df = (
        pd.DataFrame(
            [
                {"use_group": "motorized", "uses": "auto,truck,transit", "description": "Motorized vehicles"},
                {"use_group": "nonmotorized", "uses": "bike,walk", "description": "Non-motorized users"},
            ]
        )
        .sort_values("use_group")
        .reset_index(drop=True)
    )
    return df


def build_time_set_definitions_table() -> pd.DataFrame:
    """Define one TOD window: weekday AM peak (07:00-09:00)."""
    df = pd.DataFrame(
        [
            {
                "timeday_id": "weekday_am_peak",
                "monday": True,
                "tuesday": True,
                "wednesday": True,
                "thursday": True,
                "Friday": True,
                "saturday": False,
                "sunday": False,
                "holiday": False,
                "start_time": "07:00:00",
                "end_time": "09:00:00",
            }
        ]
    )
    return df


def build_link_tod_table(link_df: pd.DataFrame) -> pd.DataFrame:
    """At least one TOD restriction.

    We pick the first downtown 'primary' or 'secondary' link by link_id.
    During the AM peak it loses parking (no_parking is represented as
    parking='none' in shared_categories).
    """
    candidates = link_df[link_df["facility_type"].isin(["primary", "secondary", "tertiary"])]
    if candidates.empty:
        candidates = link_df.head(1)
    target = candidates.iloc[0]
    df = pd.DataFrame(
        [
            {
                "link_tod_id": 1,
                "link_id": int(target["link_id"]),
                "timeday_id": "weekday_am_peak",
                "parking": "none",
                "allowed_uses": "auto,truck,transit,bike,walk",
            }
        ]
    )
    return df


def build_signal_controller_table(node_df: pd.DataFrame) -> pd.DataFrame:
    """One controller per signalized node."""
    sig_nodes = node_df[node_df["ctrl_type"] == "signal"]
    rows = [{"controller_id": int(node_id)} for node_id in sorted(sig_nodes["node_id"].tolist())]
    return pd.DataFrame(rows, columns=["controller_id"])


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _csv_text(df: pd.DataFrame) -> str:
    """Serialize a DataFrame to CSV text with a stable line ending + no index."""
    buf = StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue()


def write_csv(tables: dict[str, pd.DataFrame]) -> None:
    """Write each table to csv/<name>.csv."""
    if CSV_DIR.exists():
        shutil.rmtree(CSV_DIR)
    CSV_DIR.mkdir(parents=True)
    for name, df in tables.items():
        path = CSV_DIR / f"{name}.csv"
        path.write_text(_csv_text(df))


def write_parquet(tables: dict[str, pd.DataFrame]) -> None:
    """Write each table as parquet/<name>.parquet with deterministic settings."""
    if PARQUET_DIR.exists():
        shutil.rmtree(PARQUET_DIR)
    PARQUET_DIR.mkdir(parents=True)
    for name, df in tables.items():
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(
            table,
            PARQUET_DIR / f"{name}.parquet",
            compression="snappy",
            use_dictionary=True,
            write_statistics=False,  # stats include creation-time-varying min/max bytes
            store_schema=True,
        )


def write_duckdb(tables: dict[str, pd.DataFrame]) -> None:
    """Materialize all tables into a single duckdb file."""
    if DUCKDB_PATH.exists():
        DUCKDB_PATH.unlink()
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        for name, df in tables.items():
            con.register(f"_in_{name}", df)
            # Build-time fixture writer talks to duckdb directly (no ibis on
            # path at fixture-build time, and this is a one-shot CTAS).
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM _in_{name}")  # pragma: allow-sql
            con.unregister(f"_in_{name}")
    finally:
        con.close()


def write_zip() -> None:
    """Zip the canonical csv/ tree into leavenworth.csv.zip."""
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
    (fix: rerun this script with ``--descriptor-only``).
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_fks(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Return a list of FK errors (empty list = pass)."""
    errors: list[str] = []
    node_ids = set(tables["node"]["node_id"].tolist())
    link_ids = set(tables["link"]["link_id"].tolist())
    geom_ids = set(tables["geometry"]["geometry_id"].tolist())

    bad = set(tables["link"]["from_node_id"]) - node_ids
    if bad:
        errors.append(f"link.from_node_id values not in node: {bad}")
    bad = set(tables["link"]["to_node_id"]) - node_ids
    if bad:
        errors.append(f"link.to_node_id values not in node: {bad}")
    bad = set(tables["link"]["geometry_id"]) - geom_ids
    if bad:
        errors.append(f"link.geometry_id values not in geometry: {bad}")
    bad = set(tables["lane"]["link_id"]) - link_ids
    if bad:
        errors.append(f"lane.link_id values not in link: {bad}")
    bad = set(tables["link_tod"]["link_id"]) - link_ids
    if bad:
        errors.append(f"link_tod.link_id values not in link: {bad}")
    timeday_ids = set(tables["time_set_definitions"]["timeday_id"].tolist())
    bad = set(tables["link_tod"]["timeday_id"]) - timeday_ids
    if bad:
        errors.append(f"link_tod.timeday_id values not in time_set_definitions: {bad}")
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _bytes_to_mb(n: int) -> float:
    return round(n / (1024 * 1024), 3)


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def refresh_descriptor() -> int:
    """Rewrite datapackage.json (and the zip that embeds it) from the csv/ tree.

    No OSM fetch: use this when only the vendored spec changed.
    """
    resource_order = [
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
    names = [n for n in resource_order if (CSV_DIR / f"{n}.csv").is_file()]
    write_datapackage(names)
    write_zip()
    print(f"[leavenworth] rewrote {DATAPACKAGE_PATH.name} and {ZIP_PATH.name} for {len(names)} tables")
    return 0


def main() -> int:
    """Build all four format variants. Print a summary; return non-zero on failure."""
    if "--descriptor-only" in sys.argv[1:]:
        return refresh_descriptor()
    print(f"[leavenworth] fetching OSM: place={OSM_PLACE!r} type={OSM_NETWORK_TYPE}")
    g, fetched_at = fetch_graph()
    print(f"[leavenworth] OSM graph: {len(g.nodes)} nodes, {len(g.edges)} edges (fetched at {fetched_at})")

    node_df, osmid_to_node_id = build_node_table(g)
    link_df, geom_df = build_link_and_geometry_tables(g, osmid_to_node_id)
    lane_df = build_lane_table(link_df)
    use_def_df = build_use_definition_table()
    use_grp_df = build_use_group_table()
    tsd_df = build_time_set_definitions_table()
    link_tod_df = build_link_tod_table(link_df)
    sig_ctrl_df = build_signal_controller_table(node_df)

    # Order matters for datapackage.json resource order (deterministic)
    tables: dict[str, pd.DataFrame] = {
        "node": node_df,
        "link": link_df,
        "geometry": geom_df,
        "lane": lane_df,
        "use_definition": use_def_df,
        "use_group": use_grp_df,
        "time_set_definitions": tsd_df,
        "link_tod": link_tod_df,
    }
    if not sig_ctrl_df.empty:
        tables["signal_controller"] = sig_ctrl_df

    # FK validation before write
    fk_errors = validate_fks(tables)
    if fk_errors:
        print("[leavenworth] FK validation FAILED:")
        for e in fk_errors:
            print(f"   - {e}")
        return 2
    print("[leavenworth] FK validation: OK")

    # Write everything
    write_csv(tables)
    write_datapackage(list(tables.keys()))
    write_parquet(tables)
    write_duckdb(tables)
    write_zip()

    # Summary
    print("\n[leavenworth] row counts per table:")
    for name, df in tables.items():
        print(f"   {name:>22}  {len(df):>5} rows")

    print("\n[leavenworth] storage variants:")
    csv_bytes = _dir_size(CSV_DIR)
    pq_bytes = _dir_size(PARQUET_DIR)
    duck_bytes = DUCKDB_PATH.stat().st_size
    zip_bytes = ZIP_PATH.stat().st_size
    dp_bytes = DATAPACKAGE_PATH.stat().st_size
    total = csv_bytes + pq_bytes + duck_bytes + zip_bytes + dp_bytes
    print(f"   csv/                    {_bytes_to_mb(csv_bytes):>8} MB  ({csv_bytes:>7} bytes)")
    print(f"   parquet/                {_bytes_to_mb(pq_bytes):>8} MB  ({pq_bytes:>7} bytes)")
    print(f"   leavenworth.duckdb      {_bytes_to_mb(duck_bytes):>8} MB  ({duck_bytes:>7} bytes)")
    print(f"   leavenworth.csv.zip     {_bytes_to_mb(zip_bytes):>8} MB  ({zip_bytes:>7} bytes)")
    print(f"   datapackage.json        {_bytes_to_mb(dp_bytes):>8} MB  ({dp_bytes:>7} bytes)")
    print(f"   {'TOTAL':<22} {_bytes_to_mb(total):>9} MB  ({total:>7} bytes)")

    if total > 5 * 1024 * 1024:
        print("[leavenworth] ERROR: total > 5 MB, this is too big for a wheel-bundled fixture")
        return 3

    print(f"\n[leavenworth] done. OSM snapshot fetched at {fetched_at} (UTC).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
