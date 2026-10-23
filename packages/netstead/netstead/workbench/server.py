"""Assemble the workbench FastAPI app: static front end + API routers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .plugins import HOST_API
from .redact import describe_error
from .routes.assistant import assistant_router
from .routes.core import core_router
from .routes.edit import edit_router
from .routes.io import io_router
from .routes.llm import llm_router
from .routes.network import network_router
from .routes.select import select_router
from .session import Session

__all__ = ["STATIC_DIR", "build_app", "is_loopback_host"]

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

#: Hosts a loopback bind trusts, as lowercased ``_host_name()`` output.
#: "testserver" is what Starlette's TestClient sends as its Host header.
_LOOPBACK_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}

#: Bind hosts considered loopback-only (not reachable off the local machine).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: HTTP methods that can't mutate state, so cross-origin requests for them are harmless
#: (the Host-header guard above already blocks DNS-rebinding reads from another machine).
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_loopback_host(host: str) -> bool:
    """Whether ``host`` (a bind address, no port) is loopback-only."""
    return host in _LOOPBACK_HOSTS


def _origin_hostname(origin: str) -> str:
    """Lowercased, bracket-preserving hostname of an ``Origin`` header value.

    ``urlsplit(...).hostname`` already lowercases and strips brackets from an
    IPv6 literal; we re-add brackets so the result compares directly against
    ``_host_name()``'s bracketed IPv6 output.
    """
    hostname = urlsplit(origin).hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return hostname


def _host_name(header: str) -> str:
    """Return the hostname portion of a ``Host`` header, with any ``:port`` stripped.

    Bracket-aware: an IPv6 literal like ``[::1]:8850`` contains colons of its
    own, so a plain ``split(":")[0]`` would truncate it to ``"["``. For a
    bracketed host we keep everything through the closing ``]``; otherwise we
    strip at most one trailing ``:port`` from the right.
    """
    if header.startswith("["):
        end = header.find("]")
        name = header if end == -1 else header[: end + 1]
    else:
        name = header.rsplit(":", 1)[0] if ":" in header else header
    return name.lower()


#: Fields of a request-validation error that are safe to return: never ``input`` or ``ctx``,
#: either of which can carry the submitted value (an API key, a token in a URL).
_SAFE_ERROR_FIELDS = ("type", "loc", "msg")


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 for a body/query FastAPI itself rejects, without echoing what was sent (FastAPI's default does)."""
    detail = [{k: err[k] for k in _SAFE_ERROR_FIELDS if k in err} for err in exc.errors()]
    return JSONResponse({"error": "invalid request", "detail": jsonable_encoder(detail)}, status_code=422)


def build_app(session: Session) -> FastAPI:
    """Return the FastAPI app serving ``session``."""
    app = FastAPI(title="Netstead Workbench")
    app.add_exception_handler(RequestValidationError, _validation_error)
    if is_loopback_host(session.settings.app.host):
        # Binding to loopback only stops *other machines* from connecting; a
        # malicious web page on the same machine can still reach us via DNS
        # rebinding (a hostname that resolves to 127.0.0.1). Reject requests
        # whose Host header isn't one we expect. A non-loopback bind means the
        # user deliberately exposed the server, so we don't gate on Host there
        # (auth for that case is a separate, P1 item).
        @app.middleware("http")
        async def _check_host(request: Request, call_next):
            if _host_name(request.headers.get("host", "")) not in _LOOPBACK_ALLOWED_HOSTS:
                return PlainTextResponse("Invalid host header", status_code=400)
            # Local CSRF guard: a cross-origin page can still POST here without
            # a CORS preflight (a "simple" request needs no custom header, and
            # recent fastapi's strict_content_type default only blocks a
            # *missing* Content-Type — a guarantee we can't rely on across
            # supported fastapi versions). For state-changing methods, reject
            # any request that names a foreign origin, by either signal a
            # browser can send on its own. Reads are left alone: they're
            # harmless, and the Host check above already covers DNS rebinding.
            if request.method not in _SAFE_METHODS:
                origin = request.headers.get("origin")
                if origin is not None and (origin == "null" or _origin_hostname(origin) not in _LOOPBACK_ALLOWED_HOSTS):
                    return PlainTextResponse("Cross-origin request rejected", status_code=403)
                if request.headers.get("sec-fetch-site") == "cross-site":
                    return PlainTextResponse("Cross-origin request rejected", status_code=403)
            return await call_next(request)

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(core_router(session))
    app.include_router(io_router(session))
    app.include_router(network_router(session))
    app.include_router(edit_router(session))
    app.include_router(select_router(session))
    app.include_router(assistant_router(session))
    # Key writes are refused on an exposed bind: there is no auth beyond the loopback guard (design T10).
    app.include_router(llm_router(session, allow_key_writes=is_loopback_host(session.settings.app.host)))
    _mount_plugins(app, session)

    @app.get("/api/plugins")
    def plugins() -> dict:
        return {"host_api": HOST_API, "plugins": [s.to_dict() for s in session.plugin_status]}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must revalidate (``Cache-Control: no-cache``) before reusing.

    The front end is ES modules importing one another; a heuristically cached old module next to a fresh one
    breaks the page after an upgrade (a missing export is a SyntaxError). Revalidation is cheap: an unchanged
    file answers 304 against its ETag.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_plugins(app: FastAPI, session: Session) -> None:
    """Attach each installed plugin's router (``/api/plugins/<id>``) and static dir (``/plugins/<id>``).

    Both sit behind the same Host-header and Origin guards as every core route (the middleware wraps
    the whole app). The session built and checked them when it installed the plugin; here both are
    staged inside one failure boundary before either is attached, so a plugin is mounted whole or
    not at all. If staging fails the plugin is unloaded (no Actions, no state) and marked ``error``.
    """
    for plugin_id, (router, static_dir) in session.plugin_mounts().items():
        try:
            staged = APIRouter()
            if router is not None:
                staged.include_router(router, prefix=f"/api/plugins/{plugin_id}")
            static = RevalidatedStaticFiles(directory=static_dir) if static_dir is not None else None
        # boundary: third-party code must not stop the app (not even with ``sys.exit``)
        except (Exception, SystemExit) as exc:
            logger.exception("mounting workbench plugin %r failed", plugin_id)
            session.unload_plugin(plugin_id, describe_error(exc))
            continue
        app.include_router(staged)
        if static is not None:
            app.mount(f"/plugins/{plugin_id}", static, name=f"plugin-{plugin_id}")
