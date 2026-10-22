"""Foreign-key relations between a network's tables, read from its spec (never hard-coded).

The related-records view tints rows that are a foreign key away from what is focused or
highlighted: the lanes of a link (``lane.link_id`` points *in*), the nodes a link references
(``link.from_node_id`` points *out*), and so on. Every relation is a predicate
``<column> IN (<values>)`` on the related table, with ``values`` bounded by the highlight: an
inbound key reuses the highlighted ids as they are; an outbound key costs one query for the
distinct key values of the highlighted rows. It is a read-only view: nothing here is recorded.

Only single-column keys between two *different* tables are followed: self-references
(``link.parent_link_id``) and composite keys are out of scope for now.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from netstead.viz.styling import json_scalar
from netstead.viz.tables import columns_of, primary_key, rowcount_of

__all__ = [
    "MAX_MAP_IDS",
    "MAX_SOURCE_IDS",
    "ForeignKey",
    "Match",
    "Relation",
    "RelationGraph",
    "count",
    "field_enums",
    "foreign_keys",
    "ids_of",
    "primary_keys",
    "relate",
    "relation_graph",
    "restrict",
    "row_vias",
]

#: Highlighted ids accepted per request (and the cap on a hop-2 frontier).
MAX_SOURCE_IDS = 10_000
#: Link/node ids returned for map tinting; beyond this the answer says ``truncated``.
MAX_MAP_IDS = 50_000


@dataclass(frozen=True)
class ForeignKey:
    """One single-column foreign key: ``table.column`` references ``ref_table.ref_column``."""

    table: str
    column: str
    ref_table: str
    ref_column: str

    @property
    def label(self) -> str:
        """How the UI names the relation, e.g. ``lane.link_id → link``."""
        return f"{self.table}.{self.column} → {self.ref_table}"


@dataclass(frozen=True)
class Match:
    """Rows whose ``column`` is in ``values``, reached through the relation named ``via``."""

    column: str
    values: tuple[Any, ...]
    via: str


@dataclass
class Relation:
    """Everything related in one table: the OR of its matches, at the hop it was first reached."""

    table: str
    hop: int
    matches: list[Match] = field(default_factory=list)
    partial: bool = False  # derived from a frontier capped at MAX_SOURCE_IDS

    @property
    def via(self) -> list[str]:
        """The distinct relation labels that reach this table, sorted."""
        return sorted({m.via for m in self.matches})


@dataclass(frozen=True)
class RelationGraph:
    """A network's browsable tables, their primary keys, and the foreign keys between them."""

    tables: Mapping[str, Any]
    fks: tuple[ForeignKey, ...]
    pks: Mapping[str, str | None]


def _single(fields: str | list[str]) -> str | None:
    if isinstance(fields, str):
        return fields
    return fields[0] if len(fields) == 1 else None


def _schemas(spec: Any) -> dict[str, Any]:
    return {
        r.name: r.table_schema
        for r in spec.resources
        if r.table_schema is not None and not isinstance(r.table_schema, str)
    }


def field_enums(spec: Any, table: str) -> dict[str, list[Any]]:
    """``column -> allowed values`` for the fields of ``table`` that the spec constrains with an ``enum``.

    Shared categories are already inlined into ``constraints.enum`` by the spec loader. Empty for a
    table the spec does not describe.
    """
    schema = _schemas(spec).get(table)
    if schema is None:
        return {}
    return {f.name: list(f.constraints.enum) for f in schema.fields if f.constraints is not None and f.constraints.enum}


def foreign_keys(spec: Any, columns: Mapping[str, Sequence[str]], *, self_refs: bool = False) -> list[ForeignKey]:
    """Single-column FKs between tables in ``columns``, both columns present.

    Same-table keys (``link.parent_link_id``) are skipped unless ``self_refs``: the related-records view
    never follows them; edit planning and the live checks do.
    """
    out: list[ForeignKey] = []
    for name, schema in _schemas(spec).items():
        if name not in columns:
            continue
        for fk in schema.foreign_keys:
            ref_table = fk.reference.resource or name
            column, ref_column = _single(fk.fields), _single(fk.reference.fields)
            if ref_table == name and not self_refs:
                continue
            if ref_table not in columns or column is None or ref_column is None:
                continue
            if column in columns[name] and ref_column in columns[ref_table]:
                out.append(ForeignKey(name, column, ref_table, ref_column))
    return out


def primary_keys(spec: Any, columns: Mapping[str, Sequence[str]]) -> dict[str, str | None]:
    """Each table's key: the spec's single-column ``primaryKey`` when present, else the ``*_id`` heuristic."""
    declared = {name: schema.primary_key for name, schema in _schemas(spec).items()}
    out: dict[str, str | None] = {}
    for name, cols in columns.items():
        pk = declared.get(name)
        out[name] = pk if isinstance(pk, str) and pk in cols else primary_key(name, list(cols))
    return out


def relation_graph(spec: Any, tables: Mapping[str, Any]) -> RelationGraph:
    """The :class:`RelationGraph` over ``tables`` (pandas frames or lazy corral tables)."""
    columns = {name: columns_of(src) for name, src in tables.items()}
    return RelationGraph(tables=tables, fks=tuple(foreign_keys(spec, columns)), pks=primary_keys(spec, columns))


