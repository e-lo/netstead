"""Lower a change onto core's edit planner and apply it through the Host: ``host.plan_*``, then ``host.mutate``.

A property change or an addition is one ``host.mutate`` (one pending edit, one undo step, all or nothing). A
deletion is one per table it deletes from (links, then nodes): core plans a node deletion against the network as it
is now, so the links that used a node must be gone before that plan is made. Call :func:`apply_change` from an
Action handler: if a later step fails, the handler's failure undoes the earlier ones. Core's planner refuses what
storage cannot hold (``EditRefused``, raised here as :class:`~netstead_project_cards.errors.CardError`); the live checks
report the rest as warnings (``host.edit_warnings``).

Each step reads the network's version before the frames it decides on (they are cached per version), refuses plans
made against any other version, and passes that version to ``host.mutate``: an edit landing anywhere between the read
and the mutation refuses the step rather than applying a plan made from stale values.
"""

from __future__ import annotations

import math
import numbers
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from netstead.workbench.plugins import EditPlan, EditRefused, Host

from .errors import CardError, CardNotSupported, not_supported
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
    """A change as applied: how many ``host.mutate`` calls it took, and what happened.

    Each ``host.mutate`` is one pending edit. A deletion of links and nodes takes two (``steps == 2``), so undoing the
    change outside an Action takes ``steps`` calls to ``host.undo``.
    """

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

    Everything this module can check is checked before the first ``host.mutate``, including a deleted node that links
    this change keeps still use. A deletion of links and nodes is still two mutations, though: if the second fails for
    any other reason (core refuses its plan, or the network changed in between), the first stays applied unless the
    calling Action's failure rolls it back. Call it from an Action handler.

    Raises:
        CardNotSupported: a transit change, or an added record field with no column in the network.
        CardError: an incomplete change, a conflict with ``existing``, a missing record or column, a node still in
            use, a network that changed while the change was planned, or anything core's planner refuses.
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
        clean = " (and unused end nodes)" if change.clean_nodes and change.links else ""
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
    if pc.set is None:
        delta = f"{pc.change:+g}"
        return f"{delta} (was {pc.existing})" if pc.existing is not None else delta
    return f"{pc.existing} → {pc.set}" if pc.existing is not None else f"→ {pc.set}"


def _frame(host: Host, net_id: str, table: str) -> Any:
    h = host.network(net_id)
    return h.links_df() if table == "link" else h.nodes_df()


def _plan(fn: Callable[..., EditPlan], *args: Any) -> EditPlan:
    try:
        return fn(*args)
    except EditRefused as exc:
        raise CardError(str(exc)) from exc


def _mutate(host: Host, net_id: str, version: int, plans: list[EditPlan], note: str) -> None:
    """Apply the plans' edits in one ``host.mutate``, refused unless every plan, and the network, is at ``version``.

    ``version`` is the one read before the frames that decided what to plan; a plan stamped otherwise was made
    against a network that changed after those reads.
    """
    if any(p.version != version for p in plans):
        raise CardError(f"{net_id} changed while the change was being planned; apply it again")
    edits = [e for p in plans for e in p.edits]
    host.mutate(net_id, edits, note=note, expected_version=version)


def _same(current: Any, existing: Any) -> bool:
    """Whether a cell holding ``current`` matches a card's ``existing``: text as text, anything else as a number."""
    if current is None:
        return False
    if isinstance(current, str):
        return current == str(existing)
    try:
        return math.isclose(float(current), float(existing), rel_tol=1e-9, abs_tol=1e-9)  # True matches 1
    except (TypeError, ValueError):
        return str(current) == str(existing)


def _groups(
    pc: PropertyChange, ids: list[Any], current: dict[str, dict[str, Any]], table: str, prop: str
) -> dict[Any, list[Any]]:
    """New value -> the ids that get it: one group for ``set``; one per distinct current value for ``change``."""
    if pc.set is not None:
        return {pc.set: list(ids)}
    groups: dict[Any, list[Any]] = defaultdict(list)
    for i in ids:
        now = current[str(i)][prop]
        if now is None:
            raise CardError(f"{prop} is empty on {table} {i}: set it rather than changing it by {pc.change:+g}")
        if isinstance(now, bool) or not isinstance(now, numbers.Number):
            raise CardError(f"{prop} holds {type(now).__name__} values: only numbers change by a delta")
        try:
            new = now + pc.change
        except TypeError:  # a Decimal plus a float delta: add the delta as the cell's own type
            new = now + type(now)(str(pc.change))
        groups[int(new) if isinstance(now, int) and float(new).is_integer() else new].append(i)
    return dict(groups)


# ---------------------------------------------------------------- appliers


def _property_change(host: Host, net_id: str, change: RoadwayPropertyChange, note: str) -> Applied:
    sel = change.facility
    pk = KEYS[sel.table]
    version = host.network(net_id).version  # before the frame: it decides the conflicts and the deltas
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
        for value, group in _groups(pc, ids, current, sel.table, prop).items():
            plans.append(_plan(host.plan_update, net_id, sel.table, group, {prop: value}))
    if plans:
        _mutate(host, net_id, version, plans, note)
        out.steps, out.rows = 1, {sel.table: len(ids)}
    return out


def _add_rows(into: dict[str, int], rows: dict[str, int]) -> None:
    for table, n in rows.items():
        into[table] = into.get(table, 0) + n


