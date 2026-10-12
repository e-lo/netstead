"""Helpers shared by the per-network routers (``/api/n/{net_id}/{component}/...``)."""

from __future__ import annotations

from fastapi import HTTPException

from ..registry import NetworkHandle
from ..session import Session

__all__ = ["network_handle"]


def network_handle(session: Session, net_id: str, component: str) -> NetworkHandle:
    """The open handle for ``net_id`` (404 when unknown); ``transit`` answers 501 until phase P6."""
    if component == "transit":
        raise HTTPException(501, "the transit component is not supported yet (phase P6)")
    if component != "roadway":
        raise HTTPException(404, f"unknown component {component!r}")
    try:
        return session.registry.get(net_id)
    except KeyError as exc:
        raise HTTPException(404, exc.args[0]) from exc