def restrict(src: Any, matches: Sequence[Match]) -> Any:
    """``src`` limited to rows any of ``matches`` selects; no matches selects nothing.

    ``src`` is a pandas frame (masked in memory) or a lazy corral table (an ibis ``IN`` predicate
    pushed to DuckDB; no SQL text).
    """
    if isinstance(src, pd.DataFrame):
        mask = pd.Series(False, index=src.index)
        for m in matches:
            mask |= src[m.column].isin(m.values)
        return src[mask]
    if not matches:
        return src.limit(0)

    def predicate(expr: Any) -> Any:
        return expr.filter(functools.reduce(operator.or_, [expr[m.column].isin(list(m.values)) for m in matches]))

    return src.filter(predicate)


def count(src: Any, matches: Sequence[Match]) -> int:
    """How many rows of ``src`` the matches select (one DuckDB ``COUNT`` for a lazy table)."""
    return rowcount_of(restrict(src, matches))


def ids_of(src: Any, matches: Sequence[Match], pk: str, *, limit: int) -> tuple[list[Any], bool]:
    """The first ``limit`` primary keys (in key order) of the matched rows, and whether there are more."""
    sub = restrict(src, matches)
    if isinstance(sub, pd.DataFrame):
        keys = sub[pk].dropna().sort_values().head(limit + 1).tolist()
    else:
        keys = sub.select(pk).order_by(pk).limit(limit + 1).to_pandas()[pk].dropna().tolist()
    return [json_scalar(k) for k in keys[:limit]], len(keys) > limit


def _distinct(src: Any, column: str, pk: str, ids: Sequence[Any]) -> tuple[Any, ...]:
    """Distinct non-null ``column`` values of the rows whose ``pk`` is in ``ids`` (bounded by ``ids``)."""
    sub = restrict(src, [Match(pk, tuple(ids), "")])
    if isinstance(sub, pd.DataFrame):
        frame = sub
    else:  # DISTINCT runs in DuckDB: only the distinct values come back (corral's Table has no distinct())
        frame = sub.select(column).filter(lambda expr: expr.distinct()).to_pandas()
    return tuple(json_scalar(v) for v in pd.unique(frame[column].dropna()))


def _hop(
    graph: RelationGraph, frontier: Mapping[str, tuple[Any, ...]], reached: set[str], hop: int
) -> dict[str, Relation]:
    """Every table one key away from ``frontier`` that is not yet ``reached``."""
    found: dict[str, Relation] = {}
    for fk in graph.fks:
        ref_pk, own_pk = graph.pks.get(fk.ref_table), graph.pks.get(fk.table)
        if fk.ref_table in frontier and fk.table not in reached and ref_pk:  # inbound: fk.table points at the frontier
            ids = frontier[fk.ref_table]
            values = (
                ids if fk.ref_column == ref_pk else _distinct(graph.tables[fk.ref_table], fk.ref_column, ref_pk, ids)
            )
            found.setdefault(fk.table, Relation(fk.table, hop)).matches.append(Match(fk.column, values, fk.label))
        if fk.table in frontier and fk.ref_table not in reached and own_pk:  # outbound: the frontier points at it
            values = _distinct(graph.tables[fk.table], fk.column, own_pk, frontier[fk.table])
            found.setdefault(fk.ref_table, Relation(fk.ref_table, hop)).matches.append(
                Match(fk.ref_column, values, fk.label)
            )
    return {name: r for name, r in found.items() if any(m.values for m in r.matches)}


def relate(graph: RelationGraph, sources: Mapping[str, Sequence[Any]], *, hops: int = 1) -> dict[str, Relation]:
    """Tables related to ``sources`` (``{table: ids}``) within ``hops`` keys, each at the hop it is first reached.

    A table is never revisited, sources included, so hop 2 reaches *new* tables (link → lane →
    lane_tod), never back to the source table.
    """
    frontier = {t: tuple(ids) for t, ids in sources.items() if t in graph.tables and ids}
    reached: set[str] = set(frontier)
    out: dict[str, Relation] = {}
    partial = False
    for hop in range(1, hops + 1):
        found = _hop(graph, frontier, reached, hop)
        for relation in found.values():
            relation.partial = partial
        out.update(found)
        reached |= set(found)
        if hop == hops or not found:
            break
        frontier = {}
        for name, relation in found.items():
            pk = graph.pks.get(name)
            if pk:
                ids, more = ids_of(graph.tables[name], relation.matches, pk, limit=MAX_SOURCE_IDS)
                frontier[name], partial = tuple(ids), partial or more
    return out


def row_vias(page: pd.DataFrame, relation: Relation | None) -> list[str | None]:
    """Per row of ``page``: the first relation label that selects it, else ``None`` (pure pandas)."""
    out: list[str | None] = [None] * len(page)
    for m in relation.matches if relation else []:
        hits = page[m.column].isin(m.values).tolist() if m.column in page.columns else [False] * len(page)
        out = [prev or (m.via if hit else None) for prev, hit in zip(out, hits, strict=True)]
    return out
