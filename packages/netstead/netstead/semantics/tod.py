"""Time-of-day (TOD) resolution for GMNS link / lane / segment tables.

GMNS TOD tables (``link_tod``, ``lane_tod``, ``segment_tod``,
``segment_lane_tod``, ``movement_tod``) carry per-time-period
overrides keyed by ``time_set_definitions.timeday_id``. For example, a
link's ``capacity`` may be 1800 by default but drop to 1500 during
weekday AM peak via a ``link_tod`` row with ``timeday_id =
"weekday_am_peak"`` and ``capacity = 1500``.

:func:`resolve_link_attrs_at` returns the link table with any matching
TOD overrides applied for the given ``time_set_id``.
:func:`tod_coverage` reports which time periods have data on each TOD
table — useful for auditing a fixture before running a TOD-aware op.

Like :mod:`netstead.semantics.geometry`, this materializes through
pyarrow rather than expressing the overlay in ibis. TOD tables are
typically small (10s to 1000s of rows per period) so the round-trip is
cheap, and the resulting code reads as a single top-to-bottom loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netstead.network import Network

__all__ = ["resolve_link_attrs_at", "tod_coverage"]

# Resource names of GMNS TOD tables, ordered roughly by usage frequency.
# Each is the GMNS resource name (NOT the Network attribute); we look
# them up by name via Package.tables[...] so attribute renames don't
# silently break TOD coverage.
_TOD_RESOURCES: tuple[str, ...] = (
    "link_tod",
    "lane_tod",
    "segment_tod",
    "segment_lane_tod",
    "movement_tod",
)


def resolve_link_attrs_at(net: Network, time_set_id: str | int | None = None) -> pa.Table:
    """Return the link table with TOD overrides applied for ``time_set_id``.

    Args:
        net: A loaded :class:`netstead.Network`.
        time_set_id: The ``timeday_id`` to look up in ``link_tod``.
            ``None`` returns the base link table unmodified.

    Returns:
        A :class:`pyarrow.Table` with the same columns as ``net.links``.
        Where a ``link_tod`` row exists for the given ``time_set_id``
        and a column, the override value replaces the base value;
        otherwise the base value passes through.

    The returned table is always materialized — callers downstream of
    TOD resolution typically need to consume the values immediately
    (e.g. for assignment, scoring, or report rendering).

    Examples:
        >>> from netstead import Network
        >>> from netstead.fixtures import leavenworth
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> from netstead.semantics import resolve_link_attrs_at
        >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
        >>> base = resolve_link_attrs_at(net, None)
        >>> base.column_names == [c for c in net.links.columns()]
        True
    """
    base = _to_arrow(net.links)
    if time_set_id is None or net.link_tod is None:
        return base

    tod = _to_arrow(net.link_tod)
    if "timeday_id" not in tod.column_names:
        return base

    # Filter TOD rows to the requested time period; keep only columns
    # that exist on the base link table (TOD tables otherwise carry
    # extra metadata like the TOD primary key).
    tod_rows = _filter_by(tod, "timeday_id", time_set_id)
    if tod_rows.num_rows == 0:
        return base

    overrides_by_link: dict = {}
    tod_link_ids = tod_rows.column("link_id").to_pylist()
    overridable_cols = [c for c in tod_rows.column_names if c in base.column_names and c != "link_id"]
    col_values = {c: tod_rows.column(c).to_pylist() for c in overridable_cols}

    for i, link_id in enumerate(tod_link_ids):
        if link_id is None:
            continue
        # Last-writer-wins per link if the TOD table has dupes — log
        # would be noise; the spec disallows duplicates per (link_id,
        # timeday_id) so this is a defensive choice.
        per_link = overrides_by_link.setdefault(link_id, {})
        for col in overridable_cols:
            val = col_values[col][i]
            if val is not None:
                per_link[col] = val

    if not overrides_by_link:
        return base

    # Apply overrides column by column. We pylist + rebuild rather than
    # in-place pyarrow mutation (which isn't supported) — the cost is
    # one O(rows) pass per overridden column.
    base_link_ids = base.column("link_id").to_pylist()
    out_columns: dict[str, pa.Array] = {}
    for col_name in base.column_names:
        base_values = base.column(col_name).to_pylist()
        if col_name in overridable_cols:
            for i, lid in enumerate(base_link_ids):
                ov = overrides_by_link.get(lid, {}).get(col_name)
                if ov is not None:
                    base_values[i] = ov
        out_columns[col_name] = pa.array(base_values, type=base.column(col_name).type)

    return pa.table(out_columns)


def tod_coverage(net: Network) -> dict[str, list]:
    """Return ``{tod_table_name: [timeday_ids present]}`` for each TOD table.

    Useful for auditing: ``tod_coverage(net)["link_tod"]`` tells you
    which periods have ``link_tod`` overrides at all, which is the
    first thing you check before calling :func:`resolve_link_attrs_at`
    in a loop over time periods.

    Tables without a ``timeday_id`` column (or absent entirely) are
    omitted from the result. Empty TOD tables map to an empty list.

    Examples:
        >>> from netstead import Network
        >>> from netstead.fixtures import leavenworth
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> from netstead.semantics import tod_coverage
        >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
        >>> coverage = tod_coverage(net)
        >>> "link_tod" in coverage  # Leavenworth has one link_tod row.
        True
    """
    out: dict[str, list] = {}
    for resource_name in _TOD_RESOURCES:
        table = net.tables.get(resource_name)
        if table is None:
            continue
        arrow = _to_arrow(table)
        if "timeday_id" not in arrow.column_names:
            continue
        # Stable order (sorted) so doctests + snapshot tests are stable.
        ids = sorted({i for i in arrow.column("timeday_id").to_pylist() if i is not None})
        out[resource_name] = ids
    return out


def _filter_by(arrow: pa.Table, column: str, value) -> pa.Table:
    """Return rows where ``arrow[column] == value``.

    Mini helper kept inline (rather than imported from a util module)
    so the TOD overlay code reads top-to-bottom in one file.
    """
    mask = pa.compute.equal(arrow.column(column), pa.scalar(value, type=arrow.column(column).type))
    return arrow.filter(mask)


def _to_arrow(table) -> pa.Table:
    """Materialize a :class:`~corral.dataset.Table` to pyarrow."""
    return pa.Table.from_pandas(table.to_pandas(), preserve_index=False)
