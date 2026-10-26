"""FastAPI app factory + generic package endpoints (corral.api, task 4.10).

This is the framework half of architecture §6.8: a thin
:class:`fastapi.FastAPI` factory that wires the configured packages
behind bearer auth and exposes the four generic endpoints. Domain
packages (e.g. :mod:`netstead.server`) call this then attach their own
routers — same composition pattern as the CLI's
:func:`corral.cli.app.build_app`.

Endpoints (v1):

* ``GET /health`` — always 200; no auth required (load-balancer probe).
* ``GET /packages`` — list configured package ids + descriptions.
* ``GET /packages/{id}`` — package metadata (table list + row counts).
* ``GET /packages/{id}/spec`` — resolved Frictionless DataPackage JSON.
* ``POST /packages/{id}/validate`` — run full validation, return
  :class:`~corral.reports.ValidationReport` as JSON via
  :meth:`~corral.reports.ValidationReport.to_dict` (the canonical
  wire shape; matches the CLI ``--json`` + MCP ``validate_package``
  shapes exactly).

Defaults intentionally match the CLI's ``--json`` contract so an MCP
client / agent / curl invocation gets the same shape it would get
from the local commands.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from corral.dataset import Package

from .auth import build_auth_dependency
from .config import ServerSettings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter


__all__ = [
    "AuthDep",
    "ExtraRouterFactory",
    "PackageLoader",
    "PackageRegistry",
    "build_app",
]

logger = logging.getLogger(__name__)


#: Type alias for the auth dependency callable that endpoints depend on
#: via :class:`fastapi.Depends`. The callable receives the request's
#: ``Authorization`` header (or ``None``) and returns ``None`` on pass
#: or raises :class:`fastapi.HTTPException`. Domain extensions that
#: write their own routers accept one of these to gate their endpoints.
AuthDep = Callable[[str | None], None]


#: Type alias for the package-loader injected into
#: :class:`PackageRegistry`. Domain extensions pass their own loader
#: (e.g. :meth:`netstead.Network.from_source`) so the registry caches
#: the domain-typed instance rather than a bare :class:`Package`.
PackageLoader = Callable[[str], Package]


class ExtraRouterFactory(Protocol):
    """Callable shape accepted by :func:`build_app` for domain router extensions.

    A factory receives the live :class:`PackageRegistry` and the auth
    dependency, and returns a :class:`fastapi.APIRouter` mounted on
    top of the generic routes.

    Examples:
        Minimal extension that adds a ``/extras/{id}`` endpoint::

            from fastapi import APIRouter, Depends
            from corral.api import build_app, AuthDep, PackageRegistry

            def my_factory(registry: PackageRegistry, auth_dep: AuthDep) -> APIRouter:
                router = APIRouter(prefix="/extras", tags=["extras"])

                @router.get("/{pkg_id}", dependencies=[Depends(auth_dep)])
                def get_extra(pkg_id: str) -> dict:
                    return registry.describe(pkg_id)

                return router

            app = build_app(settings, extra_router_factory=my_factory)
    """

    def __call__(self, registry: PackageRegistry, auth_dep: AuthDep) -> APIRouter:
        """Return a router to mount alongside the generic routes."""
        ...


class PackageRegistry:
    """Lazy registry mapping public id → :class:`Package` (or subclass).

    Built once at app startup from :class:`ServerSettings.packages`;
    each :meth:`get` materializes the package on first access via the
    configured ``loader`` (default :meth:`Package.from_source`) and
    caches the result. Hot-reload + cache invalidation are out of
    scope for v1 — restart the server to pick up a config change.

    Domain extensions inject a loader to cache the right type. For
    example, :func:`netstead.server.build_app` passes
    ``Network.from_source`` so the cache holds :class:`Network`
    instances and the ``/networks/{id}`` handler can read
    ``pkg.spec_version`` directly without a second load.
    """

    def __init__(self, settings: ServerSettings, *, loader: PackageLoader | None = None) -> None:
        """Index settings by public id and remember which loader to use on first access.

        Args:
            settings: The server config; only the ``packages`` list is
                consumed here.
            loader: Callable that takes a source string and returns a
                :class:`Package` (or subclass). Defaults to
                :meth:`Package.from_source`.
        """
        self._refs = {pkg.id: pkg for pkg in settings.packages}
        self._cache: dict[str, Package] = {}
        # Default loader is the bare Package.from_source; domain extensions
        # (netstead) pass Network.from_source so the cache holds the richer
        # type and downstream endpoints can read spec_version directly.
        self._loader: PackageLoader = loader or Package.from_source

    def list_ids(self) -> list[str]:
        """Return all configured public ids (insertion order)."""
        return list(self._refs)

    def describe(self, pkg_id: str) -> dict[str, Any]:
        """Return ``{id, source, description}`` without loading the package."""
        if pkg_id not in self._refs:
            raise KeyError(pkg_id)
        ref = self._refs[pkg_id]
        return {"id": ref.id, "source": ref.source, "description": ref.description}

    def source_for(self, pkg_id: str) -> str:
        """Return the configured source string for ``pkg_id``.

        Public surface for domain routers that need to re-resolve the
        original source (e.g. via a domain-specific factory like
        :meth:`Network.from_source`). Use this instead of reaching
        into the private ``_refs`` dict.
        """
        if pkg_id not in self._refs:
            raise KeyError(pkg_id)
        return self._refs[pkg_id].source

    def get(self, pkg_id: str) -> Package:
        """Load + cache the package for ``pkg_id`` via the configured loader.

        Returns whatever type the loader returns (subclasses of
        :class:`Package` are explicitly supported and common —
        :class:`netstead.Network` being the canonical case).
        """
        if pkg_id in self._cache:
            return self._cache[pkg_id]
        if pkg_id not in self._refs:
            raise KeyError(pkg_id)
        pkg = self._loader(self._refs[pkg_id].source)
        self._cache[pkg_id] = pkg
        return pkg

    def require(self, pkg_id: str) -> Package:
        """Return the package for ``pkg_id`` or raise :class:`fastapi.HTTPException(404)`.

        Public surface for endpoint handlers — both the generic
        corral routes and domain extensions (netstead.server). Replaces
        the previous private ``_safe_get`` helper.
        """
        try:
            return self.get(pkg_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"package {pkg_id!r} not configured",
            ) from None


def build_app(
    settings: ServerSettings | None = None,
    *,
    package_loader: PackageLoader | None = None,
    extra_router_factory: ExtraRouterFactory | None = None,
) -> FastAPI:
    """Return a :class:`FastAPI` wired with the generic corral endpoints.

    Args:
        settings: :class:`ServerSettings` to mount. Defaults to
            :class:`ServerSettings` defaults (localhost, no packages).
        package_loader: Callable that loads a package from a source
            string. Domain extensions pass their own loader so the
            registry caches the right type (e.g.
            ``Network.from_source``). Defaults to ``Package.from_source``.
        extra_router_factory: Optional callable used by domain packages
            (netstead) to attach extra routers. See
            :class:`ExtraRouterFactory` for the contract + an example.

    Returns:
        A fully wired :class:`FastAPI` instance. The caller passes it
        to :func:`uvicorn.run` (or any ASGI server).
    """
    settings = settings or ServerSettings()
    settings.warn_on_unsafe_combinations()
    registry = PackageRegistry(settings, loader=package_loader)
    auth_dep = build_auth_dependency(settings.auth)

    app = FastAPI(
        title="corral API",
        description=(
            "Generic Frictionless data-package HTTP service. Every endpoint matches "
            "the CLI's --json contract for response shape."
        ),
        version="0.1.0",
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe. No auth; safe to expose to load balancers."""
        return {"status": "ok"}

    @app.get("/packages", dependencies=[Depends(auth_dep)], tags=["packages"])
    def list_packages() -> list[dict[str, Any]]:
        """List all configured package ids (without materializing them)."""
        return [registry.describe(pid) for pid in registry.list_ids()]

    @app.get("/packages/{pkg_id}", dependencies=[Depends(auth_dep)], tags=["packages"])
    def get_package(pkg_id: str) -> dict[str, Any]:
        """Return metadata about ``pkg_id`` — table list + row counts."""
        pkg = registry.require(pkg_id)
        tables = []
        for name, table in pkg.tables.items():
            try:
                tables.append({"name": name, "rows": table.count(), "columns": table.columns()})
            except Exception as exc:  # pragma: no cover - per-table resilience
                logger.warning("get_package: skipping table %r — %s", name, exc)
        return {
            "id": pkg_id,
            "name": pkg.spec.name,
            "engine": type(pkg.engine).__name__,
            "table_count": len(pkg.tables),
            "tables": tables,
        }

    @app.get("/packages/{pkg_id}/spec", dependencies=[Depends(auth_dep)], tags=["packages"])
    def get_spec(pkg_id: str) -> dict[str, Any]:
        """Return the resolved Frictionless ``DataPackage`` as JSON."""
        pkg = registry.require(pkg_id)
        return pkg.spec.model_dump(mode="json")

    @app.post("/packages/{pkg_id}/validate", dependencies=[Depends(auth_dep)], tags=["packages"])
    def validate_package(pkg_id: str) -> dict[str, Any]:
        """Run full validation; return :meth:`ValidationReport.to_dict` (canonical shape)."""
        pkg = registry.require(pkg_id)
        report = pkg.validate()
        return report.to_dict()

    # Domain extensions (e.g. netstead.server) layer on via a router factory.
    if extra_router_factory is not None:
        router = extra_router_factory(registry, auth_dep)
        app.include_router(router)

    return app


# The fastapi import isn't actually used by anything below this line,
# but Header is referenced by the AuthDep type alias above for clarity
# (FastAPI auto-injects via Annotated[..., Header()] in dependencies).
_ = Header
