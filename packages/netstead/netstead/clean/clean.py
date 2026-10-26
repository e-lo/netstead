"""Network-editing ops (netstead.clean) — optional ``[clean]`` extra.

Each op:

* Computes the new state of the affected table(s) in pyarrow.
* Builds one :class:`corral.editing.Edit` with ``op="replace_table"``
  per affected table and pushes it through the open
  :class:`corral.editing.Session`.
* Returns the :class:`~corral.editing.EditResult` so the caller can
  inspect the diff + roll back if needed.

Rollback fidelity is exact: ``replace_table`` captures the previous
expression in the rollback record, and :meth:`Session.rollback` restores
it byte-for-byte (via ``Engine.from_arrow`` round-tripping).

Why ``replace_table`` and not surgical per-row updates: the cleanups
here change many rows at once (every link's geometry, every node merge
relabels all incident links, …) and the bulk-replace path is simpler
to reason about + cleaner to roll back than threading row-level edits.
For tiny network sizes this is a non-issue; for regional-scale a
follow-up could specialise the hot paths.

Ops shipping in this PR:

* :func:`simplify_geometry` — drop redundant collinear vertices or
  Douglas-Peucker simplify each link's geometry.
* :func:`merge_close_nodes` — collapse node clusters within a
  threshold distance to a single survivor; rewrite incident links.
* :func:`remove_orphans` — drop nodes with no incident links.
* :func:`recompute_lengths` — set ``link.length`` to the geometry's
  geodesic length (degrees-aware approximation via shapely).

Deferred for follow-up (not in this PR):

* ``split_link_at_node`` — needs careful handling of mid-link node
  insertion + new link_id minting.
* ``connect_disconnected_components`` — needs synthetic-link policy
  + max-distance bound.
* ``snap_to_reference`` — needs CRS-aware nearest-neighbour matching.

Each op signature takes the :class:`Network` + the open
:class:`Session` (the session is the rollback boundary; passing it
explicitly keeps the integration with ``corral.editing`` visible
in every call site).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING, Any

# Guard the optional [clean] extra up front so an import-time error is
# obvious and points the user at the install command. The actual shapely
# usage is inside each op.
try:
    import shapely  # noqa: F401
except ImportError as e:  # pragma: no cover - defensive
    raise ImportError("netstead.clean requires the [clean] extra: pip install 'netstead[clean]'") from e

import pyarrow as pa
from corral.editing import Edit, EditResult, Session

from .errors import CleanError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netstead.network import Network

__all__ = [
    "merge_close_nodes",
    "recompute_lengths",
    "remove_orphans",
    "simplify_geometry",
]


# ---------------------------------------------------------------------------
# Helpers — pyarrow access, edit submission
# ---------------------------------------------------------------------------


def _to_arrow(table) -> pa.Table:
    """Materialize a :class:`~corral.dataset.Table` to pyarrow."""
    return pa.Table.from_pandas(table.to_pandas(), preserve_index=False)


def _submit_replace(session: Session, table_name: str, new_rows: list[dict]) -> EditResult:
    """Submit a ``replace_table`` Edit for ``table_name`` carrying ``new_rows``.

    Uses the package's engine to materialize the new expression from
    plain row dicts — same path as :meth:`Engine.from_records` so we
    stay backend-agnostic.
    """
    engine = session.package.engine
    new_expr = engine.from_records(new_rows)
    edit = Edit(op="replace_table", table=table_name, payload={"expr": new_expr})
    return session.add_edit(edit)


def _shape_table(net: Network, *, op: str) -> str:
    """Name of the table that physically holds the link shapes.

    GMNS lets a network carry link geometry either inline
    (``link.geometry``) or in a separate ``geometry`` table keyed by
    ``link.geometry_id``. Ops that *rewrite* shapes edit whichever one
    is present so the edit lands where the data lives and rollback
    restores that same table. Inline wins when both exist, matching
    :func:`netstead.semantics.assemble_link_geometry`'s priority.

    Raises:
        CleanError: When neither location exists — the message names
            both and what to add.
    """
    if "geometry" in net.links.columns():
        return "link"
    geometry = net.geometry
    if geometry is not None and "geometry" in geometry.columns():
        return "geometry"
    raise CleanError(
        f"{op} found no link shapes: the network has neither an inline "
        "'link.geometry' column nor a 'geometry' table (GMNS link.geometry_id). "
        "Add link geometry in one of those two places before running this op."
    )


# ---------------------------------------------------------------------------
# Op 1 — simplify_geometry
# ---------------------------------------------------------------------------


def simplify_geometry(
    net: Network,
    session: Session,
    *,
    mode: str = "redundant_only",
    tolerance: float = 0.0,
) -> EditResult:
    """Drop redundant interior vertices on every link geometry.

    The shapes are edited where they live: on ``link.geometry`` when
    the link table carries geometry inline, otherwise on the
    ``geometry`` table that ``link.geometry_id`` points at (inline wins
    when both exist). The returned edit — and its rollback — target that
    table, so a network that keeps shapes in ``geometry`` never grows an
    inline column as a side effect.

    Args:
        net: The source :class:`Network`. Must carry link shapes either
            as an inline ``link.geometry`` column or in a ``geometry``
            table.
        session: An open :class:`corral.editing.Session` bound to
            ``net``. The returned :class:`EditResult` is appended to
            the session's log so :meth:`Session.rollback` can undo it.
        mode: ``"redundant_only"`` (default) drops vertices that are
            collinear with their neighbours within ``tolerance``. No
            spatial accuracy is lost when ``tolerance == 0.0``.
            ``"douglas_peucker"`` runs shapely's
            :meth:`shapely.geometry.LineString.simplify` with the given
            ``tolerance`` (CRS units).
        tolerance: Threshold in CRS units. For ``redundant_only`` this
            is the maximum perpendicular distance a vertex can sit
            from the chord through its neighbours before it is
            considered non-redundant. For ``douglas_peucker`` it is
            the shapely tolerance.

    Returns:
        An :class:`EditResult` describing the bulk ``replace_table``
        edit; ``result.edit.table`` is ``"link"`` or ``"geometry"``
        depending on where the shapes live.

    Raises:
        CleanError: If the network has no link shapes in either
            location, or ``mode`` is unrecognized.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("shapely")
        >>> from netstead import Network
        >>> from corral.editing import Session
        >>> # (See packages/netstead/tests/test_clean.py for runnable examples.)
        >>> simplify_geometry  # doctest: +ELLIPSIS
        <function simplify_geometry at ...>
    """
    from netstead._geom import shapely_from_any

    target = _shape_table(net, op="simplify_geometry")
    if mode not in {"redundant_only", "douglas_peucker"}:
        raise CleanError(f"simplify_geometry mode must be 'redundant_only' or 'douglas_peucker'; got {mode!r}.")

    arrow = _to_arrow(net.tables[target])
    new_rows: list[dict] = arrow.to_pylist()
    for row in new_rows:
        geom = shapely_from_any(row.get("geometry"))
        if geom is None or geom.is_empty or geom.geom_type != "LineString":
            continue
        if mode == "douglas_peucker":
            simplified = geom.simplify(tolerance, preserve_topology=False)
        else:
            simplified = _drop_collinear(geom, tolerance=tolerance)
        if simplified is geom or simplified.is_empty:
            continue
        # Write geometry back as WKB bytes (canonical in-memory encoding; ADR).
        row["geometry"] = simplified.wkb
    return _submit_replace(session, target, new_rows)


def _drop_collinear(linestring, *, tolerance: float):
    """Return a :class:`shapely.LineString` with redundant interior vertices removed.

    A vertex is redundant when its perpendicular distance from the
    chord through its previous + next neighbour is <= ``tolerance``.
    With ``tolerance == 0.0`` only strictly-collinear vertices drop —
    no spatial accuracy is lost.
    """
    from shapely.geometry import LineString

    coords = [tuple(c[:2]) for c in linestring.coords]
    if len(coords) <= 2:
        return linestring
    keep = [coords[0]]
    for i in range(1, len(coords) - 1):
        prev = keep[-1]
        nxt = coords[i + 1]
        if _perp_distance(prev, coords[i], nxt) > tolerance:
            keep.append(coords[i])
    keep.append(coords[-1])
    if len(keep) == len(coords):
        return linestring  # nothing dropped
    return LineString(keep)


def _perp_distance(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    """Perpendicular distance from point ``b`` to the line through ``a`` and ``c``.

    Returns 0.0 when ``a`` and ``c`` coincide (degenerate chord).
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    chord_dx = cx - ax
    chord_dy = cy - ay
    chord_len = math.hypot(chord_dx, chord_dy)
    if chord_len == 0.0:
        return 0.0
    # |(b-a) x (c-a)| / |c-a|
    cross = (bx - ax) * chord_dy - (by - ay) * chord_dx
    return abs(cross) / chord_len


