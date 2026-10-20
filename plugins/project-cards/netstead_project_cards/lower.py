"""Lower a change onto core's edit planner and apply it through the Host: ``host.plan_*``, then ``host.mutate``.

A property change or an addition is one ``host.mutate`` (one pending edit, one undo step, all or nothing). A
deletion is one per table it deletes from (links, then nodes): core plans a node deletion against the network as it
is now, so the links that used a node must be gone before that plan is made. Call :func:`apply_change` from an
Action handler: if a later step fails, the handler's failure undoes the earlier ones. Core's planner refuses what
storage cannot hold (``EditRefused``, raised here as :class:`~netstead_project_cards.errors.CardError`); the live checks
report the rest as warnings (``host.edit_warnings``).

Each step reads the network's version before the frames it decides on (they are cached per version) and passes that
version to ``host.mutate``. An edit landing between the read and planning makes a plan stamped with another version,
which this module refuses with :class:`~netstead_project_cards.errors.CardError`; one landing between planning and the
mutation is refused by core's ``expected_version`` check in ``host.mutate``, which raises the Workbench's
``ActionError``. Either way the step is refused rather than applying a plan made from stale values.
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
from .mapping import load_mapping
from .types import (
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    incomplete,
)

__all__ = ["KEYS", "Applied", "Mark", "Pending", "apply_change", "describe", "few", "rows_by_key"]

#: The key column of each table a roadway change edits (GMNS names), from the mapping file's selectors.
KEYS = {table: load_mapping().selector(table)[0] for table in ("link", "node")}


@dataclass(frozen=True)
class Pending:
    """This plugin's pending edits on a network, read at one moment: their ``seqs``, and the newest seq ``issued``.

    Read from ``host.pending_edits`` (one atomic read under the session lock). A seq above ``issued`` belongs to an
    edit made after the read, which the read says nothing about: the routes reconcile outside the session lock, so a
    change can land between their read and their reconcile.
    """

    seqs: frozenset[int]
    issued: int

    def knows(self, seq: int) -> bool:
        """Whether this read can speak for the edit with this seq (it was handed out by the time of the read)."""
        return seq <= self.issued


@dataclass(frozen=True)
class Mark:
    """One ``host.mutate`` of a change: the pending edit's ``seq`` (its stable id) and its ledger note.

    The network holds the change while the seq is among its pending edits (``host.pending_edits``): ``host.undo``
    and a failed Action's rollback take it out, and core never hands the seq out again for that network id.
    """

    seq: int | None  # ``None`` only for a mutation with no edits, which no change makes (it never holds)
    note: str

    def holds(self, pending: Pending) -> bool:
        """Whether the network still has this mutation; one newer than the ``pending`` read is taken as held."""
        if self.seq is None:
            return False
        return not pending.knows(self.seq) or self.seq in pending.seqs

    def back(self, pending: Pending) -> bool:
        """Whether this undone mutation is pending again (its undo was rolled back), as far as ``pending`` knows."""
        return self.seq is not None and pending.knows(self.seq) and self.seq in pending.seqs


@dataclass
class Applied:
    """A change as applied: the ``host.mutate`` calls it took (their marks), and what happened.

    Each ``host.mutate`` is one pending edit. A deletion of links and nodes takes two (``steps == 2``), so undoing the
    change outside an Action takes ``steps`` calls to ``host.undo``.
    """

    change: NetworkChange
    marks: list[Mark] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def steps(self) -> int:
        """How many ``host.mutate`` calls (pending edits) the change took."""
        return len(self.marks)

    def summary(self) -> dict[str, Any]:
        """JSON-safe: the type, its description, steps, rows per table, warnings and what was skipped."""
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

    A property change whose selection has ``ignore_missing``, or a deletion with ``ignore_missing`` (a query
    selection's links), skips ids that are not in the network (listed in :attr:`Applied.skipped`); without it, a
    missing id refuses the change.

    Raises:
        CardNotSupported: a transit change, or an added record field with no column in the network.
        CardError: an incomplete change, a conflict with ``existing``, a missing record or column, a node still in
            use, a network that changed between reading it and planning, or anything core's planner refuses.
        ActionError: (the Workbench's, from ``host.mutate``) the network changed between planning and the mutation.
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

    Keys compare as text (an id typed ``"7"`` finds row 7); missing cells come back as ``None``, numpy scalars as
    Python ones, and list or array cells as they are. Uses only the frame's own methods, so the plugin needs no
    pandas import.
    """
    wanted = {str(i) for i in ids}
    keep = list(dict.fromkeys([pk, *columns]))
    sub = frame.loc[frame[pk].astype(str).isin(wanted), keep].astype(object)
    sub = sub.where(sub.notna(), None)
    return {str(_py(r[pk])): {c: _py(r[c]) for c in columns} for r in sub.to_dict("records")}


