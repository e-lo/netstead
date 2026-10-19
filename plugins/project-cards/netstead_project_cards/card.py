"""Draft ProjectCards: group changes, and translate them to and from card dicts and YAML files.

``DraftCard.to_card()`` writes ProjectCard names through the maintained mapping (``link_id`` -> ``model_link_id``,
``from_node_id`` -> ``A``, ...). A change cannot carry extra fields in the ProjectCard schema, so netstead's
provenance travels in the card-level ``notes``, one ``netstead.<what>[<i>]: <value>`` line per item: ``resolved``
(the link ids a query selection resolved to) and ``note`` (why change ``i`` was made). :func:`read_card_file` reads
them back. It also reads the offline validation report's edit log (``netstead.map.edits.dump_edit_log``), whose
facility puts ``model_link_id`` at the top level and whose changes carry their own ``notes``.

Files are read with ``projectcard.read_card`` (YAML, JSON or TOML, its key normalisation and schema defaults) and
written with ``yaml.safe_dump``, not ``projectcard.write_card``, which comments out every line containing ``#`` (so a
street named ``Hwy #2`` makes the card unreadable) and always writes ``dependencies: {}``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from projectcard import read_card
from projectcard.errors import ProjectCardReadError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import CardError, CardNotSupported, not_supported
from .mapping import FieldMap, load_mapping
from .types import (
    CHANGE_TYPES,
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    incomplete,
)

__all__ = ["Dependencies", "DraftCard", "read_card_file"]

_PROVENANCE = re.compile(r"^netstead\.(resolved|note)\[(\d+)\]: (.*)$")
_PROP_KEYS = ("existing", "set", "change", "existing_value_conflict")


class Dependencies(BaseModel):
    """A card's ProjectCard ``dependencies``: other projects, by name."""

    model_config = ConfigDict(extra="forbid")
    prerequisites: list[str] = Field(default_factory=list)
    corequisites: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    def to_card(self) -> dict[str, list[str]]:
        """Only the non-empty lists (the schema has no required keys)."""
        return {k: list(v) for k, v in self.model_dump().items() if v}


class DraftCard(BaseModel):
    """A ProjectCard being built (or committed): project, tags, dependencies, notes and its changes."""

    model_config = ConfigDict(extra="forbid")
    project: str = ""
    tags: list[str] = Field(default_factory=list)
    dependencies: Dependencies = Field(default_factory=Dependencies)
    notes: str | None = None
    changes: list[NetworkChange] = Field(default_factory=list)

    def to_card(self, mapping: FieldMap | None = None) -> dict[str, Any]:
        """The ProjectCard dict, in Wrangler names.

        Raises:
            CardNotSupported: a transit change.
            CardError: a change that is incomplete (no facility, nothing to delete).
        """
        m = mapping or load_mapping()
        card: dict[str, Any] = {"project": self.project or "untitled netstead card"}
        if self.tags:
            card["tags"] = list(self.tags)
        if deps := self.dependencies.to_card():
            card["dependencies"] = deps
        notes = ([self.notes] if self.notes else []) + _provenance(self.changes)
        if notes:
            card["notes"] = "\n".join(notes)
        card["changes"] = [{c.type: _body(i, c, m)} for i, c in enumerate(self.changes)]
        return card

    def to_yaml(self, mapping: FieldMap | None = None) -> str:
        """The card as YAML text, in the field order ProjectCard documents use."""
        return yaml.safe_dump(self.to_card(mapping), sort_keys=False, default_flow_style=False, allow_unicode=True)

    @classmethod
    def from_card(cls, data: Mapping[str, Any], mapping: FieldMap | None = None) -> DraftCard:
        """Read a ProjectCard dict (a ``changes`` list, or one top-level change) back into GMNS names.

        Raises:
            CardError: not a ProjectCard (no ``project``), or a malformed change (named by its number).
            CardNotSupported: transit, ``pycode``, managed lanes, or a query selection without netstead provenance.
        """
        m = mapping or load_mapping()
        if not isinstance(data, Mapping) or "project" not in data:
            raise CardError("not a ProjectCard: expected a top-level 'project'")
        raw = data.get("changes")
        if raw is None:
            raw = [{k: data[k]} for k in (*CHANGE_TYPES, "roadway_managed_lanes", "pycode") if k in data]
        notes, resolved, why = _split_notes(data.get("notes"))
        changes = []
        for i, item in enumerate(raw):
            if not isinstance(item, Mapping) or len(item) != 1:
                raise CardError(f"change {i + 1}: expected exactly one change type")
            ((kind, body),) = item.items()
            try:
                changes.append(_change(kind, body or {}, m, resolved.get(i), why.get(i)))
            except ValidationError as exc:
                first = exc.errors(include_url=False, include_input=False)[0]["msg"]
                raise CardError(f"change {i + 1}: {first}") from exc
        deps = {k: list(v) for k, v in (data.get("dependencies") or {}).items()}
        return cls(
            project=str(data["project"]),
            tags=[str(t) for t in data.get("tags") or []],
            dependencies=Dependencies(**deps),
            notes=notes,
            changes=changes,
        )


