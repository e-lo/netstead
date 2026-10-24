"""The example networks bundled with netstead, offered on the Open / Import wizard's first screen.

Only fixtures that ship in the wheel and are full GMNS networks are listed. They live inside the installed
package, usually outside ``io.allowed_roots``, so :func:`is_bundled_example` lets ``open_network`` read exactly
these folders (read-only; nothing else under the package is opened).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import duckdb

from netstead.fixtures import leavenworth, market_street, rdu_i40

__all__ = ["examples", "is_bundled_example"]

_RDU_DESCRIPTION = "I-40 interchanges in Research Triangle Park, NC (ramp and mainline topology, from OpenStreetMap)"


def _parquet_count(directory: Path, table: str) -> int:
    path = (directory / f"{table}.parquet").as_posix()
    with duckdb.connect() as con:
        row = con.execute("SELECT count(*) FROM read_parquet(?)", [path]).fetchone()
    return int(row[0]) if row else 0


@cache
def examples() -> tuple[dict[str, Any], ...]:
    """``{id, name, description, links, nodes, source}`` per bundled example, computed once."""
    info = leavenworth._FIXTURE_INFO
    rdu = Path(rdu_i40.__file__).parent / "parquet"
    return (
        {
            "id": "leavenworth",
            "name": "Leavenworth, WA",
            "description": str(info["description"]),
            "links": int(info["tables"]["link"]),
            "nodes": int(info["tables"]["node"]),
            "source": str(leavenworth.parquet_dir()),
        },
        {
            "id": "rdu_i40",
            "name": "RDU I-40, NC",
            "description": _RDU_DESCRIPTION,
            "links": _parquet_count(rdu, "link"),
            "nodes": _parquet_count(rdu, "node"),
            "source": str(rdu),
        },
        {
            "id": "market_street",
            "name": "Market Street, San Francisco",
            "description": str(market_street._FIXTURE_INFO["description"]),
            "links": int(market_street._FIXTURE_INFO["tables"]["link"]),
            "nodes": int(market_street._FIXTURE_INFO["tables"]["node"]),
            "source": str(market_street.parquet_dir()),
        },
    )


def is_bundled_example(path: Path) -> bool:
    """Whether resolved ``path`` is one of the listed examples' folders (exact match, not a parent or child)."""
    return any(path == Path(e["source"]).resolve() for e in examples())