def _users(links: Any, nodes: Sequence[Any], deleted: Sequence[Any]) -> dict[str, list[Any]]:
    """``{str(node): [link ids]}``: the links in the ``links`` frame, other than ``deleted``, at each of ``nodes``.

    A link counts when it starts or ends at the node; a node no remaining link uses is absent.
    """
    wanted, gone = {str(n) for n in nodes}, {str(i) for i in deleted}
    ends = links["from_node_id"].astype(str), links["to_node_id"].astype(str)
    hit = (ends[0].isin(wanted) | ends[1].isin(wanted)) & ~links["link_id"].astype(str).isin(gone)
    users: dict[str, list[Any]] = defaultdict(list)
    for r in links.loc[hit, ["link_id", "from_node_id", "to_node_id"]].to_dict("records"):
        for end in dict.fromkeys(str(_py(r[c])) for c in ("from_node_id", "to_node_id")):  # a self-loop counts once
            if end in wanted:
                users[end].append(_py(r["link_id"]))
    return dict(users)


def _deletion(host: Host, net_id: str, change: RoadwayDeletion, note: str) -> Applied:
    """Links, then nodes. Which nodes go (and whether any is still in use) is decided before the first mutation."""
    out = Applied(change)
    two_steps = bool(change.links) and (bool(change.nodes) or change.clean_nodes)
    version = host.network(net_id).version  # before the frame: it decides which nodes go
    links = _frame(host, net_id, "link")
    if in_use := _users(links, change.nodes, change.links):  # core would refuse it only at step 2, after step 1
        raise CardError(_in_use(in_use))
    nodes = list(change.nodes)
    if change.links and change.clean_nodes:
        rows = rows_by_key(links, "link_id", change.links, ["from_node_id", "to_node_id"])
        ends = list(
            dict.fromkeys(v for r in rows.values() for v in (r["from_node_id"], r["to_node_id"]) if v is not None)
        )
        used = _users(links, ends, change.links)
        taken = {str(n) for n in nodes}
        nodes += [e for e in ends if str(e) not in used and str(e) not in taken]
    if change.links:
        plan = _plan(host.plan_delete, net_id, "link", change.links)
        _mutate(host, net_id, version, [plan], f"{note} (links)" if two_steps else note)
        out.steps += 1
        _add_rows(out.rows, plan.rows)
    if nodes:
        version = host.network(net_id).version  # the network after step 1, which core plans the nodes against
        plan = _plan(host.plan_delete, net_id, "node", nodes)
        _mutate(host, net_id, version, [plan], f"{note} (nodes)" if two_steps else note)
        out.steps += 1
        _add_rows(out.rows, plan.rows)
    return out


def _in_use(users: dict[str, list[Any]]) -> str:
    """``node 1 is still used by links 3, 4, ..., which this change does not delete; add them or drop the node``."""
    used = "; ".join(
        f"node {n} is still used by link{'s' if len(ids) > 1 else ''} {few(ids)}" for n, ids in users.items()
    )
    one_link = sum(len(ids) for ids in users.values()) == 1
    nodes = "node" if len(users) == 1 else "nodes"
    return f"{used}, which this change does not delete; add {'it' if one_link else 'them'} or drop the {nodes}"


def _addition(host: Host, net_id: str, change: RoadwayAddition, note: str) -> Applied:
    """Nodes, then links, in one mutation; ids, end nodes and record fields are checked before it."""
    version = host.network(net_id).version  # before the frames: they decide which ids are free
    nodes_frame = _frame(host, net_id, "node")
    frames = {"node": nodes_frame, "link": _frame(host, net_id, "link")}
    for name, records in (("node", change.nodes), ("link", change.links)):
        columns = set(frames[name].columns)
        for field_name in dict.fromkeys(k for r in records for k in r):
            if field_name not in columns:  # a foreign card's flag (walk_access, bus_only): nothing to store it in
                raise CardNotSupported(f"{name} record field {field_name!r} has no column in this network")
        pk = KEYS[name]
        ids = [r[pk] for r in records]
        if len({str(i) for i in ids}) != len(ids):
            raise CardError(f"{name} ids repeat within the change")
        found = rows_by_key(frames[name], pk, ids, [])
        if taken := [i for i in ids if str(i) in found]:
            raise CardError(f"{name} {few(taken)} {'already exists' if len(taken) == 1 else 'already exist'}")
    if change.links:
        ends = list(dict.fromkeys(r[c] for r in change.links for c in ("from_node_id", "to_node_id")))
        present = set(rows_by_key(nodes_frame, "node_id", ends, [])) | {str(r["node_id"]) for r in change.nodes}
        missing = [e for e in ends if str(e) not in present]
        if missing:  # core would only warn; Wrangler fails such a card, so refuse before anything is applied
            raise CardError(
                f"added links use node(s) {few(missing)}, which are not in the network; add them in the same change"
            )
    plans = []
    if change.nodes:  # nodes first: the links need their ends
        plans.append(_plan(host.plan_add, net_id, "node", change.nodes))
    if change.links:
        plans.append(_plan(host.plan_add, net_id, "link", change.links))
    _mutate(host, net_id, version, plans, note)
    out = Applied(change, steps=1)
    for plan in plans:
        _add_rows(out.rows, plan.rows)
    return out


_APPLIERS: dict[str, Callable[[Host, str, Any, str], Applied]] = {
    "roadway_property_change": _property_change,
    "roadway_deletion": _deletion,
    "roadway_addition": _addition,
}
