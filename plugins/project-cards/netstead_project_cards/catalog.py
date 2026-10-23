"""A catalog: the ProjectCard files in a folder, and whether each applies to the open network as it is now.

:func:`scan_catalog` lists the card files (``*.yml``, ``*.yaml``, ``*.json``) of a folder and one level of its
sub-folders through a listing the caller supplies (``host.list_dir``, which stays inside ``io.allowed_roots`` and
skips hidden entries) and reads each with the tolerant reader; a file the reader rejects is reported, not raised.
:func:`applicability` resolves each change's targets against the link and node tables **without mutating**: an id
selection checks its ids exist; a query selection uses the provenance ids the card recorded (``netstead.resolved[i]``)
and, without them, reports :data:`~netstead_project_cards.types.UNRESOLVED_QUERY`. Both are pure: the handlers and the
routes feed them a Host.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from .card import DraftCard
from .errors import CardError
from .lower import KEYS, describe, existing_mismatches, few, rows_by_key
from .types import (
    TRANSIT_TYPES,
    UNRESOLVED_QUERY,
    NetworkChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    incomplete,
)

__all__ = [
    "CARD_SUFFIXES",
    "Catalog",
    "CatalogEntry",
    "applicability",
    "card_targets",
    "fit",
    "scan_catalog",
    "with_options",
]

#: The file suffixes a catalog folder's cards may have (lower-cased).
CARD_SUFFIXES = frozenset({".yml", ".yaml", ".json"})


@dataclass
class CatalogEntry:
    """One card of a catalog: its id (the path relative to the catalog folder), what the file says, and its check.

    ``applicability`` is ``None`` until ``project_cards.check_card`` (or the ``/catalog`` route) computes it; it is
    the dict :func:`applicability` returns, plus the ``net_id`` and ``version`` of the network it was computed on.
    The parsed ``card`` is kept for the handlers and is not part of :meth:`to_dict`.
    """

    card_id: str
    path: str
    project: str
    tags: list[str]
    change_count: int
    card: DraftCard = field(repr=False)
    applicability: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for ``state()`` and the ``/catalog`` route."""
        return {
            "card_id": self.card_id,
            "path": self.path,
            "project": self.project,
            "tags": list(self.tags),
            "change_count": self.change_count,
            "applicability": self.applicability,
            "fit": fit(self.applicability),
        }


@dataclass
class Catalog:
    """An open catalog folder: its cards (by id) and the files that were not cards."""

    path: str
    cards: list[CatalogEntry]
    rejected: list[dict[str, Any]]  # each ``{card_id, path, error}``

    def entry(self, card_id: str) -> CatalogEntry | None:
        """The card with this id (``None`` when the catalog has none)."""
        return next((c for c in self.cards if c.card_id == card_id), None)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for ``state()`` and the ``/catalog`` route."""
        return {
            "path": self.path,
            "cards": [c.to_dict() for c in self.cards],
            "rejected": [dict(r) for r in self.rejected],
        }


def _card_files(folder: str, list_dir: Callable[[str], list[dict[str, Any]]]) -> list[tuple[str, str]]:
    """``(card_id, path)`` for each card file in ``folder`` and one level of sub-folders (hidden entries skipped)."""
    found = []
    for entry in list_dir(folder):
        if entry["is_dir"]:
            found.extend(
                (f"{entry['name']}/{child['name']}", child["path"])
                for child in list_dir(entry["path"])
                if not child["is_dir"] and PurePath(child["name"]).suffix.lower() in CARD_SUFFIXES
            )
        elif PurePath(entry["name"]).suffix.lower() in CARD_SUFFIXES:
            found.append((entry["name"], entry["path"]))
    return found


def scan_catalog(
    folder: str, list_dir: Callable[[str], list[dict[str, Any]]], read: Callable[[str], DraftCard]
) -> Catalog:
    """Read every card file of ``folder`` (and one level of sub-folders): the cards, and the files that were not.

    ``list_dir(path)`` lists a folder as ``host.list_dir`` does (``{name, path, is_dir, ...}``); ``read(path)`` is
    the tolerant reader (``read_card_file`` with the mapping). A file ``read`` refuses (a ``CardError``, or an
    ``OSError``: unreadable) goes to ``rejected`` with its error; nothing is raised for it. Card ids are the paths
    relative to ``folder``, with ``/`` (``sub/card.yml``), in listing order (folders first, then names).
    """
    cards: list[CatalogEntry] = []
    rejected: list[dict[str, Any]] = []
    for card_id, path in _card_files(folder, list_dir):
        try:
            card = read(path)
        except (CardError, OSError) as exc:
            rejected.append({"card_id": card_id, "path": path, "error": str(exc)})
            continue
        cards.append(CatalogEntry(card_id, path, card.project, list(card.tags), len(card.changes), card))
    return Catalog(folder, cards, rejected)


def card_targets(card: DraftCard) -> dict[str, list[Any]]:
    """The ids the card's changes aim at, by table (``links``, ``nodes``), for highlighting; additions excluded."""
    links: list[Any] = []
    nodes: list[Any] = []
    for change in card.changes:
        if isinstance(change, RoadwayPropertyChange) and change.facility is not None:
            (links if change.facility.table == "link" else nodes).extend(change.facility.target_ids())
        elif isinstance(change, RoadwayDeletion):
            links.extend(change.links)
            nodes.extend(change.nodes)
    return {"links": list(dict.fromkeys(links)), "nodes": list(dict.fromkeys(nodes))}


