"""The assistant's routes: read the conversation, ask, approve or reject a draft, clear.

The routes are never recorded themselves: the Actions the assistant runs are (with ``via: "assistant"``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..assistant.core import AssistantBusy
from ..session import Session

__all__ = ["assistant_router"]


class AskBody(BaseModel):
    """One question."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    text: str = Field(min_length=1, max_length=2000)


def assistant_router(session: Session) -> APIRouter:
    """Build the ``/api/assistant`` router bound to ``session``.

    The handlers are plain ``def``, so FastAPI runs them on its thread pool: a slow model call blocks neither the
    event loop nor the SSE stream.
    """
    router = APIRouter(prefix="/api/assistant")

    @router.get("")
    def conversation() -> dict[str, Any]:
        """The conversation, the pending draft and the status."""
        return session.assistant.payload()

    @router.post("/ask")
    def ask(body: AskBody) -> dict[str, Any]:
        """Answer one question; the model call can take seconds (a local model longer). Busy is 409."""
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "ask something")
        try:
            return session.assistant.ask(text)
        except AssistantBusy as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/drafts/{draft_id}/approve")
    def approve(draft_id: int) -> dict[str, Any]:
        """Run the pending draft; 409 when it isn't pending, went stale, or the assistant is busy."""
        try:
            return session.assistant.approve(draft_id)
        except (ValueError, AssistantBusy) as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/drafts/{draft_id}/reject")
    def reject(draft_id: int) -> dict[str, Any]:
        """Discard the pending draft; 409 when it isn't pending."""
        try:
            return session.assistant.reject(draft_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/clear")
    def clear() -> dict[str, Any]:
        """Forget the conversation and any pending draft."""
        session.assistant.clear()
        return {"ok": True}

    return router
