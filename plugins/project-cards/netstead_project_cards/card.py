"""Draft ProjectCards: group changes, and translate them to and from card dicts and YAML files.

``DraftCard.to_card()`` writes ProjectCard names through the maintained mapping (``link_id`` -> ``model_link_id``,
``from_node_id`` -> ``A``, ...). A change cannot carry extra fields in the ProjectCard schema, so netstead's
provenance travels in the card-level ``notes``, one ``netstead.<what>[<i>]: <value>`` line per item: ``resolved``
(the link ids a query selection resolved to) and ``note`` (why change ``i`` was made). :func:`read_card_file` reads
them back. The offline validation report's edit log (``netstead.map.edits.dump_edit_log``) writes the same
``netstead.note[<i>]`` lines and is an ordinary card; edit logs written before netstead 2026-10-20 put
``model_link_id`` at the top of the facility and give each change its own ``notes``, and are read tolerantly.

Files are read with ``projectcard.read_card`` (YAML or JSON, its key normalisation and schema defaults; its TOML reader
is broken in 0.3.3, so a ``.toml`` card is refused) and
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
from projectcard.errors import ProjectCardReadError, ProjectCardValidationError
from projectcard.projectcard import CHANGE_TYPES as _PROJECTCARD_TYPES
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
#: Every key a card may hold a change under: projectcard's types, plus managed lanes (refused clearly on read).
_CARD_CHANGE_KEYS = (*_PROJECTCARD_TYPES, "roadway_managed_lanes")
_UNSUPPORTED = frozenset({*TRANSIT_TYPES, "pycode", "roadway_managed_lanes"})


class Dependencies(BaseModel):
    """A card's ProjectCard ``dependencies``: other projects, by name."""

    model_config = ConfigDict(extra="forbid")
    prerequisites: list[str] = Field(default_factory=list, strict=True)
    corequisites: list[str] = Field(default_factory=list, strict=True)
    conflicts: list[str] = Field(default_factory=list, strict=True)

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
        notes = ([self.notes] if self.notes else []) + _provenance(self.changes, m)
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
            CardError: not a ProjectCard (no ``project``), malformed ``tags``, ``dependencies`` or provenance notes,
                or a malformed change (the message starts ``change <n>:``).
            CardNotSupported: transit, ``pycode``, managed lanes, a query selection without netstead provenance, a
                scoped (time-of-day) value in an added record, or a selector key other than the id and
                ``ignore_missing``.

        A deletion keeps its ``links`` selector's ``ignore_missing``. Its ``nodes`` flag is dropped and written back
        as ``ignore_missing: false`` (a node deletion aims at specific records and fails loudly when one is gone).
        """
        m = mapping or load_mapping()
        if not isinstance(data, Mapping) or "project" not in data:
            raise CardError("not a ProjectCard: expected a top-level 'project'")
        raw = data.get("changes")
        if raw is None:
            raw = [{k: data[k]} for k in _CARD_CHANGE_KEYS if k in data]
        raw = _list(raw, "changes")
        notes, resolved, why = _split_notes(data.get("notes"), len(raw))
        changes = []
        for i, item in enumerate(raw):
            if not isinstance(item, Mapping) or len(item) != 1:
                raise CardError(f"change {i + 1}: expected exactly one change type")
            ((kind, body),) = item.items()
            try:
                changes.append(_change(kind, body, m, resolved.get(i), why.get(i)))
            except ValidationError as exc:
                raise CardError(f"change {i + 1}: {_message(exc)}") from exc
            except CardError as exc:
                raise type(exc)(f"change {i + 1}: {exc}") from exc
        tags = data.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise CardError(f"tags: expected a list of strings, got {tags!r}")
        return cls(
            project=str(data["project"]),
            tags=tags,
            dependencies=_dependencies(data.get("dependencies")),
            notes=notes,
            changes=changes,
        )


def read_card_file(path: str | Path, mapping: FieldMap | None = None) -> DraftCard:
    """Read a ProjectCard file (YAML or JSON) or an offline-report edit log as a :class:`DraftCard`.

    ``projectcard.read_card(validate=False)`` reads it (an older edit log is not schema-valid) and fills schema
    defaults: a selector without ``ignore_missing`` reads as ``true``, Wrangler's default.

    Raises:
        FileNotFoundError: there is no file at ``path``.
        CardNotSupported: a TOML card (projectcard 0.3.3 cannot read one), or a change this version cannot read.
        CardError: a file that cannot be read (permissions, an I/O error) or that projectcard cannot read (bad YAML
            or JSON, not a mapping, an unsupported extension), or a card :meth:`DraftCard.from_card` refuses.
    """
    path = Path(path)
    if path.suffix.lower() == ".toml":
        raise CardNotSupported("TOML cards can't be read with projectcard 0.3.3; use YAML or JSON")
    try:
        card = read_card(path, validate=False)
    except FileNotFoundError:
        raise
    except OSError as exc:  # unreadable: permissions, a folder, an I/O error
        raise CardError(f"could not read {path}: {exc.strerror or exc}") from exc
    except KeyError as exc:  # projectcard looks the project name up first
        raise CardError(f"not a ProjectCard: expected a top-level {exc.args[0]!r}") from None
    except ProjectCardReadError as exc:  # an unsupported file type
        raise CardError(str(exc)) from exc
    except (yaml.YAMLError, ValueError, TypeError, AttributeError, ProjectCardValidationError) as exc:
        raise CardError(f"{path}: not a readable ProjectCard: {exc}") from exc
    return DraftCard.from_card({k: v for k, v in card.to_dict.items() if k != "file"}, mapping)


# ---------------------------------------------------------------- writing


def _provenance(changes: list[Any], m: FieldMap) -> list[str]:
    lines = []
    for i, c in enumerate(changes):
        if isinstance(c, RoadwayPropertyChange) and c.facility is not None and c.facility.query is not None:
            lines.append(f"netstead.resolved[{i}]: {m.selector('link')[1]}={json.dumps(c.facility.resolved)}")
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
        if c.links:  # false for picked or typed links; a query selection's own flag for the links it resolved to
            body["links"] = _ids(c.links, "link", m, ignore_missing=c.ignore_missing)
        if c.nodes:  # nodes are aimed at specific records: fail loudly when one is gone
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


def _message(exc: ValidationError) -> str:
    """The first error's message, without pydantic's ``Value error, `` prefix."""
    return exc.errors(include_url=False, include_input=False)[0]["msg"].removeprefix("Value error, ")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CardError(f"{where}: expected a mapping, got {value!r}")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise CardError(f"{where}: expected a list, got {value!r}")
    return value


def _dependencies(value: Any) -> Dependencies:
    try:
        return Dependencies.model_validate(value or {})
    except ValidationError as exc:
        error = exc.errors(include_url=False, include_input=False)[0]
        where = ".".join(str(p) for p in error["loc"])
        raise CardError(f"dependencies: {where + ': ' if where else ''}{_message(exc)}") from exc
    except (TypeError, AttributeError) as exc:
        raise CardError(f"dependencies: {exc}") from exc


def _resolved_ids(line: str, value: str) -> list[Any]:
    """The ids of a ``netstead.resolved[i]: <key>=<json list>`` line (``CardError`` naming the line otherwise)."""
    _, eq, ids = value.partition("=")
    if not eq:
        raise CardError(f"notes: {line!r}: expected <selector>=[ids]")
    try:
        parsed = json.loads(ids)
    except ValueError as exc:
        raise CardError(f"notes: {line!r}: the ids are not JSON ({exc})") from exc
    if not isinstance(parsed, list) or not all(isinstance(i, int | str) and not isinstance(i, bool) for i in parsed):
        raise CardError(f"notes: {line!r}: expected a list of ids")
    return parsed


def _split_notes(notes: Any, count: int) -> tuple[str | None, dict[int, list[Any]], dict[int, str]]:
    """``(the user's notes, {i: resolved ids}, {i: note})`` from a card's ``notes`` text.

    A provenance line for a change the card does not have (``i >= count``) stays in the user's notes.
    """
    if not isinstance(notes, str):
        return None, {}, {}
    kept, resolved, why = [], {}, {}
    for line in notes.splitlines():
        match = _PROVENANCE.match(line.strip())
        if match is None or int(match[2]) >= count:
            kept.append(line)
        elif match[1] == "resolved":
            resolved[int(match[2])] = _resolved_ids(line.strip(), match[3])
        else:
            why[int(match[2])] = match[3]
    text = "\n".join(kept).strip()
    return text or None, resolved, why


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def _selector_ids(selector: Any, table: str, m: FieldMap, where: str) -> list[Any]:
    if not selector:
        return []
    selector = _mapping(selector, where)
    _, key = m.selector(table)
    if key not in selector or set(selector) - {key, "ignore_missing"}:
        raise CardNotSupported(f"only {key} selections of {table}s can be read in this version")
    return _as_list(selector[key])


def _selection(facility: Mapping[str, Any], m: FieldMap, resolved: list[Any] | None) -> Selection:
    for table in ("link", "node"):  # an older edit log: {model_link_id: [...]} at the top level
        _, key = m.selector(table)
        if key in facility:
            return Selection(table=table, ids=_as_list(facility[key]))
    if facility.get("nodes") is not None:
        nodes = _mapping(facility["nodes"], "facility nodes")
        return Selection(
            table="node",
            ids=_selector_ids(nodes, "node", m, "facility nodes"),
            ignore_missing=bool(nodes.get("ignore_missing")),
        )
    links = facility.get("links")
    if links is None:
        raise CardError("a property change needs a facility with links or nodes")
    links = _mapping(links, "facility links")
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
    """A card record in GMNS names.

    A derived field (``walk_access``) is dropped only when the record also carries its source column
    (``allowed_uses``), from which writing rebuilds it; otherwise it is kept as an ordinary field, so a foreign
    card's flags survive a round trip.
    """
    rebuilt = {k for k, rule in m.derived.items() if m.to_card(table, rule["column"]) in record}
    return {m.from_card(table, k): m.gmns_value(k, v) for k, v in record.items() if k not in rebuilt}


def _records(body: Mapping[str, Any], kind: str, table: str, m: FieldMap) -> list[dict[str, Any]]:
    records = [_mapping(r, f"{kind}[{j}]") for j, r in enumerate(_list(body.get(kind) or [], kind))]
    _, key = m.selector(table)
    for j, record in enumerate(records):
        for name, value in record.items():
            if isinstance(value, Mapping):  # Wrangler's scoped value: {default: 2, timeofday: [...]}
                where = f"{table} {record.get(key, f'#{j + 1}')} field {name!r}"
                raise CardNotSupported(f"{where}: scoped (time-of-day) values are not supported yet")
    return [_from_record(r, table, m) for r in records]


def _properties(body: Mapping[str, Any], sel: Selection, m: FieldMap) -> dict[str, PropertyChange]:
    props = {}
    for name, spec in _mapping(body.get("property_changes") or {}, "property_changes").items():
        spec = _mapping(spec, f"property {name}")
        if extra := sorted(set(spec) - set(_PROP_KEYS)):
            if {"scoped", "overwrite_scoped"} & set(extra):
                raise CardNotSupported(f"property {name}: scoped changes are not supported yet")
            raise CardNotSupported(f"property {name}: {', '.join(map(repr, extra))} cannot be read in this version")
        values = {k: (m.gmns_value(name, v) if k in ("existing", "set") else v) for k, v in spec.items()}
        props[m.from_card(sel.table, name)] = PropertyChange(**values)
    return props


def _change(kind: str, body: Any, m: FieldMap, resolved: list[Any] | None, note: str | None) -> Any:
    if kind in _UNSUPPORTED:
        raise CardNotSupported(f"{kind} changes are not supported yet")
    if kind not in CHANGE_TYPES:
        raise CardError(f"unknown change type {kind!r}")
    body = {} if body is None else _mapping(body, kind)
    if note is None and isinstance(body.get("notes"), str):  # an older edit log's per-change notes
        note = body["notes"]
    if kind == "roadway_property_change":
        sel = _selection(_mapping(body.get("facility") or {}, "facility"), m, resolved)
        return RoadwayPropertyChange(facility=sel, property_changes=_properties(body, sel, m), note=note)
    if kind == "roadway_deletion":
        links = _selector_ids(body.get("links"), "link", m, "links")
        change = RoadwayDeletion(
            links=links,
            nodes=_selector_ids(body.get("nodes"), "node", m, "nodes"),
            clean_nodes=bool(body.get("clean_nodes")),
            ignore_missing=bool(links) and bool(body["links"].get("ignore_missing")),
            note=note,
        )
        if problem := incomplete(change):
            raise CardError(problem)
        return change
    return RoadwayAddition(
        links=_records(body, "links", "link", m), nodes=_records(body, "nodes", "node", m), note=note
    )
