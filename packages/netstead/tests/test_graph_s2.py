"""Tests for netstead.graph.s2 — vectorized S2 cell ids."""

import numpy as np
import pytest

pytest.importorskip("scipy")  # netstead.graph guards on the [graph] extra

from netstead.graph.s2 import MAX_LEVEL, cell_ids, level_of, parent

# Reference ids from s2sphere 0.2.5 (CellId.from_lat_lng(...).parent(level).id()).
REFERENCE = [
    # (lon, lat, level 12, level 13, leaf)
    (-122.4194, 37.7749, 0x8085809000000000, 0x8085809C00000000, 0x8085809E8E8D8C61),
    (-78.6382, 35.7796, 0x89AC5F7000000000, 0x89AC5F6C00000000, 0x89AC5F6DB90CF59F),
    (2.3522, 48.8566, 0x47E66E1000000000, 0x47E66E1C00000000, 0x47E66E1D8F8BE23B),
    (151.2093, -33.8688, 0x6B12AE3000000000, 0x6B12AE3C00000000, 0x6B12AE3FF6290055),
    (139.6917, 35.6895, 0x60188CD000000000, 0x60188CD400000000, 0x60188CD4C86839B9),
]


@pytest.mark.parametrize(("column", "level"), [(2, 12), (3, 13), (4, MAX_LEVEL)])
def test_cell_ids_match_reference(column, level):
    lon = [row[0] for row in REFERENCE]
    lat = [row[1] for row in REFERENCE]
    expected = [row[column] for row in REFERENCE]
    assert [int(i) for i in cell_ids(lon, lat, level)] == expected


def test_face_cells_cover_each_cube_face():
    # Points on each cube face's axis land in faces 0..5 (id = face << 61 | 1 << 60).
    lon = [0, 90, 0, 180, -90, 0]
    lat = [0, 0, 90, 0, 0, -90]
    assert [int(i) for i in cell_ids(lon, lat, 0)] == [(f << 61) | (1 << 60) for f in range(6)]


def test_parent_of_leaf_equals_direct_level():
    rng = np.random.default_rng(0)
    lon, lat = rng.uniform(-180, 180, 500), rng.uniform(-89, 89, 500)
    leaf = cell_ids(lon, lat)
    for level in (0, 9, 13, 29):
        assert (parent(leaf, level) == cell_ids(lon, lat, level)).all()


def test_level_of_reads_trailing_bit():
    leaf = cell_ids([10.0, -45.0], [20.0, 60.0])
    assert level_of(leaf).tolist() == [30, 30]
    assert level_of(parent(leaf, 7)).tolist() == [7, 7]


def test_rejects_bad_level_and_missing_coordinates():
    with pytest.raises(ValueError, match="level"):
        cell_ids([0.0], [0.0], level=31)
    with pytest.raises(ValueError, match="finite"):
        cell_ids([np.nan], [0.0])
