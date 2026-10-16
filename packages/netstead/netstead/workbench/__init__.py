"""Netstead Workbench: one live session served as a local web app (``netstead app``).

``Session`` is the action bus; ``build_app``/``serve`` need the ``[server]``
extra (FastAPI + uvicorn) and are imported lazily so ``Session`` works without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .actions import (
    AddRows,
    BuildNetwork,
    ClearSelection,
    CloseNetwork,
    DeleteRows,
    DeleteSelection,
    EditCells,
    Filter,
    FilterCond,
    Navigate,
    OpenNetwork,
    PinAnchor,
    RecordRef,
    RunValidation,
    SaveNetwork,
    SaveSelection,
    Select,
    SetActiveNetwork,
    SetSetting,
    Style,
    UndoEdit,
    UseSelection,
)
from .area import BboxArea, PlaceArea, PointArea
from .errors import ActionError, ApprovalRequired, JobCancelled, NotSupportedYet, PathNotAllowed
from .session import Session

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "ActionError",
    "AddRows",
    "ApprovalRequired",
    "BboxArea",
    "BuildNetwork",
    "ClearSelection",
    "CloseNetwork",
    "DeleteRows",
    "DeleteSelection",
    "EditCells",
    "Filter",
    "FilterCond",
    "JobCancelled",
    "Navigate",
    "NotSupportedYet",
    "OpenNetwork",
    "PathNotAllowed",
    "PinAnchor",
    "PlaceArea",
    "PointArea",
    "RecordRef",
    "RunValidation",
    "SaveNetwork",
    "SaveSelection",
    "Select",
    "Session",
    "SetActiveNetwork",
    "SetSetting",
    "Style",
    "UndoEdit",
    "UseSelection",
    "build_app",
    "serve",
]


def build_app(session: Session) -> FastAPI:
    """Return the workbench FastAPI app over ``session``."""
    from .server import build_app as _build_app

    return _build_app(session)


def serve(session: Session) -> None:
    """Serve ``session`` with uvicorn at ``settings.app.host:port`` (blocks)."""
    import uvicorn

    app_settings = session.settings.app
    uvicorn.run(build_app(session), host=app_settings.host, port=app_settings.port)