def read_card_file(path: str | Path, mapping: FieldMap | None = None) -> DraftCard:
    """Read a ProjectCard file (YAML, JSON or TOML) or an offline-report edit log as a :class:`DraftCard`.

    ``projectcard.read_card(validate=False)`` reads it (an edit log is not schema-valid) and fills schema defaults: a
    selector without ``ignore_missing`` reads as ``true``, Wrangler's default.
    """
    try:
        card = read_card(Path(path), validate=False)
    except KeyError as exc:  # projectcard looks the project name up first
        raise CardError(f"not a ProjectCard: expected a top-level {exc.args[0]!r}") from None
    except ProjectCardReadError as exc:  # an unsupported file type
        raise CardError(str(exc)) from exc
    return DraftCard.from_card({k: v for k, v in card.to_dict.items() if k != "file"}, mapping)


# ---------------------------------------------------------------- writing


def _provenance(changes: list[Any]) -> list[str]:
    lines = []
    for i, c in enumerate(changes):
        if isinstance(c, RoadwayPropertyChange) and c.facility is not None and c.facility.query is not None:
            lines.append(f"netstead.resolved[{i}]: model_link_id={json.dumps(c.facility.resolved)}")
        if c.note:
            lines.append(f"netstead.note[{i}]: {' '.join(c.note.split())}")
    return lines


def _ids(ids: list[Any], table: str, m: FieldMap, *, ignore_missing: bool) -> dict[str, Any]:
    _, key = m.selector(table)
    return {key: list(ids), "ignore_missing": ignore_missing}


def _facility(sel: Selection, m: FieldMap) -> dict[str, Any]:
    if sel.query is not None:
        query = copy.deepcopy(sel.query)
        query.setdefault("links", {}).setdefault("ignore_missing", True)  # required by the schema
        return query
    selector = _ids(list(sel.ids or []), sel.table, m, ignore_missing=sel.ignore_missing)
    return {"links" if sel.table == "link" else "nodes": selector}


def _prop(name: str, pc: PropertyChange, m: FieldMap) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if pc.existing is not None:
        out["existing"] = m.card_value(name, pc.existing)
        out["existing_value_conflict"] = pc.existing_value_conflict
    if pc.set is not None:
        out["set"] = m.card_value(name, pc.set)
    else:
        out["change"] = pc.change
    return out


def _record(record: dict[str, Any], table: str, m: FieldMap, *, derive: bool = False) -> dict[str, Any]:
    out = {}
    for column, value in record.items():
        if value is not None:
            name = m.to_card(table, column)
            out[name] = m.card_value(name, value)
    return {**m.derive(record), **out} if derive else out


def _body(i: int, c: Any, m: FieldMap) -> dict[str, Any]:
    if c.type in TRANSIT_TYPES:
        raise not_supported(c.type)
    if problem := incomplete(c):
        raise CardError(f"change {i + 1}: {problem}")
    if isinstance(c, RoadwayPropertyChange):
        table = c.facility.table
        props = {m.to_card(table, p): _prop(m.to_card(table, p), pc, m) for p, pc in c.property_changes.items()}
        return {"facility": _facility(c.facility, m), "property_changes": props}
    if isinstance(c, RoadwayDeletion):
        body: dict[str, Any] = {}
        if c.links:  # a deletion aims at specific records: fail loudly when one is gone
            body["links"] = _ids(c.links, "link", m, ignore_missing=False)
        if c.nodes:
            body["nodes"] = _ids(c.nodes, "node", m, ignore_missing=False)
        if c.clean_nodes:
            body["clean_nodes"] = True
        return body
    body = {}
    if c.links:
        body["links"] = [_record(r, "link", m, derive=True) for r in c.links]
    if c.nodes:
        body["nodes"] = [_record(r, "node", m) for r in c.nodes]
    return body


