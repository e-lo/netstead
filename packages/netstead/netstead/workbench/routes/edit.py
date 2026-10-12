"""Validation and edit views of one network: ``/api/n/{net_id}/{component}/issues``, ``/report.html``, ``/edits``.

Read-only: they show what actions produced (``RunValidation``, ``EditCells``, ``DeleteRows``, ...), and the
delete plan is a dry run. Nothing here is recorded in the session history. Issue messages can quote data
values: the JSON routes carry them as data, and the report page is rendered with autoescaping.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from ..actions import MAX_EDIT_IDS
from ..issues import MAX_MARKERS
from ..session import Session
from .common import network_handle

__all__ = ["MAX_ISSUE_PAGE", "DeletePlanQuery", "edit_router"]

#: Issues returned in one page.
MAX_ISSUE_PAGE = 500


def _record(text: str | None) -> tuple[str, str] | None:
    """``"<table>:<key>"`` as ``(table, key)``; 422 when it is not that shape."""
    if not text:
        return None
    table, sep, key = text.partition(":")
    if not (sep and table and key):
        raise HTTPException(422, "record is <table>:<key>")
    return table, key


class DeletePlanQuery(BaseModel):
    """The keys a deletion would start from."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    ids: list[int | str] = Field(min_length=1, max_length=MAX_EDIT_IDS)


def edit_router(session: Session) -> APIRouter:
    """Build the validation and edit routes bound to ``session``."""
    router = APIRouter(prefix="/api/n/{net_id}/{component}")

    @router.get("/issues")
    def issues(
        net_id: str,
        component: str,
        severity: str | None = None,
        table: str | None = None,
        code: str | None = None,
        located: Literal["yes", "no"] | None = None,
        record: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=MAX_ISSUE_PAGE),
    ) -> dict[str, Any]:
        """One page of the issues that match every filter, with the set's version, staleness and facets."""
        h = network_handle(session, net_id, component)
        found = _record(record)
        s = session.issue_set(h.id)
        if s is None:
            return {
                "net_id": h.id,
                "version": None,
                "current_version": h.version,
                "stale": False,
                "total": 0,
                "unlocated": 0,
                "facets": {},
                "offset": 0,
                "issues": [],
            }
        rows = s.query(
            severity=[v for v in (severity or "").split(",") if v],
            table=table,
            code=code,
            located=None if located is None else located == "yes",
            record=found,
        )
        return {
            "net_id": h.id,
            "version": s.version,
            "current_version": h.version,
            "stale": s.version != h.version,
            "total": len(rows),
            "unlocated": sum(1 for i in s.issues if i["anchor"] is None),
            "facets": s.facets(),
            "offset": offset,
            "issues": rows[offset : offset + limit],
        }

    @router.get("/issues/markers")
    def markers(net_id: str, component: str) -> dict[str, Any]:
        """Located issues for the map (number, severity, anchor, record), plus issue numbers per record."""
        h = network_handle(session, net_id, component)
        s = session.issue_set(h.id)
        if s is None:
            return {"version": None, "stale": False, "truncated": False, "markers": [], "records": {}}
        located = [i for i in s.issues if i["anchor"] is not None]
        fields = ("i", "severity", "anchor", "table", "key")
        return {
            "version": s.version,
            "stale": s.version != h.version,
            "truncated": len(located) > MAX_MARKERS,
            "markers": [{k: i[k] for k in fields} for i in located[:MAX_MARKERS]],
            "records": s.by_record(),
        }

    @router.get("/report.html")
    def report(net_id: str, component: str) -> HTMLResponse:
        """The offline validation report (the ``render_validation_html`` page) for the last, still-current run."""
        h = network_handle(session, net_id, component)
        s = session.issue_set(h.id)
        if s is None:
            raise HTTPException(409, "run validation first")
        if s.version != h.version:
            raise HTTPException(409, "the network changed since validation; re-run it, then export the report")
        from netstead.map import render_validation_html  # jinja2 is a corral dependency: always present

        html = render_validation_html(h.roadway, s.report, title=f"validation: {h.label}")
        headers = {  # network ids are slugs, so the filename needs no quoting
            "Content-Disposition": f'attachment; filename="{h.id}-validation.html"',
            "X-Content-Type-Options": "nosniff",
        }
        return HTMLResponse(html, headers=headers)

    @router.get("/edits")
    def edits(net_id: str, component: str) -> dict[str, Any]:
        """Pending edits, undo and dirty state, and the live edit warnings (always current, never stale)."""
        return session.edits_payload(network_handle(session, net_id, component))

    @router.post("/table/{table_name}/delete-plan")
    def delete_plan(net_id: str, component: str, table_name: str, q: DeletePlanQuery) -> dict[str, Any]:
        """A dry run of ``DeleteRows``: rows per table that would go, or why the deletion is refused."""
        return session.delete_preview(network_handle(session, net_id, component), table_name, q.ids)

    return router
