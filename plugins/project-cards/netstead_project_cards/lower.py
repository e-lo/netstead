"""Lower a change onto core's edit planner and apply it through the Host: ``host.plan_*``, then ``host.mutate``.

A property change or an addition is one ``host.mutate`` (one pending edit, one undo step, all or nothing). A
deletion is one per table it deletes from (links, then nodes): core plans a node deletion against the network as it
is now, so the links that used a node must be gone before that plan is made. Call :func:`apply_change` from an
Action handler: if a later step fails, the handler's failure undoes the earlier ones. Core's planner refuses what
storage cannot hold (``EditRefused``, raised here as :class:`~netstead_project_cards.errors.CardError`); the live checks
report the rest as warnings (``host.edit_warnings``).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from netstead.workbench.plugins import EditPlan, EditRefused, Host

from .errors import CardError, not_supported
from .types import (
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    incomplete,
)

__all__ = ["KEYS", "Applied", "apply_change", "describe", "few", "rows_by_key"]

#: The key column of each table a roadway change edits (GMNS names).
KEYS = {"link": "link_id", "node": "node_id"}


@dataclass
class Applied:
    """A change as applied: how many ``host.mutate`` calls it took (undone together), and what happened."""

    change: NetworkChange
    steps: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """JSON-safe: the type, its description, steps, rows per table, warnings and skipped properties."""
        return {
            "type": self.change.type,
            "describe": describe(self.change),
            "steps": self.steps,
            "rows": dict(self.rows),
            "warnings": list(self.warnings),
            "skipped": list(self.skipped),
        }


def apply_change(host: Host, net_id: str, change: NetworkChange) -> Applied:
    """Plan ``change`` with core and apply it to network ``net_id``; the lineage gets ``"project_cards: <describe>"``.

    Raises:
        CardNotSupported: a transit change.
        CardError: an incomplete change, a conflict with ``existing``, a missing record or column, or anything
            core's planner refuses.
    """
    if change.type in TRANSIT_TYPES:
        raise not_supported(change.type)
    if problem := incomplete(change):
        raise CardError(problem)
    applier = _APPLIERS[change.type]
    return applier(host, net_id, change, describe(change))


def describe(change: Any) -> str:
    """A short description of ``change``, for the lineage, the Edits panel and the draft card's list."""
    if isinstance(change, RoadwayPropertyChange):
        sel = change.facility
        target = f"{sel.table} {few(sel.target_ids())}" if sel is not None else "the selection"
        parts = [f"{name} {_what(pc)}" for name, pc in change.property_changes.items()]
        return f"roadway_property_change {target}: {', '.join(parts)}"
    if isinstance(change, RoadwayDeletion):
        bits = [f"link {few(change.links)}"] if change.links else []
        bits += [f"node {few(change.nodes)}"] if change.nodes else []
        clean = " (and unused end nodes)" if change.clean_nodes else ""
        return f"roadway_deletion {' and '.join(bits) or 'the selection'}{clean}"
    if isinstance(change, RoadwayAddition):
        bits = [f"link {few([r['link_id'] for r in change.links])}"] if change.links else []
        bits += [f"node {few([r['node_id'] for r in change.nodes])}"] if change.nodes else []
        return f"roadway_addition {' and '.join(bits)}"
    return change.type


def few(ids: Sequence[Any], n: int = 5) -> str:
    """The first ``n`` ids, and how many more.

    >>> few([1, 2, 3, 4, 5, 6, 7])
    '1, 2, 3, 4, 5 (+2 more)'
    """
    items = list(ids)
    shown = ", ".join(str(i) for i in items[:n])
    return shown + (f" (+{len(items) - n} more)" if len(items) > n else "")


def rows_by_key(frame: Any, pk: str, ids: Sequence[Any], columns: Sequence[str]) -> dict[str, dict[str, Any]]:
    """``{str(key): {column: value}}`` for the rows of a pandas ``frame`` whose ``pk`` is in ``ids``.

    Keys compare as text (an id typed ``"7"`` finds row 7); missing cells come back as ``None`` and numpy scalars as
    Python ones. Uses only the frame's own methods, so the plugin needs no pandas import.
    """
    wanted = {str(i) for i in ids}
    keep = list(dict.fromkeys([pk, *columns]))
    sub = frame.loc[frame[pk].astype(str).isin(wanted), keep].astype(object)
    sub = sub.where(sub.notna(), None)
    return {str(_py(r[pk])): {c: _py(r[c]) for c in columns} for r in sub.to_dict("records")}


