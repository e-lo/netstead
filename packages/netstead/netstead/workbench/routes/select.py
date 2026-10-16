"""Selection routes: export a selection (read-only; never recorded)."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from ..selection import export_selection, fragment_problems, selection_warnings
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
        """The current (or the saved ``name``) selection as a fragment or ProjectCard object, with a file name.

        A saved selection is resolved again on its network as it is now when that network is open from the same
        source (read-only: the current selection is unchanged). ``warnings`` says what the file may not show: a
        pin that has gone stale (the links are the ones selected before an edit), or a saved selection that could
        not be checked, or was saved on another version.
        """
        try:
            found = session.saved_selection_now(name) if name else (session.selection_copy(), [])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        sel, notes = found if found is not None else (None, [])
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
            "warnings": [*selection_warnings(sel), *notes],
        }

    return router
