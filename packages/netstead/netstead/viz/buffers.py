"""Parquet link/node frames -> packed binary typed-arrays for deck.gl.

No GeoJSON. Link geometry (WKT or WKB) is parsed **once here** into a flat
coordinate buffer in deck.gl `PathLayer` binary layout. A link's shape comes from
its inline ``geometry`` cell, else from its ``geometry_id`` row in the optional
``geometry`` table (the GMNS order ``netstead.semantics.geometry`` documents),
else a straight from->to segment. The browser feeds these typed arrays straight
to the GPU.

Wire format (`pack_network`): ``[uint32 headerLen][header JSON][payload]`` where
the header gives per-array byte lengths and the payload concatenates, in order:
``linkPositions f32 (flat lon,lat)``, ``linkStartIndices u32 (nLinks+1)``,
``linkIds f64``, ``nodePositions f32``, ``nodeIds f64``.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pandas as pd

from netstead._wkt import linestring_points

__all__ = ["network_attrs", "pack_network", "unpack_network"]

#: Fallback lane count by facility_type when `lanes` is untagged, so freeways
#: still render thick. Ramps/links stay thin.
_LANES_DEFAULT = {"motorway": 4, "trunk": 3, "primary": 3, "secondary": 2, "tertiary": 2}


def _effective_lanes(links) -> np.ndarray:
    """Per-link lane count for width styling.

    `lanes` if tagged (>0), else a facility_type default (ramps=1). Clamped to 1..255 as uint8.
    """
    lanes = (
        pd.to_numeric(links.get("lanes"), errors="coerce")
        if "lanes" in links.columns
        else pd.Series([np.nan] * len(links))
    )
    ft = links["facility_type"].astype(str) if "facility_type" in links.columns else pd.Series([""] * len(links))
    default = ft.map(lambda f: 1 if f.endswith("_link") else _LANES_DEFAULT.get(f, 1))
    eff = lanes.fillna(0).astype(int)
    eff = eff.where(eff > 0, default)
    return eff.clip(1, 255).to_numpy(dtype="<u1")


def _node_coords(nodes):
    return (
        dict(zip(nodes["node_id"], nodes["x_coord"], strict=False)),
        dict(zip(nodes["node_id"], nodes["y_coord"], strict=False)),
    )


def _resolve_shapes(links, geometry) -> list:
    """One geometry cell (WKT/WKB or ``None``) per link row, in ``links`` order.

    An inline ``links.geometry`` cell wins when it parses; otherwise the link's
    ``geometry_id`` is looked up in the ``geometry`` frame (``geometry_id``,
    ``geometry``). ``None`` where neither resolves, so the caller falls back to
    a straight from/to segment.
    """
    n = len(links)
    inline = links["geometry"].tolist() if "geometry" in links.columns else [None] * n
    if geometry is None or "geometry_id" not in links.columns:
        return inline
    by_id = dict(zip(geometry["geometry_id"].tolist(), geometry["geometry"].tolist(), strict=True))
    ids = links["geometry_id"].tolist()
    return [g if len(linestring_points(g)) >= 2 else by_id.get(gid) for g, gid in zip(inline, ids, strict=True)]


def _link_paths(links, nx: dict, ny: dict, geometry=None):
    """Flat positions + startIndices + ids for every link (PathLayer binary).

    Column arrays + index loop (no per-row Series) so it scales; the WKT parse
    is the only inherent per-link cost, and straight from/to is the fallback.
    """
    geoms = _resolve_shapes(links, geometry)
    fn = links["from_node_id"].to_numpy()
    tn = links["to_node_id"].to_numpy()
    positions: list[float] = []
    start_indices: list[int] = [0]
    for i in range(len(links)):
        g = geoms[i]
        pts = linestring_points(g)
        if len(pts) >= 2:
            for x, y in pts:
                positions.append(x)
                positions.append(y)
        else:
            u, v = fn[i], tn[i]
            if u in nx and v in nx:
                positions.extend((float(nx[u]), float(ny[u]), float(nx[v]), float(ny[v])))
        start_indices.append(len(positions) // 2)
    return (
        np.asarray(positions, dtype="<f4"),
        np.asarray(start_indices, dtype="<u4"),
        links["link_id"].to_numpy().astype("<f8"),
    )


def _node_points(nodes):
    pos = np.empty(len(nodes) * 2, dtype="<f4")
    pos[0::2] = nodes["x_coord"].to_numpy(dtype="f4")
    pos[1::2] = nodes["y_coord"].to_numpy(dtype="f4")
    ids = nodes["node_id"].to_numpy(dtype="f8")
    return pos, ids


def pack_network(links, nodes, geometry=None) -> bytes:
    """Pack link paths + node points into the binary wire format.

    ``geometry`` is the optional GMNS ``geometry`` table (``geometry_id``,
    ``geometry``) that shapes links whose ``geometry_id`` points into it and
    which carry no inline ``geometry`` of their own.
    """
    nx, ny = _node_coords(nodes)
    lpos, lstart, lids = _link_paths(links, nx, ny, geometry)
    llanes = _effective_lanes(links)
    npos, nids = _node_points(nodes)

    header = {
        "links": {
            "count": len(links),
            "positionsBytes": int(lpos.nbytes),
            "startIndicesBytes": int(lstart.nbytes),
            "idsBytes": int(lids.nbytes),
            "lanesBytes": int(llanes.nbytes),
        },
        "nodes": {"count": len(nodes), "positionsBytes": int(npos.nbytes), "idsBytes": int(nids.nbytes)},
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload = b"".join(
        [lpos.tobytes(), lstart.tobytes(), lids.tobytes(), llanes.tobytes(), npos.tobytes(), nids.tobytes()]
    )
    return struct.pack("<I", len(header_bytes)) + header_bytes + payload


def unpack_network(blob: bytes) -> dict:
    """Inverse of :func:`pack_network` (for tests / debugging)."""
    (header_len,) = struct.unpack_from("<I", blob, 0)
    start = 4 + header_len
    header = json.loads(blob[4:start].decode("utf-8"))
    off = start
    lk, nd = header["links"], header["nodes"]

    def take(n_bytes, dtype):
        nonlocal off
        arr = np.frombuffer(blob, dtype=dtype, count=n_bytes // np.dtype(dtype).itemsize, offset=off)
        off += n_bytes
        return arr

    lpos = take(lk["positionsBytes"], "<f4")
    lstart = take(lk["startIndicesBytes"], "<u4")
    lids = take(lk["idsBytes"], "<f8")
    llanes = take(lk["lanesBytes"], "<u1")
    npos = take(nd["positionsBytes"], "<f4")
    nids = take(nd["idsBytes"], "<f8")
    return {
        "links": {
            "count": lk["count"],
            "positions": lpos.tolist(),
            "startIndices": lstart.tolist(),
            "ids": [int(i) for i in lids.tolist()],
            "lanes": [int(x) for x in llanes.tolist()],
        },
        "nodes": {"count": nd["count"], "positions": npos.tolist(), "ids": [int(i) for i in nids.tolist()]},
    }


def _col(links, name):
    if name not in links.columns:
        return [None] * len(links)
    return [None if pd_isna(v) else (v.item() if hasattr(v, "item") else v) for v in links[name]]


def pd_isna(v) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return v is None


def network_attrs(links) -> dict:
    """Index-aligned attribute arrays for the hover tooltip (not per-feature objects)."""
    return {
        "link_id": [int(i) if hasattr(i, "item") else i for i in links["link_id"]],
        "name": _col(links, "name"),
        "ref": _col(links, "ref"),
        "facility_type": _col(links, "facility_type"),
    }