# ---------------------------------------------------------------- helpers


def _py(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _what(pc: PropertyChange) -> str:
    new = f"{pc.set}" if pc.set is not None else f"{pc.change:+g}"
    return f"{pc.existing} → {new}" if pc.existing is not None else (f"→ {new}" if pc.set is not None else new)


def _frame(host: Host, net_id: str, table: str) -> Any:
    h = host.network(net_id)
    return h.links_df() if table == "link" else h.nodes_df()


def _plan(fn: Callable[..., EditPlan], *args: Any) -> EditPlan:
    try:
        return fn(*args)
    except EditRefused as exc:
        raise CardError(str(exc)) from exc


def _mutate(host: Host, net_id: str, plans: list[EditPlan], note: str) -> None:
    """Apply the plans' edits in one ``host.mutate``, refused if the network moved since the first plan."""
    edits = [e for p in plans for e in p.edits]
    host.mutate(net_id, edits, note=note, expected_version=plans[0].version)


def _same(current: Any, existing: Any) -> bool:
    if current is None:
        return False
    try:
        return math.isclose(float(current), float(existing), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(current) == str(existing)


def _groups(pc: PropertyChange, ids: list[Any], current: dict[str, dict[str, Any]], prop: str) -> dict[Any, list[Any]]:
    """New value -> the ids that get it: one group for ``set``; one per distinct current value for ``change``."""
    if pc.set is not None:
        return {pc.set: list(ids)}
    groups: dict[Any, list[Any]] = defaultdict(list)
    for i in ids:
        now = current[str(i)][prop]
        if now is None:
            raise CardError(f"{prop} on {i} is empty: set it rather than changing it by {pc.change:+g}")
        if isinstance(now, bool) or not isinstance(now, int | float):
            raise CardError(f"{prop} holds {type(now).__name__} values: only numbers change by a delta")
        new = now + pc.change
        groups[int(new) if isinstance(now, int) and float(new).is_integer() else new].append(i)
    return dict(groups)


# ---------------------------------------------------------------- appliers


def _property_change(host: Host, net_id: str, change: RoadwayPropertyChange, note: str) -> Applied:
    sel = change.facility
    pk = KEYS[sel.table]
    frame = _frame(host, net_id, sel.table)
    unknown = [p for p in change.property_changes if p not in frame.columns]
    if unknown:
        raise CardError(f"{sel.table} has no column(s) {', '.join(unknown)}")
    ids = list(dict.fromkeys(sel.target_ids()))
    current = rows_by_key(frame, pk, ids, list(change.property_changes))
    missing = [i for i in ids if str(i) not in current]
    if missing:
        raise CardError(f"{sel.table} {few(missing)} not found")
    out = Applied(change)
    plans: list[EditPlan] = []
    for prop, pc in change.property_changes.items():
        if pc.existing is not None:
            bad = [i for i in ids if not _same(current[str(i)][prop], pc.existing)]
            if bad:
                found = current[str(bad[0])][prop]
                message = f"{prop}: expected {pc.existing!r} on {sel.table} {few(bad)}, found {found!r}"
                if pc.existing_value_conflict == "error":
                    raise CardError(f"conflict: {message}")
                if pc.existing_value_conflict == "skip":
                    out.skipped.append(message)
                    continue
                out.warnings.append(message)
        for value, group in _groups(pc, ids, current, prop).items():
            plans.append(_plan(host.plan_update, net_id, sel.table, group, {prop: value}))
    if plans:
        _mutate(host, net_id, plans, note)
        out.steps, out.rows = 1, {sel.table: len(ids)}
    return out


_APPLIERS: dict[str, Callable[[Host, str, Any, str], Applied]] = {
    "roadway_property_change": _property_change,
}
