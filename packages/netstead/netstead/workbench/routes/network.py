"""Per-network data routes: ``/api/n/{net_id}/{component}/...``.

The ``component`` segment is ``roadway`` today; ``transit`` answers 501 until the
GTFS component lands (P6). Heavy payloads are cached on the handle per version.

Table rows can be read by ``GET`` (query string) or ``POST`` (JSON body). The POST form exists
because id lists (a box-selection, the related-records sources) outgrow a URL; it also carries
the related-records ``tint``/``filter``. ``/related`` and ``/locate`` are read-only views: like
every route here, nothing is recorded in the session history.
"""

from __future__ import annotations

import contextlib
import functools
import json as _json
from collections.abc import Iterator, Sequence
from typing import Any, Literal

import duckdb
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Response
from ibis.common.exceptions import IbisTypeError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netstead.viz.buffers import network_attrs, pack_network
from netstead.viz.styling import json_scalar, property_payload, styleable_columns
from netstead.viz.tables import (
    MAX_FILTER_CONDITIONS,
    MAX_IN_VALUES,
    FilterError,
    KeyTypeError,
    coerce_keys,
    column_dtype,
    distinct_values,
    locate_row,
    page_table,
    parse_ids,
    table_list_entry,
    table_schema,
)

from ..errors import ActionError
from ..registry import NetworkHandle
from ..related import (
    MAX_MAP_IDS,
    MAX_SOURCE_IDS,
    Match,
    Relation,
    RelationGraph,
    count,
    field_enums,
    ids_of,
    relate,
    relation_graph,
    restrict,
    row_vias,
)
from ..session import Session
from .common import network_handle

__all__ = ["LocateQuery", "RelatedQuery", "RowsQuery", "network_router"]

ScalarId = int | str
#: Related-records answers remembered per network version (each holds up to MAX_SOURCE_IDS ids).
RELATED_MEMO = 16


