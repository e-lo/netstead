"""Vectorized S2 cell ids for lon/lat points (numpy only, no S2 library).

S2 projects the sphere onto the six faces of a cube and orders the cells on
each face along a Hilbert curve. A cell id is a 64-bit integer: 3 face bits,
then 2 bits per level of the Hilbert position, then a single trailing 1 bit
that marks the level. Two properties make them useful for routing:

* **Hierarchy is bit truncation.** :func:`parent` of a level-13 id is its
  level-12 cell, so a partition at several levels costs one leaf computation.
* **Ids sort spatially.** Nearby cells have nearby ids, and all descendants of
  a cell fall in one contiguous id range.

Only the point -> cell mapping is implemented; it follows the reference
``S2CellId::FromLatLng`` (quadratic uv->st projection) and is checked against
it in the tests.

Examples:
    >>> from netstead.graph.s2 import cell_ids, level_of
    >>> ids = cell_ids([-122.4194], [37.7749], level=13)
    >>> hex(int(ids[0]))
    '0x8085809c00000000'
    >>> int(level_of(ids)[0])
    13
"""

from __future__ import annotations

import numpy as np

__all__ = ["MAX_LEVEL", "cell_ids", "level_of", "parent"]

#: Leaf level: 30 levels of Hilbert subdivision below each cube face.
MAX_LEVEL = 30

_SWAP = 1
_INVERT = 2
# Hilbert position of quadrant (i_bit << 1 | j_bit), per curve orientation.
_IJ_TO_POS = np.array([[0, 1, 3, 2], [0, 3, 1, 2], [2, 3, 1, 0], [2, 1, 3, 0]], dtype=np.uint8)
# Orientation change after stepping into the quadrant at each Hilbert position.
_POS_TO_ORIENT = np.array([_SWAP, 0, 0, _INVERT | _SWAP], dtype=np.uint8)


def cell_ids(lon, lat, level: int = MAX_LEVEL) -> np.ndarray:
    """Return the S2 cell id (``uint64``) containing each ``(lon, lat)`` point at ``level``.

    Args:
        lon: Longitudes in degrees (array-like).
        lat: Latitudes in degrees (array-like, same length as ``lon``).
        level: S2 level, ``0`` (cube face) to ``30`` (leaf, ~1 cm).

    Raises:
        ValueError: If ``level`` is out of range or any coordinate is non-finite.
    """
    if not 0 <= level <= MAX_LEVEL:
        raise ValueError(f"S2 level must be 0..{MAX_LEVEL}, got {level}.")
    lon = np.radians(np.asarray(lon, dtype="float64"))
    lat = np.radians(np.asarray(lat, dtype="float64"))
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        raise ValueError("cell_ids needs finite coordinates; drop NaN points first.")

    xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    face, u, v = _face_uv(xyz)
    i = _st_to_ij(_uv_to_st(u))
    j = _st_to_ij(_uv_to_st(v))

    # Walk the Hilbert curve from the top level down, two bits per level.
    pos = np.zeros(len(face), dtype=np.uint64)
    orient = (face & _SWAP).astype(np.uint8)
    for k in range(1, level + 1):
        shift = MAX_LEVEL - k
        quadrant = (((i >> shift) & 1) << 1) | ((j >> shift) & 1)
        step = _IJ_TO_POS[orient, quadrant]
        pos = (pos << np.uint64(2)) | step.astype(np.uint64)
        orient ^= _POS_TO_ORIENT[step]

    tail = np.uint64(2 * (MAX_LEVEL - level))
    return (face.astype(np.uint64) << np.uint64(61)) | (pos << (tail + np.uint64(1))) | (np.uint64(1) << tail)


def parent(ids, level: int) -> np.ndarray:
    """Return the ancestor of each cell id at ``level`` (must be <= each id's own level)."""
    ids = np.asarray(ids, dtype=np.uint64)
    lsb = np.uint64(1) << np.uint64(2 * (MAX_LEVEL - level))
    return (ids & ~(lsb - np.uint64(1)) & ~lsb) | lsb


def level_of(ids) -> np.ndarray:
    """Return the level of each cell id (from the position of its trailing 1 bit)."""
    ids = np.asarray(ids, dtype=np.uint64)
    lowest = ids & (~ids + np.uint64(1))
    return MAX_LEVEL - (np.log2(lowest.astype("float64")).astype(np.int64) // 2)


def _face_uv(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cube face (0..5) of each unit vector, and its (u, v) coordinates on that face."""
    x, y, z = xyz
    axis = np.argmax(np.abs(xyz), axis=0)
    negative = xyz[axis, np.arange(xyz.shape[1])] < 0
    face = axis + 3 * negative
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.select(
            [face == 0, face == 1, face == 2, face == 3, face == 4],
            [y / x, -x / y, -x / z, z / x, z / y],
            default=-y / z,
        )
        v = np.select(
            [face == 0, face == 1, face == 2, face == 3, face == 4],
            [z / x, z / y, -y / z, y / x, -x / y],
            default=-x / z,
        )
    return face, u, v


def _uv_to_st(u: np.ndarray) -> np.ndarray:
    """Quadratic projection from face coordinates to [0, 1] cell space."""
    root = 0.5 * np.sqrt(1 + 3 * np.abs(u))
    return np.where(u >= 0, root, 1 - root)


def _st_to_ij(s: np.ndarray) -> np.ndarray:
    """Leaf-cell integer coordinate (0 .. 2**30 - 1) for an st value."""
    return np.clip(np.floor(s * (1 << MAX_LEVEL)), 0, (1 << MAX_LEVEL) - 1).astype(np.int64)
