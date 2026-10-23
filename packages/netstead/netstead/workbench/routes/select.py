"""Selection routes: export a selection (read-only; never recorded)."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from ..selection import export_options, export_selection, fragment_problems, id_captions, selection_warnings
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
        form: Literal["segment", "link_list", "resolved", "query"] = "resolved",
        id_system: Literal["gmns", "osm", "gers"] = "gmns",
        name: str | None = None,
    ) -> dict[str, Any]:
        """The current (or the saved ``name``) selection as a fragment or ProjectCard object, with a file name.

        ``form`` is the Find tab's ``segment`` (facility name + from/to) or ``link_list`` (only the ids), written
        in ``id_system`` (``gmns``, ``osm`` or ``gers``); ``resolved`` / ``query`` are the older forms. ``options``
        says which forms and id systems this selection can use (``{key: reason or null}``), and ``captions`` notes
        ids that several links share.

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
        handle = session.registry.get(sel["net_id"]) if sel["net_id"] in session.registry.ids() else None
        try:
            content = export_selection(sel, what=what, form=form, id_system=id_system, handle=handle)
            captions = id_captions(sel, handle, id_system, form)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        problems = fragment_problems(content) if what == "fragment" else None
        return {
            "filename": f"{_slug(sel['net_id'])}-{_slug(name or 'selection')}-{what}-{form}"
            f"{'' if id_system == 'gmns' else '-' + id_system}.json",
            "content": content,
            "valid": None if problems is None else not problems,
            "problems": problems or [],
            "warnings": [*selection_warnings(sel), *notes],
            "captions": captions,
            "options": export_options(sel, handle, what=what),
        }

    return router
