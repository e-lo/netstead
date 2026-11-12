#!/usr/bin/env python3
"""Add the ``ref`` (route number) column to the bundled Leavenworth ``link`` table, without an OSM rebuild.

The fixture was built before OSM's ``ref`` tag was kept, so US 2 through town is 10 ``primary`` links of which 8
have no ``name`` (their OSM ways carry only ``ref=US 2``). ``build_leavenworth.py`` now keeps ``ref``; a full rebuild
needs network access and renumbers ids, so this one-time patch adds the column to the existing data instead.

The way -> ref table below is what the OpenStreetMap API (``/api/0.6/ways?ways=...``) returned on 2026-11-12 for every
distinct ``osm_way_id`` of the fixture's links: three of the 89 ways carry a ``ref``, all of them ``US 2``. The patch
is deterministic and offline (it reads only the existing files and this table); it is idempotent.

It rewrites ``csv/link.csv``, ``parquet/link.parquet``, the ``link`` table of ``leavenworth.duckdb`` and the zip
(``datapackage.json`` does not change: ``osm_way_id``, also an extra field, is not declared there either).

Usage::

    uv run --all-extras python packages/netstead/netstead/fixtures/leavenworth/scripts/add_route_refs.py
"""

from __future__ import annotations

import csv
import io
import sys

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from descriptor import CSV_DIR, FIXTURE_DIR, write_zip

PARQUET_PATH = FIXTURE_DIR / "parquet" / "link.parquet"
DUCKDB_PATH = FIXTURE_DIR / "leavenworth.duckdb"

# OSM ways of the fixture that carry a ``ref`` tag (OSM API, 2026-11-12); every other way has none.
WAY_REFS: dict[int, str] = {5846044: "US 2", 5851752: "US 2", 60696381: "US 2"}


def patch_csv() -> list[str]:
    """Append a ``ref`` column to ``csv/link.csv``; return the refs in row order."""
    path = CSV_DIR / "link.csv"
    rows = list(csv.reader(io.StringIO(path.read_text())))
    header, body = rows[0], rows[1:]
    if "ref" in header:
        return [row[header.index("ref")] for row in body]
    way = header.index("osm_way_id")
    refs = [WAY_REFS.get(int(row[way]), "") for row in body]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*header, "ref"])
    writer.writerows([*row, ref] for row, ref in zip(body, refs, strict=True))
    path.write_text(out.getvalue())
    return refs


def patch_parquet(refs: list[str]) -> None:
    """Append ``ref`` to ``parquet/link.parquet`` with the build script's writer settings."""
    df = pq.read_table(PARQUET_PATH).to_pandas()
    if "ref" in df.columns:
        return
    df["ref"] = refs
    pq.write_table(
        pa.Table.from_pandas(df, preserve_index=False),
        PARQUET_PATH,
        compression="snappy",
        use_dictionary=True,
        write_statistics=False,
        store_schema=True,
    )


def patch_duckdb(refs: list[str]) -> None:
    """Append ``ref`` to the ``link`` table (rows are in ``link_id`` order, as in the csv)."""
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        columns = [r[0] for r in con.execute("DESCRIBE link").fetchall()]  # pragma: allow-sql
        if "ref" in columns:
            return
        df = con.execute("SELECT * FROM link ORDER BY link_id").df()  # pragma: allow-sql
        df["ref"] = refs
        con.register("_in_link", df)
        con.execute("CREATE OR REPLACE TABLE link AS SELECT * FROM _in_link")  # pragma: allow-sql
        con.unregister("_in_link")
    finally:
        con.close()
    _compact()


def _compact() -> None:
    """Rewrite the database file: replacing a table leaves its old blocks behind, which would grow the fixture."""
    fresh = DUCKDB_PATH.with_name("leavenworth.compact.duckdb")
    fresh.unlink(missing_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{DUCKDB_PATH}' AS old")  # pragma: allow-sql
        con.execute(f"ATTACH '{fresh}' AS fresh")  # pragma: allow-sql
        con.execute("COPY FROM DATABASE old TO fresh")  # pragma: allow-sql
    finally:
        con.close()
    fresh.replace(DUCKDB_PATH)


def main() -> int:
    """Patch every form of the fixture and say what changed."""
    refs = patch_csv()
    patch_parquet(refs)
    patch_duckdb(refs)
    write_zip()
    print(
        f"[leavenworth] ref set on {sum(bool(r) for r in refs)} of {len(refs)} links; csv, parquet, duckdb, zip updated"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
