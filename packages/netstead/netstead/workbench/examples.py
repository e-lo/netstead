"""The example networks bundled with netstead, offered on the Open / Import wizard's first screen.

Only fixtures that ship in the wheel and are full GMNS networks are listed. They live inside the installed
package, usually outside ``io.allowed_roots``, so :func:`is_bundled_example` lets ``open_network`` read exactly
these folders (read-only; nothing else under the package is opened).

The bundled sample ProjectCard catalogs (:func:`sample_catalogs`) are treated the same way: :func:`in_sample_catalog`
lets a plugin read the catalog folder and the files directly in it, though never write there.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from netstead.fixtures import leavenworth, market_street, rdu_i40

__all__ = ["SAMPLE_CATALOG_READ_ONLY", "examples", "in_sample_catalog", "is_bundled_example", "sample_catalogs"]

_RDU_DESCRIPTION = "I-40 interchanges in Research Triangle Park, NC (ramp and mainline topology, from OpenStreetMap)"


def _parquet_count(directory: Path, table: str) -> int:
    return pq.ParquetFile(directory / f"{table}.parquet").metadata.num_rows  # the footer: no rows read


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


#: Why a bundled sample catalog refuses every write (the Host's ``writable`` raises it).
SAMPLE_CATALOG_READ_ONLY = (
    "This is a bundled sample catalog and cannot be changed here; copy its folder to a folder of your own "
    "(inside io.allowed_roots), open that, and edit the copy"
)


@cache
def sample_catalogs() -> tuple[dict[str, Any], ...]:
    """``{id, name, path, network, cards}`` per bundled sample ProjectCard catalog, computed once.

    ``network`` is the id of the example network the cards were written against (see :func:`examples`).
    """
    folder = leavenworth.cards_dir()
    return (
        {
            "id": "leavenworth",
            "name": "Sample cards (Leavenworth)",
            "path": str(folder),
            "network": "leavenworth",
            "cards": sum(1 for p in folder.iterdir() if p.suffix == ".yml"),
        },
    )


def in_sample_catalog(path: Path, *, whole_tree: bool = False) -> bool:
    """Whether resolved ``path`` is a sample catalog's folder or a file directly in it (all below: ``whole_tree``)."""
    for sample in sample_catalogs():
        folder = Path(sample["path"]).resolve()
        if path == folder or path.parent == folder or (whole_tree and path.is_relative_to(folder)):
            return True
    return False
