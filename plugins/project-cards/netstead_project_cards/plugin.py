"""The project cards plugin's entry point, ``project_cards = "netstead_project_cards:plugin"``.

It registers the plugin's recorded Actions (``project_cards.*``, :mod:`netstead_project_cards.actions`) and its
state (``plugins.project_cards``: one draft card per network), and two read-only routes for its forms
(:mod:`netstead_project_cards.routes`); the panel (selection → verb → form → preview → commit, the plugins design's
UX principle 2) is upcoming. Every network change goes through core's edit planner and
``host.mutate`` (:func:`netstead_project_cards.lower.apply_change`), so core's history, Edits panel, live warnings
and dirty badge see it. The package imports nothing from netstead except
``netstead.workbench.plugins`` (an import-linter contract).
"""

from __future__ import annotations

from netstead.workbench.plugins import WorkbenchPlugin

from ._version import __version__
from .flow import open_handle
from .handlers import Handlers
from .mapping import load_mapping
from .routes import build_router
from .state import CardsState

__all__ = ["REQUIRES_API", "plugin"]

#: The plugin API this plugin is written against (1.5: ActionError and NotSupportedYet).
REQUIRES_API = "1.5"


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin whose drafts live in this closure (one per Workbench session)."""
    state = CardsState()
    mapping = load_mapping()
    handlers = Handlers(state, mapping)
    return WorkbenchPlugin(
        id="project_cards",
        name="Project cards",
        version=__version__,
        requires_api=REQUIRES_API,
        actions=handlers.specs(),
        router=lambda host: build_router(host, state, mapping),
        state=lambda host: state.snapshot(lambda net_id: open_handle(host, net_id)),
    )