def with_options(
    card: DraftCard, *, existing_value_conflict: str | None = None, ignore_missing: bool | None = None
) -> list[NetworkChange]:
    """The card's changes with ``project_cards.apply_card``'s options applied (``None``: as the card says).

    ``ignore_missing`` sets every selection's flag (a property change's facility, a deletion's links; nodes are
    always aimed at specific records). ``existing_value_conflict`` sets every property's. The card is not changed.
    """
    out: list[NetworkChange] = []
    for change in card.changes:
        if isinstance(change, RoadwayPropertyChange):
            update: dict[str, Any] = {}
            if ignore_missing is not None and change.facility is not None:
                update["facility"] = change.facility.model_copy(update={"ignore_missing": ignore_missing})
            if existing_value_conflict is not None:
                update["property_changes"] = {
                    name: pc.model_copy(update={"existing_value_conflict": existing_value_conflict})
                    for name, pc in change.property_changes.items()
                }
            change = change.model_copy(update=update) if update else change
        elif isinstance(change, RoadwayDeletion) and ignore_missing is not None:
            change = change.model_copy(update={"ignore_missing": ignore_missing})
        out.append(change)
    return out


def applicability(card: DraftCard, links: Any, nodes: Any) -> dict[str, Any]:
    """Whether ``card`` applies to a network whose link and node tables are ``links`` and ``nodes`` (pandas frames).

    Returns ``{ok, changes: [{describe, resolved, missing, reason, warnings?}]}``: per change, how many of its target
    records the network has (``resolved``), the ids it lacks (``missing``), and ``reason`` when the change cannot be
    applied at all (a property whose ``existing`` differs from the network's value under ``existing_value_conflict:
    error``, a transit change, an incomplete one, a query without provenance, an added record whose id is taken, or
    an added link whose end node is absent). ``ok`` is false when any change has a reason, or lacks an id its
    selection does not ignore (``ignore_missing``): the lowering would refuse it. ``warnings`` appears when a
    conflict is only warned about or skipped. Missing ids a selection ignores are listed but do not make the card
    inapplicable (they would be skipped). Nothing is mutated.
    """
    results = [_check(change, links, nodes) for change in card.changes]
    oks = [r.pop("_ok") for r in results]
    return {"ok": all(oks), "changes": results}


def fit(applicability: dict[str, Any] | None) -> dict[str, Any]:
    """Whether a card fits the network, as the catalog's status pill shows it: ``{state, label, reasons, warnings}``.

    ``state`` is ``fits``, ``no`` or ``unchecked``. ``reasons`` (for ``no``) are short, in order: the card's own
    reason ("already applied ..."), then each change's ("conflict: ...", "missing link 999999", ...). ``warnings``
    are the conflicts only warned about or skipped. ``net_id`` is the network it was checked on.

    >>> fit(None)
    {'state': 'unchecked', 'label': 'Not checked', 'reasons': [], 'warnings': [], 'net_id': None}
    >>> fit({"ok": False, "net_id": "n", "changes": [{"reason": None, "lacks": ["link 9"]}]})["reasons"]
    ['missing link 9']
    """
    if applicability is None:
        return {"state": "unchecked", "label": "Not checked", "reasons": [], "warnings": [], "net_id": None}
    changes = applicability.get("changes", [])
    warnings = [w for c in changes for w in c.get("warnings", [])]
    net = applicability.get("net_id")
    if applicability.get("ok"):
        return {"state": "fits", "label": "Fits this network", "reasons": [], "warnings": warnings, "net_id": net}
    reasons = [applicability["reason"]] if applicability.get("reason") else []
    for c in changes:
        if c.get("reason"):
            reasons.append(c["reason"])
        elif c.get("lacks"):
            reasons.append(f"missing {'; '.join(c['lacks'])}")
    return {
        "state": "no",
        "label": "Doesn't fit",
        "reasons": reasons or ["does not apply"],
        "warnings": warnings,
        "net_id": net,
    }


