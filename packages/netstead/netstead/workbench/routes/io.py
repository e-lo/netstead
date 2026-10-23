"""Read-only helpers for the Open / Import wizard, plus the jobs list and cancel.

None of these are recorded as actions: browsing, checking a URL, searching for a place, and
estimating a build change no session state. (Cancel acts on a job, not on the session; the
canceled action is recorded when its job ends.) All of them sit behind the app-wide loopback
Host/Origin guard in :mod:`netstead.workbench.server`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .. import build
from ..actions import BuildNetwork
from ..errors import ActionError, PathNotAllowed
from ..estimate import needs_approval
from ..extras import optional_module
from ..files import list_dir
from ..session import Session
from ..urlcheck import check_url

__all__ = ["io_router"]


def io_router(session: Session) -> APIRouter:
    """Build the wizard/jobs router bound to ``session``."""
    router = APIRouter(prefix="/api")

    @router.get("/fs/list")
    def fs_list(path: str | None = None) -> dict[str, Any]:
        """List a folder inside ``io.allowed_roots`` (no ``path``: the roots themselves)."""
        try:
            return list_dir(path, session.settings)
        except PathNotAllowed as exc:
            raise HTTPException(403, str(exc)) from exc
        except PermissionError as exc:  # inside the roots, but the OS won't let us read it: not a 500
            raise HTTPException(403, f"permission denied: {path}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except NotADirectoryError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/check-url")
    def check(body: dict = Body(...)) -> dict[str, Any]:  # noqa: B008  (FastAPI Body default)
        """Reachability, credential *source* name, and tables for a remote GMNS URL."""
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(422, 'body must be {"url": "..."}')
        return check_url(url.strip())

    @router.get("/geocode")
    def geocode(q: str, limit: int = 8) -> dict[str, Any]:
        """Nominatim place candidates (bbox + simplified outline) for the Area step."""
        try:
            osm_query = optional_module("netstead.osm.query", "osm")
        except ActionError as exc:
            raise HTTPException(501, str(exc)) from exc
        osm = session.settings.osm
        try:
            found = osm_query.geocode_candidates(
                q,
                limit=max(1, min(limit, 20)),
                session=session.http,
                user_agent=osm.user_agent or osm_query.USER_AGENT,
                timeout=10,
                retries=1,
            )
        except Exception as exc:  # boundary: a geocoder failure is shown to the user, not a 500
            raise HTTPException(502, f"place search failed: {type(exc).__name__}: {exc}") from exc
        return {"candidates": found}

    @router.post("/estimate")
    def estimate(body: dict = Body(...)) -> JSONResponse:  # noqa: B008  (FastAPI Body default)
        """Size a ``build_network`` request (pre-query + model) without running it."""
        try:
            action = BuildNetwork.model_validate({k: v for k, v in body.items() if k != "type"})
        except ValidationError as exc:
            detail = exc.errors(include_url=False, include_context=False, include_input=False)
            return JSONResponse({"error": "invalid build", "detail": jsonable_encoder(detail)}, status_code=422)
        settings = session.settings
        try:
            plan = build.plan_build(action, settings)
        except ActionError as exc:
            return JSONResponse({"error": str(exc), "error_type": type(exc).__name__}, status_code=400)
        est = build.estimate_for(action, plan, settings, http=session.http)
        threshold = settings.app.approve_above_s
        return JSONResponse(
            {"estimate": est.to_dict(), "needs_approval": needs_approval(est, threshold), "threshold_s": threshold}
        )

    @router.get("/jobs")
    def jobs() -> dict[str, Any]:
        """Every background job this session started, newest first."""
        return {"jobs": jsonable_encoder(session.jobs.snapshots())}

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        """Request cancellation; it takes effect at the job's next stage boundary."""
        try:
            return jsonable_encoder(session.jobs.cancel(job_id))
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc

    return router
