"""Session-level routes: state, actions, history, settings, basemap config, and the SSE stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from netstead.viz.styling import basemap_style

from ..actions import script_imports
from ..events import sse_format
from ..session import Session

__all__ = ["core_router"]

#: Seconds between SSE keep-alive comments (keeps proxies from closing idle streams).
KEEPALIVE_S = 15.0


def core_router(session: Session) -> APIRouter:
    """Build the ``/api`` router bound to ``session``."""
    router = APIRouter(prefix="/api")

    @router.get("/state")
    def state() -> dict[str, Any]:
        return session.state()

    @router.get("/config")
    def config() -> dict[str, Any]:
        return {"style": basemap_style(session.settings.viz.basemap)}

    @router.get("/settings")
    def settings() -> dict[str, Any]:
        return session.settings_payload()

    @router.get("/history")
    def history() -> dict[str, Any]:
        """Every entry, plus the import lines that head them as a script ("copy session as Python").

        A nested entry (``parent_seq`` set) is written as a comment, since replaying its parent repeats
        it, so it needs no import.
        """
        entries = list(session.history)
        return {
            "entries": [e.to_dict() for e in entries],
            "imports": script_imports(e.imports for e in entries if e.ok and e.parent_seq is None),
        }

    @router.get("/actions")
    def action_catalog() -> dict[str, Any]:
        """Every Action this session accepts, with its owner and JSON schema. Read-only; nothing is recorded."""
        return {"actions": session.action_catalog()}

    @router.post("/actions")
    def actions(body: dict = Body(...)) -> JSONResponse:  # noqa: B008  (FastAPI Body default)
        """Apply an action. A job action (open/build) answers 202 with its job; the outcome arrives over SSE."""
        try:
            action = session.actions.parse(body)
        except ValidationError as exc:
            detail = exc.errors(include_url=False, include_context=False, include_input=False)  # input may be a key
            return JSONResponse({"error": "invalid action", "detail": jsonable_encoder(detail)}, status_code=422)
        if action.runs_as_job:
            job = session.submit(action)
            payload = {"ok": True, "result": {"job_id": job.id}, "job": session.jobs.snapshot(job)}
            return JSONResponse(jsonable_encoder(payload), status_code=202)
        entry = session.dispatch_recorded(action)
        payload = {"ok": entry.ok, "result": entry.result, "error": entry.error, "entry": entry.to_dict()}
        return JSONResponse(jsonable_encoder(payload), status_code=200 if entry.ok else 400)

    @router.get("/events")
    async def events(request: Request, max_events: int | None = None) -> StreamingResponse:
        """SSE stream: a ``state`` snapshot first, then every published event.

        ``max_events`` closes the stream after that many events (tests and scripted clients).
        """
        queue = session.events.subscribe()

        async def stream() -> AsyncIterator[str]:
            sent = 0
            try:
                snapshot = await asyncio.to_thread(session.state)
                yield sse_format({"type": "state", "state": snapshot})
                sent += 1
                while max_events is None or sent < max_events:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield sse_format(event)
                    sent += 1
            finally:
                session.events.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return router