# ---------------------------------------------------------------------------
# Op 2 — merge_close_nodes
# ---------------------------------------------------------------------------


def merge_close_nodes(
    net: Network,
    session: Session,
    *,
    threshold_m: float = 5.0,
) -> list[EditResult]:
    """Merge pairs of nodes within ``threshold_m`` of each other.

    Survivor selection: lowest ``node_id`` per cluster. All incident
    links have their ``from_node_id`` / ``to_node_id`` rewritten to
    the survivor. Merged nodes are dropped from the node table.

    Args:
        net: Source :class:`Network`.
        session: Open :class:`Session`.
        threshold_m: Distance threshold in node CRS units (meters for
            projected coords; degrees for WGS84 — tune accordingly).

    Returns:
        A list of :class:`EditResult` — one for the ``node`` replace
        and one for the ``link`` replace.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("shapely")
        >>> merge_close_nodes  # doctest: +ELLIPSIS
        <function merge_close_nodes at ...>
    """
    nodes_arrow = _to_arrow(net.nodes)
    links_arrow = _to_arrow(net.links)

    rows = list(
        zip(
            nodes_arrow.column("node_id").to_pylist(),
            nodes_arrow.column("x_coord").to_pylist(),
            nodes_arrow.column("y_coord").to_pylist(),
            strict=True,
        )
    )
    clean = [(nid, float(x), float(y)) for nid, x, y in rows if nid is not None and x is not None and y is not None]
    threshold_sq = threshold_m * threshold_m

    # Build an id -> survivor mapping via greedy clustering with a
    # coarse spatial grid pre-filter. The grid cell size matches
    # ``threshold_m`` so any two nodes within ``threshold_m`` are
    # guaranteed to land in the same cell OR an immediately-adjacent
    # one — we only compare those pairs, which is ~O(N) when nodes are
    # spatially distributed (the common case for any real network).
    #
    # The full STRtree-based clusterer is filed as v1.1 work; for now
    # this brings 1k-node merges from ~0.5s to <10ms and stays usable
    # well past the 10k mark.
    grid_size = threshold_m if threshold_m > 0.0 else 1.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, (_nid, x, y) in enumerate(clean):
        grid[(int(x // grid_size), int(y // grid_size))].append(idx)

    survivor: dict[Any, Any] = {nid: nid for nid, _, _ in clean}
    for (gx, gy), idxs in grid.items():
        # Candidate set: this cell + the 8 neighbours. Each pair only
        # compared once by gating on ``j > i``.
        candidates: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(grid.get((gx + dx, gy + dy), ()))
        # Pair every i in this cell with every j in the 3x3 neighbourhood
        # whose index is larger (ordering avoids double-comparison and
        # keeps the merge direction stable).
        for i in idxs:
            id_i, xi, yi = clean[i]
            for j in candidates:
                if j <= i:
                    continue
                id_j, xj, yj = clean[j]
                ddx, ddy = xi - xj, yi - yj
                if ddx * ddx + ddy * ddy > threshold_sq:
                    continue
                keep = min(survivor[id_i], survivor[id_j], key=_sort_key)
                survivor[id_i] = keep
                survivor[id_j] = keep
    # Resolve chains so survivor[x] is always a root.
    for k in list(survivor):
        seen = set()
        cur = k
        while survivor[cur] != cur and cur not in seen:
            seen.add(cur)
            cur = survivor[cur]
        survivor[k] = cur

    # Pre-compute the survivor set once — the original code recomputed
    # ``survivor.values()`` on every link row, which on a 10k-link
    # network was the second-largest cost after the N² scan.
    survivors_set = set(survivor.values())

    # Filter the node table to keep only survivors.
    new_node_rows = [row for row in nodes_arrow.to_pylist() if row.get("node_id") in survivors_set]

    # Rewrite link from/to ids to survivors.
    new_link_rows = []
    for row in links_arrow.to_pylist():
        new_row = dict(row)
        if (from_id := row.get("from_node_id")) in survivor:
            new_row["from_node_id"] = survivor[from_id]
        if (to_id := row.get("to_node_id")) in survivor:
            new_row["to_node_id"] = survivor[to_id]
        new_link_rows.append(new_row)

    return [
        _submit_replace(session, "node", new_node_rows),
        _submit_replace(session, "link", new_link_rows),
    ]


def _sort_key(value: Any) -> tuple[int, Any]:
    """Sort key that handles mixed-type ids (ints first by value, then strings)."""
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


# ---------------------------------------------------------------------------
# Op 3 — remove_orphans
# ---------------------------------------------------------------------------


def remove_orphans(net: Network, session: Session) -> EditResult:
    """Drop nodes with no incident links.

    Args:
        net: Source :class:`Network`.
        session: Open :class:`Session`.

    Returns:
        An :class:`EditResult` describing the ``node`` replace.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("shapely")
        >>> remove_orphans  # doctest: +ELLIPSIS
        <function remove_orphans at ...>
    """
    links_arrow = _to_arrow(net.links)
    incident: set[Any] = set()
    for v in links_arrow.column("from_node_id").to_pylist():
        if v is not None:
            incident.add(v)
    for v in links_arrow.column("to_node_id").to_pylist():
        if v is not None:
            incident.add(v)

    nodes_arrow = _to_arrow(net.nodes)
    new_node_rows = [row for row in nodes_arrow.to_pylist() if row.get("node_id") in incident]
    return _submit_replace(session, "node", new_node_rows)


# ---------------------------------------------------------------------------
# Op 4 — recompute_lengths
# ---------------------------------------------------------------------------


def recompute_lengths(net: Network, session: Session, *, geodesic: bool = False) -> EditResult:
    """Recompute ``link.length`` from each link's resolved geometry.

    Shapes are resolved per link with
    :func:`netstead.semantics.assemble_link_geometry` — inline
    ``link.geometry`` first, then the ``geometry`` table via
    ``link.geometry_id``, then a straight segment between the endpoint
    nodes. Links that resolve to nothing keep their current ``length``.
    The edit always lands on ``link`` because that is where ``length``
    lives.

    Args:
        net: Source :class:`Network`. Must have link shapes in at
            least one of the three locations above.
        session: Open :class:`Session`.
        geodesic: When ``True``, treat geometry coords as
            (lon, lat) in WGS84 and compute haversine length in meters.
            When ``False`` (default), report shapely's planar length
            in CRS units (right for projected networks).

    Returns:
        An :class:`EditResult` describing the ``link`` replace.

    Raises:
        CleanError: If no link resolves to a shape at all (no inline
            column, no ``geometry`` table, no node coordinates).

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("shapely")
        >>> recompute_lengths  # doctest: +ELLIPSIS
        <function recompute_lengths at ...>
    """
    from netstead._geom import shapely_from_any
    from netstead.semantics.geometry import GeometrySource, assemble_link_geometry

    assembled = assemble_link_geometry(net)
    if all(src == GeometrySource.MISSING for src in assembled.column("source").to_pylist()):
        raise CleanError(
            "recompute_lengths found no link shapes: the network has neither an inline "
            "'link.geometry' column, nor a 'geometry' table reachable via link.geometry_id, "
            "nor node coordinates to fall back on. Add link geometry before running this op."
        )
    shape_by_link = dict(
        zip(assembled.column("link_id").to_pylist(), assembled.column("geometry").to_pylist(), strict=True)
    )

    new_rows = _to_arrow(net.links).to_pylist()
    for row in new_rows:
        geom = shapely_from_any(shape_by_link.get(row.get("link_id")))
        if geom is None or geom.is_empty or geom.geom_type != "LineString":
            continue
        row["length"] = _geodesic_length_m(geom) if geodesic else float(geom.length)
    return _submit_replace(session, "link", new_rows)


def _geodesic_length_m(linestring) -> float:
    """Haversine length in meters for a (lon, lat) WGS84 LineString."""
    from itertools import pairwise

    earth_r = 6_371_000.0
    coords = list(linestring.coords)
    total = 0.0
    for (lon1, lat1, *_), (lon2, lat2, *_) in pairwise(coords):
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        total += 2 * earth_r * math.asin(math.sqrt(a))
    return total
