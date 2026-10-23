"""GMNS data-quality rule pack — registered into :mod:`corral.quality`.

Each rule is a plain class with the four fields required by
:class:`corral.quality.Rule`:

* ``code`` — stable dotted identifier (e.g. ``"quality.high_speed_residential"``).
* ``description`` — one-line plain-English explanation.
* ``severity`` — default :class:`~corral.reports.Severity`.
* ``applies_to(package)`` — cheap pre-check (usually "is the required
  table present?").
* ``run(package, report)`` — does the work, emits zero or more
  :class:`~corral.reports.Issue` records into ``report``.

Rules read their thresholds from
:attr:`corral.quality.RuleConfig.thresholds` (a plain dict). Each
rule documents its keys + defaults in its docstring.

All rules in this module emit ``Issue.category = Category.DATA_QUALITY``
and ``Severity.WARNING`` (or ``INFO``) by default — they are not spec
violations. Callers wanting hard errors should override via
``RuleConfig(severity_override=Severity.ERROR)``.

**Predicate pushdown.** Rules express their predicate as an ibis
expression over ``Table.expr`` and materialize only the offending rows
(plus the few columns the issue message quotes), so DuckDB does the
filtering and a 200k-link network is never pulled into Python just to
find a handful of findings. Two rules need Python on real values:
:class:`SharpAngleBendsRule` parses geometries with shapely (after
pushing down a non-null filter and selecting two columns), and
:class:`DisconnectedComponentsRule` reuses the shared graph index.

Every rule reports ``Issue.row`` as the row's position in the table's
own order (the order ``Table.to_pandas()`` returns), carried through
the filter by :func:`_indexed`.

Why one file: the rules read a thin slice of the network, compute a
predicate, emit issues. The patterns are similar enough that side-by-side
reading helps; spreading them across files would obscure the shared
shape.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import ibis
from corral.quality import RuleConfig, register_rule
from corral.reports import Category, Issue, Severity

from netstead._quality_thresholds import check_combinations, check_epsilon

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    import ibis.expr.types as ir
    from corral.dataset import Package, Table
    from corral.reports import ValidationReport

__all__ = [
    "DEFAULT_UNIQUE_COMBINATIONS",
    "DisconnectedComponentsRule",
    "DuplicateNearNodesRule",
    "HighSpeedResidentialRule",
    "ImplausibleVcRule",
    "LaneCountMismatchRule",
    "MissingCriticalFieldsRule",
    "NonUniqueCombinationRule",
    "OverlappingLinksRule",
    "SharpAngleBendsRule",
    "register_all",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

#: Column carrying each row's original position through filters and joins (``Issue.row``).
_ROW = "__quality_row__"


def _indexed(table: Table) -> ir.Table:
    """Return ``table``'s ibis expression with a 0-based row-position column (:data:`_ROW`).

    ``row_number()`` is attached before any filter, so the position is the
    row's place in the whole table, the same position ``Table.to_pandas()``
    gives it.
    """
    expr = table.expr
    return expr.mutate(**{_ROW: ibis.row_number().cast("int64")})


def _present(column: ir.Column) -> ir.BooleanValue:
    """``column`` is non-null and, for floating columns, not NaN.

    ``Table.to_pandas()`` reads a NaN as missing (``convert_dtypes``), and
    the rules treat missing values as "skip"; DuckDB orders NaN above every
    number, so without this a NaN speed would compare as fast.
    """
    if column.type().is_floating():
        return column.notnull() & ~column.isnan()
    return column.notnull()


def _id_display(expr: ir.Table, column: str) -> Callable[[Any], Any]:
    """How to show values of id ``column`` of ``expr`` in messages and ``extra``.

    A floating id column that holds only whole numbers (a CSV id column with
    a blank cell, read as DOUBLE) shows its ids as ints, so a message says
    ``link_id=1``, not ``1.0``; one fractional id keeps the whole column as
    floats. NaN shows as ``None``. The check is one aggregate over the whole
    column, so the answer doesn't depend on which rows a rule happens to flag.
    """
    col = expr[column]
    if not col.type().is_floating():
        return lambda value: value
    fractional = expr.filter(_present(col), col % 1.0 != 0.0).count().to_pyarrow().as_py()
    whole = fractional == 0

    def show(value: Any) -> Any:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(value) if whole else value

    return show


def _rows(expr: ir.Table) -> list[dict[str, Any]]:
    """Execute ``expr`` and return its rows as dicts (only the filtered rows cross into Python)."""
    return expr.to_pyarrow().to_pylist()


def _threshold(rc: RuleConfig | None, key: str, default: float) -> float:
    """Return the configured threshold for ``key`` (or default).

    Rules accept either a :class:`RuleConfig` (forwarded from
    :func:`corral.quality.run_quality`) or ``None`` (test/CLI
    direct-call). Threshold values can be int or float — coerce.
    """
    if rc is None:
        return float(default)
    return float(rc.thresholds.get(key, default))


# ---------------------------------------------------------------------------
# Rule 1 — high speed on residential street
# ---------------------------------------------------------------------------

#: Every character for which ``str.isspace()`` is true: exactly what ``str.strip()`` removes.
_PY_WHITESPACE = "".join(
    map(
        chr,
        (*range(9, 14), *range(28, 33), 133, 160, 5760, *range(8192, 8203), 8232, 8233, 8239, 8287, 12288),
    )
)
#: :data:`_PY_WHITESPACE` as an RE2 character class (RE2's ``\s`` covers only ASCII ``[\t\n\f\r ]``).
_WHITESPACE_CLASS = "[" + "".join(f"\\x{{{ord(c):x}}}" for c in _PY_WHITESPACE) + "]"


class HighSpeedResidentialRule:
    """Flag residential links with implausibly high ``free_speed``.

    Threshold keys:

    * ``speed_limit_mph`` (default ``35``) — anything above is flagged.

    Looks at ``link.facility_type`` (matched against ``residential`` /
    ``local`` case-insensitively, ignoring surrounding whitespace) and
    ``link.free_speed``. Skips links with null facility type or null
    free_speed (those belong to the ``MissingCriticalFieldsRule``).

    The ``free_speed`` unit is whatever the spec's
    ``long_distance_units`` field declares — GMNS defaults to mph, so
    the threshold key is named ``speed_limit_mph`` for clarity. If your
    network uses km/h, set ``speed_limit_mph`` to your km/h threshold;
    the comparison is unit-agnostic from the rule's point of view.
    """

    code = "quality.high_speed_residential"
    description = "Residential / local street with free_speed above threshold."
    severity = Severity.WARNING
    _FACILITY_TYPES = frozenset({"residential", "local"})

    def applies_to(self, package: Package) -> bool:
        """We need both the link table AND its facility_type + free_speed columns."""
        link = package.tables.get("link")
        if link is None:
            return False
        cols = set(link.columns())
        return {"link_id", "facility_type", "free_speed"} <= cols

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Filter the link table in the engine; emit one issue per offending row."""
        threshold = _threshold(rc, "speed_limit_mph", 35.0)
        link = _indexed(package.tables["link"])
        show_id = _id_display(link, "link_id")
        ftype = link.facility_type.cast("string")
        speed = link.free_speed.cast("float64")
        # One anchored regex = str.strip() + case-insensitive set membership, evaluated in the engine.
        names = "|".join(sorted(map(re.escape, self._FACILITY_TYPES)))
        pattern = f"^{_WHITESPACE_CLASS}*(?:{names}){_WHITESPACE_CLASS}*$"
        offending = (
            link.filter(ftype.notnull(), _present(speed), ftype.lower().re_search(pattern), speed > threshold)
            .select(_ROW, "link_id", "facility_type", "free_speed")
            .order_by(_ROW)
        )
        for row in _rows(offending):
            i = row[_ROW]
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link row {i} (link_id={show_id(row['link_id'])!r}): "
                        f"facility_type={row['facility_type']!r} with free_speed={row['free_speed']} "
                        f"> threshold {threshold}."
                    ),
                    table="link",
                    column="free_speed",
                    row=i,
                    fix_hint=(
                        "If the speed is correct, change facility_type to a higher class "
                        "(e.g. 'tertiary', 'secondary')."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Rule 2 — disconnected components
# ---------------------------------------------------------------------------


class DisconnectedComponentsRule:
    """Flag networks with more than one weakly-connected component.

    Threshold keys: none (the presence of any extra component is the
    quality concern).

    Emits a single ``Severity.INFO`` issue (not per-component) listing
    the component count + sizes. ``connected_components`` is delegated
    to :mod:`netstead.semantics.connectivity` so the same GMNSGraph (scipy)
    build is shared with any other connectivity caller in the session.
    No table is materialized here: the graph index reads only the
    columns it needs.
    """

    code = "quality.disconnected_components"
    description = "Network has more than one weakly-connected component."
    severity = Severity.INFO

    def applies_to(self, package: Package) -> bool:
        """We need link + node tables AND the columns GMNSGraph.build needs.

        Skips silently on non-Network packages (no GMNS accessors) and on
        networks missing ``length`` on the link table — the upstream
        :class:`~netstead.graph.GMNSGraph` (built with ``cost="length"``)
        needs it and we'd rather no-op than blow up.
        """
        if not (hasattr(package, "links") and hasattr(package, "nodes")):
            return False
        link = package.tables.get("link")
        node = package.tables.get("node")
        if link is None or node is None:
            return False
        required_link_cols = {"from_node_id", "to_node_id", "length"}
        return required_link_cols <= set(link.columns()) and "node_id" in node.columns()

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Build (or reuse) the GMNSGraph, count components."""
        # Local import: connectivity pulls in scipy (the [graph] extra).
        from netstead.semantics import connected_components

        comps = connected_components(package)
        if len(comps) <= 1:
            return
        sizes = sorted((len(c) for c in comps), reverse=True)
        report.add_issue(
            Issue(
                severity=self.severity,
                category=Category.DATA_QUALITY,
                code=self.code,
                message=(
                    f"Network has {len(comps)} weakly-connected components "
                    f"(sizes: {sizes[:10]}{'…' if len(sizes) > 10 else ''})."
                ),
                table="link",
                fix_hint=(
                    "Use netstead.clean.connect_disconnected_components to add bridge links, or drop small components."
                ),
                extra={"component_sizes": sizes},
            )
        )


# ---------------------------------------------------------------------------
# Rule 3 — lane-count mismatch between link.lanes and lane table
# ---------------------------------------------------------------------------


class LaneCountMismatchRule:
    """Flag links where ``link.lanes`` disagrees with the ``lane`` table row count.

    Threshold keys: none — any non-zero mismatch is flagged.

    Skips links where either ``link.lanes`` is null OR the ``lane``
    table has zero rows for that link (those cases are
    "underspecified", not "wrong" — caught by :class:`MissingCriticalFieldsRule`).
    A fractional ``link.lanes`` is compared by its whole part.
    """

    code = "quality.lane_count_mismatch"
    description = "link.lanes disagrees with the row count in the lane table."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Needs both the link table AND a lane table."""
        link = package.tables.get("link")
        lane = package.tables.get("lane")
        if link is None or lane is None:
            return False
        return "lanes" in link.columns() and "link_id" in lane.columns()

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Count lane rows per link in the engine, join to link.lanes, emit the mismatches."""
        link = _indexed(package.tables["link"])
        lane = package.tables["lane"].expr
        if link.link_id.type().is_string() != lane.link_id.type().is_string():
            return  # A text id never equals a numeric one: no link has lane rows.
        show_id = _id_display(link, "link_id")
        lane_link, lane_rows = "__quality_lane_link_id__", "__quality_lane_rows__"
        counts = (
            lane.filter(lane.link_id.notnull())
            .select(**{lane_link: lane.link_id})
            .group_by(lane_link)
            .aggregate(**{lane_rows: ibis._.count()})
        )
        declared = link.lanes.cast("float64")
        candidates = link.filter(_present(declared), link.link_id.notnull())
        joined = candidates.join(counts, candidates.link_id == counts[lane_link])
        # Lane counts are >= 1 here, so floor() matches int() truncation for every value that could be equal.
        offending = (
            joined.filter(joined.lanes.cast("float64").floor() != joined[lane_rows])
            .select(_ROW, "link_id", "lanes", lane_rows)
            .order_by(_ROW)
        )
        for row in _rows(offending):
            i = row[_ROW]
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link row {i} (link_id={show_id(row['link_id'])!r}): link.lanes={row['lanes']} "
                        f"but lane table has {row[lane_rows]} rows for this link."
                    ),
                    table="link",
                    column="lanes",
                    row=i,
                    fix_hint="Either correct link.lanes or add/remove lane rows.",
                )
            )


# ---------------------------------------------------------------------------
# Rule 4 — duplicate/near-duplicate nodes (within ε meters)
# ---------------------------------------------------------------------------

#: The 3x3 block of grid cells around a node's own cell; a node within epsilon lies in one of them.
_NEIGHBOUR_CELLS = {
    "__quality_ox__": [-1.0, -1.0, -1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
    "__quality_oy__": [-1.0, 0.0, 1.0] * 3,
}


class DuplicateNearNodesRule:
    """Flag pairs of nodes within ``epsilon_units`` of each other.

    Threshold keys:

    * ``epsilon_units`` (default ``1e-5``) — distance threshold in the
      node coordinate CRS units. The default is deliberately tiny so
      it means "effectively duplicate" in any CRS: ~1m for WGS84 at
      mid-latitudes; a hair's breadth for projected coords. **Tune it
      for your CRS.** For projected networks (UTM, state plane) where
      coordinates are meters, ``5.0`` is a reasonable "near-duplicate"
      threshold. For WGS84 degrees, ``0.0001`` is roughly 10m. ``0``
      flags only exact duplicates; a negative value raises
      :class:`ValueError`.

    Implementation: a grid self-join in the engine. Each node is hashed
    to an epsilon-sized grid cell and joined against the nodes of the
    3x3 block of cells around it, so the work grows with the number of
    nodes, not its square; only the pairs within epsilon reach Python.
    """

    code = "quality.duplicate_near_nodes"
    description = "Pairs of nodes within epsilon distance of each other."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Needs a node table with node_id + x_coord + y_coord columns."""
        node = package.tables.get("node")
        if node is None:
            return False
        cols = set(node.columns())
        return {"node_id", "x_coord", "y_coord"} <= cols

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Self-join node points on grid cells; flag every pair within epsilon."""
        epsilon = check_epsilon(rc.thresholds.get("epsilon_units", 1e-5) if rc else 1e-5)
        node_table = package.tables["node"]
        show_id = _id_display(node_table.expr, "node_id")
        seen_pairs: set[tuple[Any, Any]] = set()
        for row in _rows(_near_pairs(node_table, epsilon)):
            id_i, id_j = show_id(row["id_i"]), show_id(row["id_j"])
            pair = (id_i, id_j) if str(id_i) < str(id_j) else (id_j, id_i)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            dx, dy = row["x_i"] - row["x_j"], row["y_i"] - row["y_j"]
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"node_id={id_i!r} and node_id={id_j!r} are within "
                        f"epsilon={epsilon} of each other "
                        f"(distance ~ {(dx * dx + dy * dy) ** 0.5:.4g})."
                    ),
                    table="node",
                    fix_hint="If they represent the same intersection, merge via netstead.clean.merge_close_nodes.",
                    extra={"node_ids": [id_i, id_j]},
                )
            )


def _near_pairs(node_table: Table, epsilon: float) -> ir.Table:
    """Node pairs (row i < row j) within ``epsilon``, ordered by (i, j): the order a pairwise scan finds them.

    Cells are a hair wider than epsilon so two nodes within epsilon never sit
    more than one cell apart after rounding. The cell key truncates with
    ``x - fmod(x, 1)`` in float64 rather than ``floor`` to int64, which would
    overflow for a tiny epsilon over large coordinates; truncation is
    monotonic, so neighbours are still at most one cell apart. ``distinct``
    drops the repeats that appear when coordinates are so large relative to
    epsilon that adjacent cell keys round to the same float.
    """
    node = _indexed(node_table)
    x = node.x_coord.cast("float64")
    y = node.y_coord.cast("float64")
    pts = node.filter(_present(node.node_id), _present(x), _present(y)).select(_ROW, nid=node.node_id, x=x, y=y)
    if epsilon == 0:
        a, b = pts.view(), pts.view()
        on = [a.x == b.x, a.y == b.y]
    else:
        cell = epsilon * (1 + 1e-9)
        qx, qy = pts.x / cell, pts.y / cell
        keyed = pts.mutate(cx=qx - qx % 1.0, cy=qy - qy % 1.0)
        offsets = ibis.memtable(_NEIGHBOUR_CELLS)
        a = keyed.view()
        b = keyed.cross_join(offsets)
        b = b.mutate(cx=b.cx + b["__quality_ox__"], cy=b.cy + b["__quality_oy__"]).drop(*_NEIGHBOUR_CELLS)
        on = [a.cx == b.cx, a.cy == b.cy]
    dist_sq = (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)
    pairs = a.join(b, [*on, a[_ROW] < b[_ROW], dist_sq <= epsilon * epsilon])
    return (
        pairs.select(row_i=a[_ROW], row_j=b[_ROW], id_i=a.nid, id_j=b.nid, x_i=a.x, y_i=a.y, x_j=b.x, y_j=b.y)
        .distinct()
        .order_by("row_i", "row_j")
    )


# ---------------------------------------------------------------------------
# Rule 5 — sharp interior angles on link geometries
# ---------------------------------------------------------------------------


class SharpAngleBendsRule:
    """Flag links whose geometry contains an interior turn angle below threshold.

    Threshold keys:

    * ``min_angle_degrees`` (default ``30.0``) — any interior vertex
      with a turn angle below this is flagged.

    Skips links without inline ``geometry`` (the geometry-table case is
    handled by :func:`netstead.semantics.assemble_link_geometry` callers
    who run this rule after assembly).

    Requires shapely (clean extra). Angles need parsed geometries, which
    the engine can't give us, so this rule materializes values: only the
    ``link_id`` and ``geometry`` of links with a non-null geometry. It
    finds candidates with vectorised shapely + numpy, then measures each
    candidate's sharpest angle exactly as reported.
    """

    code = "quality.sharp_angle_bends"
    description = "Link geometry has a sharp interior turn angle."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Needs a link table with an inline ``geometry`` column."""
        link = package.tables.get("link")
        if link is None:
            return False
        return "geometry" in link.columns()

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Parse link geometries (WKT or WKB), compute interior turn angles, flag sharp ones."""
        min_angle = _threshold(rc, "min_angle_degrees", 30.0)
        link = _indexed(package.tables["link"])
        with_geometry = link.filter(link.geometry.notnull()).select(_ROW, "link_id", "geometry").order_by(_ROW)
        rows = with_geometry.to_pyarrow()
        show_id = _id_display(link, "link_id")
        for i, raw_id, sharpest in _sharp_lines(rows, min_angle):
            link_id = show_id(raw_id)
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link row {i} (link_id={link_id!r}): "
                        f"sharpest interior angle {sharpest:.1f}° < threshold {min_angle}°."
                    ),
                    table="link",
                    column="geometry",
                    row=i,
                    fix_hint="Verify the bend; consider splitting at the vertex with split_link_at_node.",
                    extra={"angle_degrees": sharpest},
                )
            )


#: Candidate slack (degrees) between the vectorised pre-pass and the exact per-link measure.
_ANGLE_SLACK_DEG = 1e-6


def _sharp_lines(rows: Any, min_angle: float) -> list[tuple[int, Any, float]]:
    """``(row, link_id, sharpest_angle)`` for each LineString in ``rows`` with an angle below ``min_angle``.

    ``rows`` is a pyarrow table of (:data:`_ROW`, ``link_id``, ``geometry``)
    with WKT text or WKB bytes. A vectorised numpy pass computes every
    interior angle and keeps the lines whose sharpest is within a hair of
    the threshold; :func:`_min_interior_angle_deg` then measures those
    exactly, so the reported angle does not depend on numpy's rounding.
    """
    import numpy as np
    import shapely

    if rows.num_rows == 0:
        return []
    geoms = _parse_geometries(rows.column("geometry").to_pylist())
    is_line = (shapely.get_type_id(geoms) == shapely.GeometryType.LINESTRING) & ~shapely.is_empty(geoms)
    is_line &= shapely.get_num_coordinates(geoms) >= 3
    lines = np.flatnonzero(is_line)
    if lines.size == 0:
        return []
    coords, owner = shapely.get_coordinates(geoms[lines], return_index=True)
    # Interior vertex k: k-1, k, k+1 all belong to the same line.
    mid = np.flatnonzero((owner[1:-1] == owner[:-2]) & (owner[1:-1] == owner[2:])) + 1
    v1 = coords[mid - 1] - coords[mid]
    v2 = coords[mid + 1] - coords[mid]
    n1 = np.hypot(v1[:, 0], v1[:, 1])
    n2 = np.hypot(v2[:, 0], v2[:, 1])
    ok = (n1 != 0.0) & (n2 != 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos_t = np.clip((v1[:, 0] * v2[:, 0] + v1[:, 1] * v2[:, 1]) / (n1 * n2), -1.0, 1.0)
    angles = np.degrees(np.arccos(cos_t))
    sharpest = np.full(lines.size, 180.0)
    np.minimum.at(sharpest, owner[mid[ok]], angles[ok])
    candidates = lines[sharpest < min_angle + _ANGLE_SLACK_DEG]

    row_pos = rows.column(_ROW).to_pylist()
    link_ids = rows.column("link_id").to_pylist()
    found: list[tuple[int, Any, float]] = []
    for k in candidates:
        exact = _min_interior_angle_deg(list(shapely.get_coordinates(geoms[k]).tolist()))
        if exact is None or exact >= min_angle:
            continue
        found.append((row_pos[k], link_ids[k], exact))
    return found


def _parse_geometries(values: list[Any]) -> Any:
    """Vectorised :func:`netstead._geom.shapely_from_any`: an array of shapely geometries, ``None`` if unparseable.

    WKB bytes and WKT text may be mixed; empty strings and malformed values
    become ``None``, as they do one at a time.
    """
    import numpy as np
    import shapely

    geoms = np.full(len(values), None, dtype=object)
    wkb = [k for k, v in enumerate(values) if isinstance(v, (bytes, bytearray, memoryview))]
    wkt = [k for k, v in enumerate(values) if isinstance(v, str) and v.strip()]
    if wkb:
        geoms[wkb] = shapely.from_wkb(np.array([bytes(values[k]) for k in wkb], dtype=object), on_invalid="ignore")
    if wkt:
        geoms[wkt] = shapely.from_wkt(np.array([values[k] for k in wkt], dtype=object), on_invalid="ignore")
    return geoms


def _min_interior_angle_deg(coords: list[tuple[float, float] | tuple[float, float, float]]) -> float | None:
    """Return the smallest interior turn angle (degrees) along ``coords``.

    Returns ``None`` for collinear / degenerate sequences. 180° means
    straight-through (no turn); 0° means a U-turn.
    """
    smallest = 180.0
    found = False
    for i in range(1, len(coords) - 1):
        ax, ay = coords[i - 1][:2]
        bx, by = coords[i][:2]
        cx, cy = coords[i + 1][:2]
        v1x, v1y = ax - bx, ay - by
        v2x, v2y = cx - bx, cy - by
        n1 = math.hypot(v1x, v1y)
        n2 = math.hypot(v2x, v2y)
        if n1 == 0.0 or n2 == 0.0:
            continue
        cos_t = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (n1 * n2)))
        angle = math.degrees(math.acos(cos_t))
        if angle < smallest:
            smallest = angle
            found = True
    return smallest if found else None


# ---------------------------------------------------------------------------
# Rule 6 — implausible volume / capacity ratio
# ---------------------------------------------------------------------------


class ImplausibleVcRule:
    """Flag links with ``volume / capacity > threshold``.

    Threshold keys:

    * ``vc_max`` (default ``1.5``) — anything above is flagged.
    * ``volume_column`` (default ``"volume"``) — column name to read
      for volume. The base spec doesn't define one; loaded networks
      often add it as an extension. The rule skips silently if the
      column is absent.

    Skips rows where either ``capacity`` is null or zero (would be a
    divide-by-zero) — those should surface via the schema validator,
    not here.
    """

    code = "quality.implausible_vc"
    description = "Link volume / capacity above plausibility threshold."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Needs a link table with a ``capacity`` column (volume column is configurable)."""
        link = package.tables.get("link")
        if link is None:
            return False
        return "capacity" in link.columns()

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Filter volume / capacity above ``vc_max`` in the engine; emit one issue per row."""
        vc_max = _threshold(rc, "vc_max", 1.5)
        volume_col = rc.thresholds.get("volume_column", "volume") if rc else "volume"
        link_table = package.tables["link"]
        if volume_col not in link_table.columns():
            return  # No volume column — nothing to check.
        link = _indexed(link_table)
        show_id = _id_display(link, "link_id")
        cap = link.capacity.cast("float64")
        vol = link[volume_col].cast("float64")
        offending = (
            link.filter(_present(cap), _present(vol), cap != 0.0, vol / cap > vc_max)
            .select(_ROW, "link_id", "capacity", volume_col)
            .order_by(_ROW)
        )
        for row in _rows(offending):
            i, cap_v, vol_v = row[_ROW], row["capacity"], row[volume_col]
            vc = float(vol_v) / float(cap_v)
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link row {i} (link_id={show_id(row['link_id'])!r}): "
                        f"volume={vol_v} / capacity={cap_v} = {vc:.2f} > threshold {vc_max}."
                    ),
                    table="link",
                    column=volume_col,
                    row=i,
                    fix_hint="Verify the volume estimate or recompute capacity.",
                    extra={"vc_ratio": vc},
                )
            )


# ---------------------------------------------------------------------------
# Rule 7 — missing critical-but-optional fields
# ---------------------------------------------------------------------------


class MissingCriticalFieldsRule:
    """Flag tables where a "should have" but spec-optional column is mostly null.

    Threshold keys:

    * ``coverage_min`` (default ``0.90``) — minimum fraction of
      non-null rows required to NOT flag the column.

    Emits one ``Severity.INFO`` issue per affected (table, column)
    pair, in column-name order. Targets the columns the GMNS community
    treats as effectively required even when the spec marks them
    optional — ``link.length``, ``link.free_speed``, ``link.capacity``,
    ``link.lanes``. The non-null counts are one aggregate query; NaN
    counts as null.
    """

    code = "quality.missing_critical_fields"
    description = "Critical-but-optional column has high null rate."
    severity = Severity.INFO
    _CRITICAL_LINK_FIELDS: frozenset[str] = frozenset({"length", "free_speed", "capacity", "lanes"})

    def applies_to(self, package: Package) -> bool:
        """Needs a link table (we check the critical columns inside :meth:`run`)."""
        return "link" in package.tables

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """For each critical column present, emit an Info issue if non-null coverage < threshold."""
        coverage_min = _threshold(rc, "coverage_min", 0.90)
        link_table = package.tables["link"]
        present_cols = sorted(self._CRITICAL_LINK_FIELDS & set(link_table.columns()))
        link = link_table.expr
        counts = _rows(
            link.aggregate(
                __quality_rows__=link.count(),
                **{c: _present(link[c]).cast("int64").sum() for c in present_cols},
            )
        )[0]
        num_rows = counts["__quality_rows__"]
        if num_rows == 0:
            return
        for col_name in present_cols:
            non_null = counts[col_name] or 0
            coverage = non_null / num_rows
            if coverage >= coverage_min:
                continue
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link.{col_name}: {non_null}/{num_rows} rows non-null "
                        f"(coverage {coverage:.0%} < threshold {coverage_min:.0%})."
                    ),
                    table="link",
                    column=col_name,
                    fix_hint=(
                        "Backfill from upstream OSM / model data, or document why the column "
                        "is intentionally sparse for this network."
                    ),
                    extra={"coverage": coverage},
                )
            )


# ---------------------------------------------------------------------------
# Rule 8 — unique natural-key combinations
# ---------------------------------------------------------------------------

#: Column combinations GMNS expects to be unique even though each table's primary key is a surrogate id.
#: A time-of-day table holds one row per record per time window; a lane table one row per lane number per parent.
#: Read-only: copy it (``{t: [list(c) for c in cs] for t, cs in DEFAULT_UNIQUE_COMBINATIONS.items()}``) to extend.
DEFAULT_UNIQUE_COMBINATIONS: Mapping[str, tuple[tuple[str, ...], ...]] = MappingProxyType(
    {
        "link_tod": (("link_id", "time_day"), ("link_id", "timeday_id")),
        "lane_tod": (("lane_id", "time_day"), ("lane_id", "timeday_id")),
        "segment_tod": (("segment_id", "time_day"), ("segment_id", "timeday_id")),
        "segment_lane_tod": (("segment_lane_id", "time_day"), ("segment_lane_id", "timeday_id")),
        "movement_tod": (("mvmt_id", "time_day"), ("mvmt_id", "timeday_id")),
        "signal_timing_plan": (("controller_id", "time_day"), ("controller_id", "timeday_id")),
        "signal_timing_phase": (("timing_plan_id", "signal_phase_num"),),
        "lane": (("link_id", "lane_num"),),
        "segment_lane": (("segment_id", "lane_num"),),
    }
)

#: Row positions listed per duplicate group in ``Issue.extra["rows"]``; ``count`` always has the full size.
_MAX_ROWS_PER_GROUP = 20


class NonUniqueCombinationRule:
    """Flag rows that repeat a combination of columns that should be unique.

    GMNS 0.97 gives the time-of-day tables a surrogate primary key
    (``link_tod_id``, ...), so two ``link_tod`` rows for the same
    ``link_id`` and ``time_day`` pass spec validation although they
    contradict each other. This rule groups each table by its natural
    key in the engine and reports every combination that occurs more
    than once.

    Threshold keys:

    * ``combinations`` (default :data:`DEFAULT_UNIQUE_COMBINATIONS`) — a
      mapping of table name to a list of column combinations, each a list
      of column names. Setting it **replaces** the defaults, so include
      every combination you still want checked. A default combination
      whose table or columns are absent is skipped silently; a configured
      one is logged (a ``WARNING`` on the ``netstead.quality.rules``
      logger), since it is probably a typo. It is not an issue, so
      ``severity_override`` never escalates it. A value that isn't a
      mapping of table name to a list of column lists raises
      :class:`ValueError` (settings reject it when they load).

    Rows with a null in any of the combination's columns are skipped (a
    missing value is the schema validator's concern, not a duplicate).
    Emits one issue per repeated combination: ``row`` is its first row,
    and ``extra`` carries ``columns``, ``values`` (column → value),
    ``rows`` (the first 20 row positions) and ``count``. Exact matches
    only: two ``time_day`` windows that overlap without being equal are
    not flagged.

    (The code and threshold avoid the word "key": the Workbench refuses
    setting names ending in ``key``, which it reserves for credentials.)
    """

    code = "quality.non_unique_combination"
    description = "Rows repeat a column combination that should be unique (e.g. link_id + time_day in link_tod)."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Any package with tables: ``run`` resolves the configured combinations against what is present."""
        return bool(package.tables)

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Group each configured table by each key combination; emit one issue per repeated combination."""
        configured = rc is not None and "combinations" in rc.thresholds
        combos_by_table = check_combinations(rc.thresholds["combinations"]) if configured else _default_combinations()
        for table_name, combos in combos_by_table.items():
            table = package.tables.get(table_name)
            present = set(table.columns()) if table is not None else set()
            for columns in combos:
                missing = [c for c in columns if c not in present]
                if table is None or missing:
                    if configured:
                        self._log_unchecked(table_name, columns, None if table is None else missing)
                    continue
                show = {c: _id_display(table.expr, c) if c.endswith("_id") else (lambda v: v) for c in columns}
                for group in _rows(_duplicate_groups(table, columns)):
                    self._report(report, table_name, columns, group, show)

    def _log_unchecked(self, table_name: str, columns: list[str], missing: list[str] | None) -> None:
        """Log a configured combination naming a table (``missing=None``) or columns the network lacks.

        A log line, not an issue: ``run_quality`` applies ``severity_override`` to every issue a rule emits, so
        a "not checked" note would become an ERROR next to the real duplicates whenever CI promotes this rule,
        and fail every network that lacks an optional table.
        """
        what = f"table {table_name!r} is not in the network" if missing is None else f"{table_name} has no {missing}"
        _log.warning("%s: configured combination (%s) not checked: %s.", self.code, ", ".join(columns), what)

    def _report(
        self,
        report: ValidationReport,
        table_name: str,
        columns: list[str],
        group: dict[str, Any],
        show: dict[str, Callable[[Any], Any]],
    ) -> None:
        rows = sorted(group["__quality_rows__"])
        values = {c: show[c](group[c]) for c in columns}
        shown = ", ".join(f"{c}={v!r}" for c, v in values.items())
        report.add_issue(
            Issue(
                severity=self.severity,
                category=Category.DATA_QUALITY,
                code=self.code,
                message=(
                    f"{table_name}: {group['__quality_count__']} rows share ({shown}); "
                    f"({', '.join(columns)}) should be unique. Rows: {rows[:_MAX_ROWS_PER_GROUP]}"
                    f"{' …' if len(rows) > _MAX_ROWS_PER_GROUP else ''}."
                ),
                table=table_name,
                row=rows[0],
                fix_hint="Merge the rows into one, or correct the key of the row that belongs elsewhere.",
                extra={
                    "columns": columns,
                    "values": values,
                    "rows": rows[:_MAX_ROWS_PER_GROUP],
                    "count": group["__quality_count__"],
                },
            )
        )


def _default_combinations() -> dict[str, list[list[str]]]:
    return {t: [list(c) for c in combos] for t, combos in DEFAULT_UNIQUE_COMBINATIONS.items()}


def _duplicate_groups(table: Table, columns: list[str]) -> ir.Table:
    """Key combinations of ``table`` over ``columns`` that occur more than once, with their row positions."""
    t = _indexed(table)
    groups = (
        t.filter(*[_present(t[c]) for c in columns])
        .group_by(columns)
        .aggregate(
            __quality_count__=ibis._.count(),
            __quality_first__=ibis._[_ROW].min(),
            __quality_rows__=ibis._[_ROW].collect(),
        )
    )
    return groups.filter(groups.__quality_count__ > 1).order_by("__quality_first__")


# ---------------------------------------------------------------------------
# Rule 9 — partially overlapping links
# ---------------------------------------------------------------------------


class OverlappingLinksRule:
    """Flag pairs of links whose shapes run along each other for part of their length.

    Threshold keys:

    * ``min_overlap_units`` (default ``0.0``) — a pair is flagged when
      the length they share is greater than this, in the geometry's CRS
      units (degrees for WGS84; raise it to ignore digitising noise).

    A shared stretch is a 1-D intersection: links that only touch at a
    node or cross each other are not flagged. Pairs with identical shapes
    are skipped too, since that is how GMNS encodes the two directions
    of a two-way street; a link lying along part of a longer one *is*
    flagged. Emits one issue per pair, on the first link's row, with
    ``extra`` ``link_ids``, ``rows`` and ``overlap_length``.

    **Exact matching only.** The test is GEOS's exact DE-9IM relation with
    no snapping tolerance: only segments the two shapes share exactly are
    caught. Two digitisations of the same road a centimetre apart, or with
    vertices that don't line up on a common segment, are not flagged.
    ``min_overlap_units`` filters short exact overlaps; it does not widen
    the match.

    Shapes come from inline ``link.geometry`` when set, else from the
    ``geometry`` table through ``link.geometry_id`` (the first row of a
    repeated ``geometry_id``); links with neither are skipped. Requires
    shapely (clean extra). The engine selects just the link ids and
    shapes; candidate pairs come from an STRtree bounding-box query, are
    cut down in numpy, and only the survivors get the exact DE-9IM test
    (about 0.2 s on a 100k-link grid). The cost grows with how many
    bounding boxes overlap with real area, so it can get slow on long or
    diagonal links whose boxes cover much of the network (long rural
    links in WGS84, say).
    """

    code = "quality.overlapping_links"
    description = "Two links share part of their shape (partial overlap)."
    severity = Severity.WARNING

    def applies_to(self, package: Package) -> bool:
        """Needs shapely and a link shape source: inline ``geometry`` or ``geometry_id`` + a geometry table."""
        import importlib.util

        if importlib.util.find_spec("shapely") is None:
            return False
        return _link_shape_sources(package) is not None

    def run(self, package: Package, report: ValidationReport, rc: RuleConfig | None = None) -> None:
        """Index link shapes in an STRtree; emit one issue per pair sharing more than ``min_overlap_units``."""
        min_overlap = _threshold(rc, "min_overlap_units", 0.0)
        sources = _link_shape_sources(package)
        if sources is None:
            return
        shapes = _link_shapes(package, *sources)
        show_id = _id_display(package.tables["link"].expr, "link_id")
        for (row_a, raw_a), (row_b, raw_b), length in _overlapping_pairs(shapes, min_overlap):
            id_a, id_b = show_id(raw_a), show_id(raw_b)
            report.add_issue(
                Issue(
                    severity=self.severity,
                    category=Category.DATA_QUALITY,
                    code=self.code,
                    message=(
                        f"link row {row_a} (link_id={id_a!r}) and link row {row_b} (link_id={id_b!r}) "
                        f"share {length:.4g} units of their shape."
                    ),
                    table="link",
                    column="geometry",
                    row=row_a,
                    fix_hint=(
                        "If they are the same road, delete one; if one continues the other, split at the node "
                        "where they meet so each stretch is drawn once."
                    ),
                    extra={"link_ids": [id_a, id_b], "rows": [row_a, row_b], "overlap_length": length},
                )
            )


def _link_shape_sources(package: Package) -> tuple[bool, bool] | None:
    """``(inline, via_table)`` shape sources the link table has, or ``None`` when it has neither."""
    link = package.tables.get("link")
    if link is None:
        return None
    cols = set(link.columns())
    geometry = package.tables.get("geometry")
    inline = "geometry" in cols
    via_table = (
        "geometry_id" in cols and geometry is not None and {"geometry_id", "geometry"} <= set(geometry.columns())
    )
    if not (inline or via_table):
        return None
    return inline, via_table


def _link_shapes(package: Package, inline: bool, via_table: bool) -> Any:
    """Pyarrow table of (:data:`_ROW`, ``link_id``, ``inline``, ``table_shape``) for links with any shape."""
    link = _indexed(package.tables["link"])
    cols: dict[str, Any] = {_ROW: link[_ROW], "link_id": link.link_id}
    cols["inline"] = link.geometry if inline else ibis.null()
    if via_table:
        geometry = _indexed(package.tables["geometry"])
        # A repeated geometry_id would fan a link out into several rows (and pair it with itself): keep the first.
        first_of_id = geometry[_ROW].min().over(ibis.window(group_by=geometry.geometry_id))
        geometry = geometry.filter(geometry.geometry_id.notnull(), geometry[_ROW] == first_of_id)
        shapes = geometry.select(__quality_gid__=geometry.geometry_id, __quality_shape__=geometry.geometry)
        joined = link.left_join(shapes, link.geometry_id == shapes.__quality_gid__)
        cols = {_ROW: joined[_ROW], "link_id": joined.link_id}
        cols["inline"] = joined.geometry if inline else ibis.null()
        cols["table_shape"] = joined.__quality_shape__
        selected = joined.select(**cols)
    else:
        cols["table_shape"] = ibis.null()
        selected = link.select(**cols)
    return selected.filter(selected.inline.notnull() | selected.table_shape.notnull()).order_by(_ROW).to_pyarrow()


def _overlapping_pairs(shapes: Any, min_overlap: float) -> list[tuple[tuple[int, Any], tuple[int, Any], float]]:
    """``((row, id), (row, id), shared_length)`` for each link pair sharing more than ``min_overlap``, in row order."""
    import numpy as np
    import shapely

    if shapes.num_rows < 2:
        return []
    inline = _parse_geometries(shapes.column("inline").to_pylist())
    from_table = _parse_geometries(shapes.column("table_shape").to_pylist())
    geoms = np.where(np.equal(inline, None), from_table, inline)
    usable = np.flatnonzero(~np.equal(geoms, None))
    usable = usable[~shapely.is_empty(geoms[usable])]
    geoms = geoms[usable]
    # Bounding boxes that meet (each pair once), then two cheap cuts before the exact test. A shared stretch
    # has length, so the boxes' intersection must too: links that meet end to end at right angles (most
    # neighbours) share only a corner and drop out in numpy, before any GEOS call.
    a, b = shapely.STRtree(geoms).query(geoms)
    keep = a < b
    a, b = a[keep], b[keep]
    box = shapely.bounds(geoms)
    width = np.minimum(box[a, 2], box[b, 2]) - np.maximum(box[a, 0], box[b, 0])
    height = np.minimum(box[a, 3], box[b, 3]) - np.maximum(box[a, 1], box[b, 1])
    keep = np.maximum(width, height) > 0
    a, b = a[keep], b[keep]
    # DE-9IM "1********": the interiors share a 1-D part. Identical shapes are two-way twins, not overlaps.
    keep = shapely.relate_pattern(geoms[a], geoms[b], "1********") & ~shapely.equals(geoms[a], geoms[b])
    pairs = np.stack([a[keep], b[keep]], axis=1)
    if pairs.size == 0:
        return []
    lengths = shapely.length(shapely.intersection(geoms[pairs[:, 0]], geoms[pairs[:, 1]]))
    rows = shapes.column(_ROW).to_pylist()
    ids = shapes.column("link_id").to_pylist()
    out = []
    for (i, j), length in zip(pairs.tolist(), lengths.tolist(), strict=True):
        if length <= min_overlap:
            continue
        ri, rj = usable[i], usable[j]
        out.append(((rows[ri], ids[ri]), (rows[rj], ids[rj]), float(length)))
    out.sort(key=lambda p: (p[0][0], p[1][0]))
    return out


# ---------------------------------------------------------------------------
# Entry-point factory
# ---------------------------------------------------------------------------


def register_all() -> list[object]:
    """Construct + register every rule in this module.

    Wired as the ``corral.quality.rules`` entry point for the
    ``netstead`` distribution (see ``packages/netstead/pyproject.toml``).
    Also callable directly from tests / ad-hoc scripts: ``register_all()``
    returns the list of registered rule instances.
    """
    rules: list[object] = [
        HighSpeedResidentialRule(),
        DisconnectedComponentsRule(),
        LaneCountMismatchRule(),
        DuplicateNearNodesRule(),
        SharpAngleBendsRule(),
        ImplausibleVcRule(),
        MissingCriticalFieldsRule(),
        NonUniqueCombinationRule(),
        OverlappingLinksRule(),
    ]
    for r in rules:
        register_rule(r)  # type: ignore[arg-type]
    return rules