def _check(change: NetworkChange, links: Any, nodes: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"describe": describe(change), "resolved": 0, "missing": [], "reason": None, "_ok": True}
    if change.type in TRANSIT_TYPES:
        return _refuse(out, "not yet supported: transit changes arrive with the transit component (P6)")
    if problem := incomplete(change):
        return _refuse(out, problem)
    if isinstance(change, RoadwayPropertyChange):
        sel = change.facility
        if sel is None or sel.unresolved:
            return _refuse(out, UNRESOLVED_QUERY)
        frame = links if sel.table == "link" else nodes
        _resolve(out, frame, sel.table, sel.target_ids(), ignore_missing=sel.ignore_missing)
        return _existing(out, change, sel.table, frame, list(dict.fromkeys(sel.target_ids())))
    if isinstance(change, RoadwayDeletion):
        _resolve(out, links, "link", list(change.links), ignore_missing=change.ignore_missing)
        return _resolve(out, nodes, "node", list(change.nodes))  # nodes are always aimed at specific records
    if isinstance(change, RoadwayAddition):
        return _addition(out, change, links, nodes)
    return _refuse(out, f"{change.type} cannot be checked in this version")  # a type added after this module


def _existing(
    out: dict[str, Any], change: RoadwayPropertyChange, table: str, frame: Any, wanted: list[Any]
) -> dict[str, Any]:
    """Compare each property's ``existing`` with the target rows' current values, as applying the change would.

    ``existing_value_conflict`` decides: ``error`` makes the change not ok (a ``conflict:`` reason), ``warn`` adds a
    warning, ``skip`` adds a note that the property will be skipped.
    """
    props = {p: pc for p, pc in change.property_changes.items() if pc.existing is not None}
    if not props:
        return out
    unknown = [p for p in props if p not in frame.columns]
    if unknown:
        return _refuse(out, f"{table} has no column(s) {', '.join(unknown)}")
    current = rows_by_key(frame, KEYS[table], wanted, list(props))
    ids = [i for i in wanted if str(i) in current]
    errors: list[str] = []
    for prop, pc in props.items():
        bad = existing_mismatches(pc, ids, current, prop)
        if not bad:
            continue
        found = current[str(bad[0])][prop]
        message = f"{prop}: expected {pc.existing}, found {found} on {len(bad)} of {len(ids)} {table}s"
        if pc.existing_value_conflict == "error":
            errors.append(message)
        elif pc.existing_value_conflict == "skip":
            out.setdefault("warnings", []).append(f"{message}; {prop} will be skipped")
        else:
            out.setdefault("warnings", []).append(message)
    if errors:
        return _refuse(out, f"conflict: {'; '.join(errors)}")
    return out


def _refuse(out: dict[str, Any], reason: str) -> dict[str, Any]:
    out["reason"], out["_ok"] = reason, False
    return out


def _resolve(
    out: dict[str, Any], frame: Any, table: str, ids: list[Any], *, ignore_missing: bool = False
) -> dict[str, Any]:
    """Count the ids ``frame`` has and list the ones it lacks; without ``ignore_missing`` a lack makes it not ok."""
    wanted = list(dict.fromkeys(ids))
    found = rows_by_key(frame, KEYS[table], wanted, []) if wanted else {}
    missing = [i for i in wanted if str(i) not in found]
    out["resolved"] += len(wanted) - len(missing)
    out["missing"] = [*out["missing"], *missing]
    if missing and not ignore_missing:
        out["_ok"] = False
        out.setdefault("lacks", []).append(f"{table} {few(missing)}")  # what refuses it, for the fit's reasons
    return out


def _addition(out: dict[str, Any], change: RoadwayAddition, links: Any, nodes: Any) -> dict[str, Any]:
    """An addition resolves its records; an id the network already has, or a link end it lacks, is a reason."""
    for table, records in (("node", change.nodes), ("link", change.links)):
        pk = KEYS[table]
        ids = [r[pk] for r in records]
        taken = rows_by_key(links if table == "link" else nodes, pk, ids, []) if ids else {}
        if taken:
            return _refuse(out, f"{table} {few([i for i in ids if str(i) in taken])} already exists")
        out["resolved"] += len(ids)
    ends = list(dict.fromkeys(r[c] for r in change.links for c in ("from_node_id", "to_node_id")))
    present = set(rows_by_key(nodes, KEYS["node"], ends, []) if ends else {}) | {
        str(r["node_id"]) for r in change.nodes
    }
    if absent := [e for e in ends if str(e) not in present]:
        return _refuse(out, f"added links use node(s) {few(absent)}, which are not in the network")
    return out