# ---------------------------------------------------------------- helpers


def _py(value: Any) -> Any:
    """A numpy scalar as a Python one; anything else (an array cell included) as it is."""
    return value.item() if hasattr(value, "item") and getattr(value, "ndim", 0) == 0 else value


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


def _mutate(host: Host, net_id: str, version: int, plans: list[EditPlan], note: str) -> Mark:
    """Apply the plans' edits in one ``host.mutate`` at ``version``, the one read before the frames that decided them.

    A plan stamped with another version was made against a network that changed after those reads: ``CardError``
    here. A network that changes after planning is refused by ``host.mutate`` itself (core's ``ActionError``).
    """
    if any(p.version != version for p in plans):
        raise CardError(f"{net_id} changed while the change was being planned; apply it again")
    edits = [e for p in plans for e in p.edits]
    return Mark(host.mutate(net_id, edits, note=note, expected_version=version).seq, note)


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
    wanted = list(dict.fromkeys(sel.target_ids()))
    current = rows_by_key(frame, pk, wanted, list(change.property_changes))
    missing = [i for i in wanted if str(i) not in current]
    if missing and not sel.ignore_missing:
        raise CardError(f"{sel.table} {few(missing)} not found")
    out = Applied(change)
    if missing:
        out.skipped.append(f"{sel.table} {few(missing)} not found; skipped (ignore_missing)")
    ids = [i for i in wanted if str(i) in current]
    if not ids:  # every id missing, all ignored: nothing to change
        return out
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
        out.marks.append(_mutate(host, net_id, version, plans, note))
        out.rows = {sel.table: len(ids)}
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
    version = host.network(net_id).version  # before the frame: it decides which nodes go
    links = _frame(host, net_id, "link")
    deleted = list(change.links)
    if deleted and change.ignore_missing:  # a query selection's links: skip the ones the network no longer has
        there = rows_by_key(links, "link_id", deleted, [])
        if missing := [i for i in deleted if str(i) not in there]:
            out.skipped.append(f"link {few(missing)} not found; skipped (ignore_missing)")
            deleted = [i for i in deleted if str(i) in there]
    two_steps = bool(deleted) and (bool(change.nodes) or change.clean_nodes)
    if in_use := _users(links, change.nodes, deleted):  # core would refuse it only at step 2, after step 1
        raise CardError(_in_use(in_use))
    nodes = list(change.nodes)
    if deleted and change.clean_nodes:
        rows = rows_by_key(links, "link_id", deleted, ["from_node_id", "to_node_id"])
        ends = list(
            dict.fromkeys(v for r in rows.values() for v in (r["from_node_id"], r["to_node_id"]) if v is not None)
        )
        used = _users(links, ends, deleted)
        taken = {str(n) for n in nodes}
        nodes += [e for e in ends if str(e) not in used and str(e) not in taken]
    if deleted:
        plan = _plan(host.plan_delete, net_id, "link", deleted)
        out.marks.append(_mutate(host, net_id, version, [plan], f"{note} (links)" if two_steps else note))
        _add_rows(out.rows, plan.rows)
    if nodes:
        version = host.network(net_id).version  # the network after step 1, which core plans the nodes against
        plan = _plan(host.plan_delete, net_id, "node", nodes)
        out.marks.append(_mutate(host, net_id, version, [plan], f"{note} (nodes)" if two_steps else note))
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
    out = Applied(change, marks=[_mutate(host, net_id, version, plans, note)])
    for plan in plans:
        _add_rows(out.rows, plan.rows)
    return out


_APPLIERS: dict[str, Callable[[Host, str, Any, str], Applied]] = {
    "roadway_property_change": _property_change,
    "roadway_deletion": _deletion,
    "roadway_addition": _addition,
}