# ---------------------------------------------------------------- reading


def _split_notes(notes: Any) -> tuple[str | None, dict[int, list[Any]], dict[int, str]]:
    """``(the user's notes, {i: resolved ids}, {i: note})`` from a card's ``notes`` text."""
    if not isinstance(notes, str):
        return None, {}, {}
    kept, resolved, why = [], {}, {}
    for line in notes.splitlines():
        match = _PROVENANCE.match(line.strip())
        if match is None:
            kept.append(line)
        elif match[1] == "resolved":
            resolved[int(match[2])] = json.loads(match[3].split("=", 1)[1])
        else:
            why[int(match[2])] = match[3]
    text = "\n".join(kept).strip()
    return text or None, resolved, why


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def _selector_ids(selector: Any, table: str, m: FieldMap) -> list[Any]:
    if not selector:
        return []
    _, key = m.selector(table)
    if key not in selector:
        raise CardNotSupported(f"only {key} selections can be read for a deletion in this version")
    return _as_list(selector[key])


def _selection(facility: Mapping[str, Any], m: FieldMap, resolved: list[Any] | None) -> Selection:
    for table in ("link", "node"):  # the offline report's edit log: {model_link_id: [...]} at the top level
        _, key = m.selector(table)
        if key in facility:
            return Selection(table=table, ids=_as_list(facility[key]))
    if facility.get("nodes") is not None:
        nodes = facility["nodes"]
        return Selection(
            table="node", ids=_selector_ids(nodes, "node", m), ignore_missing=bool(nodes.get("ignore_missing"))
        )
    links = facility.get("links")
    if links is None:
        raise CardError("a property change needs a facility with links or nodes")
    _, key = m.selector("link")
    by_id = key in links and set(links) <= {key, "ignore_missing"} and "from" not in facility and "to" not in facility
    if by_id:
        return Selection(ids=_as_list(links[key]), ignore_missing=bool(links.get("ignore_missing")))
    if resolved is None:
        raise CardNotSupported(
            "this card selects links by a query (name, ref, ...) with no netstead provenance; "
            "re-resolving a query arrives with apply_card"
        )
    return Selection(query=dict(facility), resolved=resolved)


def _from_record(record: Mapping[str, Any], table: str, m: FieldMap) -> dict[str, Any]:
    return {m.from_card(table, k): m.gmns_value(k, v) for k, v in record.items() if k not in m.derived}


def _change(kind: str, body: Mapping[str, Any], m: FieldMap, resolved: list[Any] | None, note: str | None) -> Any:
    if kind not in CHANGE_TYPES or kind in TRANSIT_TYPES:
        raise CardNotSupported(f"{kind} changes are not supported yet")
    if note is None and isinstance(body.get("notes"), str):  # the offline report's per-change notes
        note = body["notes"]
    if kind == "roadway_property_change":
        sel = _selection(body.get("facility") or {}, m, resolved)
        props = {
            m.from_card(sel.table, name): PropertyChange(
                **{
                    k: (m.gmns_value(name, v) if k in ("existing", "set") else v)
                    for k, v in spec.items()
                    if k in _PROP_KEYS
                }
            )
            for name, spec in (body.get("property_changes") or {}).items()
        }
        return RoadwayPropertyChange(facility=sel, property_changes=props, note=note)
    if kind == "roadway_deletion":
        change = RoadwayDeletion(
            links=_selector_ids(body.get("links"), "link", m),
            nodes=_selector_ids(body.get("nodes"), "node", m),
            clean_nodes=bool(body.get("clean_nodes")),
            note=note,
        )
        if problem := incomplete(change):
            raise CardError(problem)
        return change
    return RoadwayAddition(
        links=[_from_record(r, "link", m) for r in body.get("links") or []],
        nodes=[_from_record(r, "node", m) for r in body.get("nodes") or []],
        note=note,
    )
