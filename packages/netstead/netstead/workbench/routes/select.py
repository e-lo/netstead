"""Selection routes: export a selection (read-only; never recorded)."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from ..selection import export_selection, fragment_problems
from ..session import Session

__all__ = ["select_router"]


def _slug(text: str) -> str:
    """A file-name-safe version of ``text``.

    >>> _slug("two links"), _slug("../x")
    ('two-links', '..-x')
    """
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "selection"


def select_router(session: Session) -> APIRouter:
    """Build the ``/api/selection`` router bound to ``session``."""
    router = APIRouter(prefix="/api/selection")

    @router.get("/export")
    def export(
        what: Literal["fragment", "projectcard"] = "fragment",
        form: Literal["resolved", "query"] = "resolved",
        name: str | None = None,
    ) -> dict[str, Any]:
        """The current (or the saved ``name``) selection as a fragment or ProjectCard object, with a file name."""
        sel = session.saved_selection(name) if name else session.selection_copy()
        if sel is None:
            raise HTTPException(404, f"no saved selection named {name!r}" if name else "nothing is selected")
        try:
            content = export_selection(sel, what=what, form=form)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        problems = fragment_problems(content) if what == "fragment" else None
        return {
            "filename": f"{_slug(sel['net_id'])}-{_slug(name or 'selection')}-{what}-{form}.json",
            "content": content,
            "valid": None if problems is None else not problems,
            "problems": problems or [],
        }

    return router
