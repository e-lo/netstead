"""Property test: the grid self-join in ``DuplicateNearNodesRule`` finds exactly the pairs a pairwise scan finds.

The join hashes nodes into epsilon-sized cells and compares each node only with the 3x3 block around its cell, so
it is only correct if no pair within epsilon ever lands two cells apart. That is most at risk where rounding is
worst: nodes placed on exact multiples of epsilon (cell edges), nudged by tiny jitter, far from the origin and on
the negative side of it. This test generates exactly those layouts, seeded, and compares against the O(n²) scan
the rule used before issue #181.
"""

from __future__ import annotations

import random

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from netstead.quality.rules import _near_pairs

ORIGINS = ((-5e6, -5e6), (-122.4, 47.6), (1e7, -1e7), (0.0, 0.0))
EPSILONS = (1e-5, 0.5, 5.0, 0.0)


def _brute_force(rows: list[dict], epsilon: float) -> list[tuple[int, int]]:
    """Every (i, j), i < j, with squared distance <= epsilon², computed as the pre-#181 rule did."""
    pts = [
        (i, float(r["x_coord"]), float(r["y_coord"]))
        for i, r in enumerate(rows)
        if r["node_id"] is not None and r["x_coord"] is not None and r["y_coord"] is not None
    ]
    eps_sq = epsilon * epsilon
    found = []
    for a in range(len(pts)):
        i, xi, yi = pts[a]
        for b in range(a + 1, len(pts)):
            j, xj, yj = pts[b]
            dx, dy = xi - xj, yi - yj
            if dx * dx + dy * dy <= eps_sq:
                found.append((i, j))
    return found


def _layout(rng: random.Random, origin: tuple[float, float], epsilon: float, n: int) -> list[dict]:
    """Nodes on a small lattice of epsilon multiples around ``origin``, jittered by nothing, an ulp, or a hair."""
    step = epsilon if epsilon > 0 else 1e-6
    rows = []
    for k in range(n):
        jitter = rng.choice((0.0, 0.0, 1e-12, -1e-12, step * 1e-9, -step * 1e-9, step * 0.5))
        x = origin[0] + rng.randint(-4, 4) * step + jitter
        y = origin[1] + rng.randint(-4, 4) * step + rng.choice((0.0, jitter))
        rows.append({"node_id": k + 1, "x_coord": x, "y_coord": y})
    rows.append({"node_id": n + 1, "x_coord": None, "y_coord": origin[1]})  # skipped like the scan skips it
    return rows


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("epsilon", EPSILONS)
def test_grid_join_matches_pairwise_scan(origin: tuple[float, float], epsilon: float) -> None:
    rng = random.Random(f"{origin}-{epsilon}")
    engine = IbisEngine()
    total = 0
    for _ in range(5):
        rows = _layout(rng, origin, epsilon, n=60)
        table = Table(name="node", expr=engine.from_records(rows), engine=engine)
        got = [(r["row_i"], r["row_j"]) for r in _near_pairs(table, epsilon).to_pyarrow().to_pylist()]
        expected = _brute_force(rows, epsilon)
        assert got == expected
        total += len(expected)
    assert total > 0, "the layout should produce pairs, or the comparison proves nothing"