def _bounded_filter(spec: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """``spec`` unchanged, or ``ValueError`` when it is too large to run (no values echoed)."""
    if spec is None:
        return None
    if len(spec) > MAX_FILTER_CONDITIONS:
        raise ValueError(f"too many filter conditions (over {MAX_FILTER_CONDITIONS})")
    for cond in spec:
        val = cond.get("val") if isinstance(cond, dict) else None
        if isinstance(val, list) and len(val) > MAX_IN_VALUES:
            raise ValueError(f"a filter condition lists too many values (over {MAX_IN_VALUES:,})")
    return spec


class RelatedQuery(BaseModel):
    """What to find related records for: highlighted ids per table, and how many keys away."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    sources: dict[str, list[ScalarId]]
    hops: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def _bounded(self) -> RelatedQuery:
        if sum(len(ids) for ids in self.sources.values()) > MAX_SOURCE_IDS:
            raise ValueError(f"too many ids (over {MAX_SOURCE_IDS:,}); narrow the highlight")
        return self


class RowsQuery(BaseModel):
    """One page of a table: the GET parameters as JSON, plus related records to filter by and/or tint.

    ``related`` with ``related_mode="filter"`` narrows the rows; with ``"tint"`` it only marks them.
    ``tint`` marks rows by a second set of sources while ``related`` filters (e.g. the selection's lanes,
    tinted by what is focused).
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1)
    sort: str | None = None
    dir: Literal["asc", "desc"] = "asc"
    filter: list[dict[str, Any]] | None = None
    ids: list[ScalarId] | None = Field(default=None, max_length=MAX_SOURCE_IDS)
    related: RelatedQuery | None = None
    related_mode: Literal["tint", "filter"] = "tint"
    tint: RelatedQuery | None = None

    @field_validator("filter")
    @classmethod
    def _filter_bounded(cls, spec: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        return _bounded_filter(spec)


class LocateQuery(RowsQuery):
    """Where the row whose primary key is ``id`` sits, in a :class:`RowsQuery`'s order."""

    id: ScalarId


@contextlib.contextmanager
def _bad_request() -> Iterator[None]:
    """A bad filter, or a value the engine cannot compare with its column, is the client's error (400).

    Ids are coerced to their key's type before they reach the engine (422 when they cannot be);
    this is the backstop for everything else, such as a filter value of the wrong type.
    """
    try:
        yield
    except FilterError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (duckdb.ConversionException, IbisTypeError) as exc:
        reason = (str(exc).splitlines() or [""])[0][:200]
        raise HTTPException(400, f"a value does not match its column's type: {reason}") from exc


def network_router(session: Session) -> APIRouter:
    """Build the ``/api/n/{net_id}/{component}`` router bound to ``session``."""
    router = APIRouter(prefix="/api/n/{net_id}/{component}")

    def handle(net_id: str, component: str) -> NetworkHandle:
        return network_handle(session, net_id, component)

    def _styled_frame(net_id: str, component: str, table: str) -> pd.DataFrame:
        """The frame whose columns colour or size the map: the ``link`` table's, or the ``node`` table's."""
        h = handle(net_id, component)
        return h.nodes_df() if table == "node" else h.links_df()

    def table(h: NetworkHandle, name: str) -> Any:
        tables = h.tables()
        if name not in tables:
            raise HTTPException(404, f"unknown table {name!r}")
        return tables[name]

    def graph(h: NetworkHandle) -> RelationGraph:
        return h.cached("relations", lambda: relation_graph(h.roadway.spec, h.tables()))

    def keys(h: NetworkHandle, name: str, ids: Sequence[Any]) -> list[Any]:
        """``ids`` as ``name``'s primary-key type (``"1"`` is ``1`` for an integer key); 422 when one cannot be."""
        g = graph(h)
        dtypes = h.cached(
            "key.dtypes", lambda: {n: column_dtype(src, pk) for n, src in g.tables.items() if (pk := g.pks.get(n))}
        )
        if name not in dtypes:
            return list(ids)
        try:
            return coerce_keys(ids, dtypes[name])
        except KeyTypeError as exc:
            raise HTTPException(422, f"{name}: {exc}") from exc

    def sources_of(h: NetworkHandle, q: RelatedQuery) -> dict[str, list[Any]]:
        unknown = sorted(set(q.sources) - set(graph(h).tables))
        if unknown:
            raise HTTPException(400, f"unknown table(s) {unknown}")
        return {name: keys(h, name, ids) for name, ids in q.sources.items()}

    def related_of(h: NetworkHandle, sources: dict[str, list[Any]], hops: int) -> dict[str, Relation]:
        """:func:`relate`, memoised per network version: paging a tinted table asks the same question per page."""

        def build() -> Any:
            return functools.lru_cache(maxsize=RELATED_MEMO)(lambda key: relate(graph(h), dict(key[1]), hops=key[0]))

        return h.cached("related.memo", build)((hops, tuple((t, tuple(ids)) for t, ids in sources.items())))

    def prepared(
        h: NetworkHandle, name: str, q: RowsQuery
    ) -> tuple[Any, str | None, list[Any] | None, Relation | None]:
        """The table (narrowed when ``related_mode`` is ``filter``), its key, ``q.ids`` as keys, its relation."""
        g, src = graph(h), table(h, name)
        pk = g.pks.get(name)
        ids = None if q.ids is None else keys(h, name, q.ids)
        if ids is not None and not ids:  # an explicit empty id list is "no rows", not "all rows"
            src = restrict(src, [])
        if q.related is None:
            return src, pk, ids, None
        sources = sources_of(h, q.related)
        relation = related_of(h, sources, q.related.hops).get(name)
        if q.related_mode == "filter":
            if relation is not None:
                src = restrict(src, relation.matches)
            elif name in sources and pk:  # the source table: its own highlighted rows
                src = restrict(src, [Match(pk, tuple(sources[name]), "")])
            else:
                src = restrict(src, [])
        return src, pk, ids, relation

    @router.get("/network.bin")
    def network_bin(net_id: str, component: str) -> Response:
        h = handle(net_id, component)
        data = h.cached("network.bin", lambda: pack_network(h.links_df(), h.nodes_df(), geometry=h.geometry_df()))
        return Response(data, media_type="application/octet-stream")

    @router.get("/network.attrs.json")
    def network_attrs_json(net_id: str, component: str) -> dict[str, Any]:
        h = handle(net_id, component)
        return h.cached("network.attrs", lambda: network_attrs(h.links_df()))

    @router.get("/properties")
    def properties(net_id: str, component: str, table: Literal["link", "node"] = "link") -> dict[str, Any]:
        """The columns that can colour (or size) the ``link`` table's features, or the ``node`` table's."""
        return {"properties": styleable_columns(_styled_frame(net_id, component, table), table)}

    @router.get("/filter")
    def view_filter(net_id: str, component: str) -> dict[str, Any]:
        """The network's ``Filter`` and the link ids it matches now (``where: null`` when there is none)."""
        h = handle(net_id, component)
        entry = session.filter_entry(h.id)
        if entry is None:
            return {"version": h.version, "where": None, "count": 0, "link_ids": []}
        try:
            ids = session.filter_ids(h.id) or []
        except ActionError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"version": h.version, "where": entry["where"], "count": len(ids), "link_ids": ids}

    @router.get("/property/{name}")
    def property_values(
        net_id: str, component: str, name: str, table: Literal["link", "node"] = "link"
    ) -> dict[str, Any]:
        payload = property_payload(_styled_frame(net_id, component, table), name)
        if payload is None:
            raise HTTPException(404, f"unknown property {name!r}")
        return payload

    @router.get("/feature/{table_name}/{pk_value}")
    def feature(net_id: str, component: str, table_name: str, pk_value: str) -> dict[str, Any]:
        h = handle(net_id, component)
        src, pk = table(h, table_name), graph(h).pks.get(table_name)
        if pk is None:
            raise HTTPException(404, f"table {table_name!r} has no primary key")
        key = keys(h, table_name, [pk_value])[0]
        with _bad_request():
            rows = page_table(src, limit=1, ids=[key], pk=pk)
        if not rows["rows"]:
            raise HTTPException(404, f"{table_name} {pk_value} not found")
        return {
            "table": table_name,
            "pk": pk,
            "id": json_scalar(key),
            "attributes": dict(zip(rows["columns"], rows["rows"][0], strict=True)),
        }

    @router.get("/tables")
    def tables_list(net_id: str, component: str) -> dict[str, Any]:
        return {"tables": [table_list_entry(n, src) for n, src in handle(net_id, component).tables().items()]}

    @router.get("/table/{table_name}/schema")
    def table_schema_ep(net_id: str, component: str, table_name: str) -> dict[str, Any]:
        h = handle(net_id, component)
        g = graph(h)
        payload = table_schema(table_name, table(h, table_name), field_enums(h.roadway.spec, table_name))
        payload["primary_key"] = g.pks.get(table_name)
        payload["foreign_keys"] = [
            {
                "column": fk.column,
                "ref_table": fk.ref_table,
                "ref_column": fk.ref_column,
                "navigable": fk.ref_column == g.pks.get(fk.ref_table),
            }
            for fk in g.fks
            if fk.table == table_name
        ]
        return payload

    @router.get("/table/{table_name}/distinct")
    def table_distinct(
        net_id: str, component: str, table_name: str, col: str, limit: int = Query(default=200, ge=1)
    ) -> dict[str, Any]:
        """The distinct values of one column (a column filter's value list); ``limit`` is capped at MAX_LIMIT."""
        h = handle(net_id, component)
        with _bad_request():
            return distinct_values(table(h, table_name), col, limit=limit)

    @router.get("/table/{table_name}/rows")
    def table_rows(
        net_id: str,
        component: str,
        table_name: str,
        offset: int = 0,
        limit: int = 100,
        sort: str | None = None,
        dir: str = "asc",
        filter: str | None = None,
        ids: str | None = None,
    ) -> dict[str, Any]:
        h = handle(net_id, component)
        try:
            spec = _json.loads(filter) if filter else None
        except _json.JSONDecodeError as exc:
            raise HTTPException(400, f"bad filter json: {exc}") from exc
        try:
            _bounded_filter(spec)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        id_list = parse_ids(ids)
        with _bad_request():
            payload = page_table(
                table(h, table_name),
                offset=offset,
                limit=limit,
                sort=sort,
                direction=dir,
                filter_spec=spec,
                ids=None if id_list is None else keys(h, table_name, id_list),
                pk=graph(h).pks.get(table_name),
            )
        return {"name": table_name, **payload}

    @router.post("/table/{table_name}/rows")
    def table_rows_post(net_id: str, component: str, table_name: str, q: RowsQuery) -> dict[str, Any]:
        """One page; when tinted, ``related[i]`` names why row ``i`` is related (or null)."""
        h = handle(net_id, component)
        with _bad_request():
            src, pk, ids, relation = prepared(h, table_name, q)
            if q.tint is not None:
                relation = related_of(h, sources_of(h, q.tint), q.tint.hops).get(table_name)
            payload = page_table(
                src,
                offset=q.offset,
                limit=q.limit,
                sort=q.sort,
                direction=q.dir,
                filter_spec=q.filter,
                ids=ids or None,
                pk=pk,
            )
        if q.tint is not None or (q.related is not None and q.related_mode == "tint"):
            payload["related"] = row_vias(pd.DataFrame(payload["rows"], columns=payload["columns"]), relation)
        return {"name": table_name, **payload}

    @router.post("/table/{table_name}/locate")
    def locate(net_id: str, component: str, table_name: str, q: LocateQuery) -> dict[str, Any]:
        """``{"index": i}``: the row's position in ``q``'s order (``None``: lazy table, or not in the view)."""
        h = handle(net_id, component)
        with _bad_request():
            src, pk, ids, _ = prepared(h, table_name, q)
            if pk is None:
                return {"index": None}
            key = keys(h, table_name, [q.id])[0]
            index = locate_row(src, key, pk=pk, sort=q.sort, direction=q.dir, filter_spec=q.filter, ids=ids)
        return {"index": index}

    @router.post("/related")
    def related(net_id: str, component: str, q: RelatedQuery) -> dict[str, Any]:
        """Per related table: hop, relation labels and row count; plus link/node ids to tint on the map."""
        h = handle(net_id, component)
        g = graph(h)
        with _bad_request():
            relations = related_of(h, sources_of(h, q), q.hops)
            tables = [
                {"table": n, "hop": r.hop, "via": r.via, "partial": r.partial, "count": count(g.tables[n], r.matches)}
                for n, r in relations.items()
            ]
            drawn: dict[str, Any] = {}
            for name in ("link", "node"):
                relation, pk = relations.get(name), g.pks.get(name)
                if relation is not None and pk:
                    ids, more = ids_of(g.tables[name], relation.matches, pk, limit=MAX_MAP_IDS)
                    drawn[name] = {"ids": ids, "truncated": more}
        return {"hops": q.hops, "tables": tables, "map": drawn}

    return router
