"""The project cards plugin's entry point, ``project_cards = "netstead_project_cards:plugin"``.

It registers the plugin's recorded Actions (``project_cards.*``, :mod:`netstead_project_cards.actions`) and its
state (``plugins.project_cards``: one draft card per network, the cards applied, the open catalog), its read-only
routes (:mod:`netstead_project_cards.routes`), its settings (``[plugins.project_cards]``: :class:`CardsSettings`) and
its front end (``static/main.js``: the "Card" dock panel and the "New change ▸ …" commands; selection → verb → form →
preview → commit, the plugins design's UX principle 2). Every network change goes through core's edit planner and
``host.mutate`` (:func:`netstead_project_cards.lower.apply_change`), so core's history, Edits panel, live warnings and
dirty badge see it. The package imports nothing from netstead except ``netstead.workbench.plugins`` (an import-linter
contract).
"""

from __future__ import annotations

import logging
from pathlib import Path

from netstead.workbench.plugins import ActionError, Host, WorkbenchPlugin
from pydantic import BaseModel, ConfigDict

from ._version import __version__
from .catalog_edits import CatalogEdits
from .flow import load_catalog, open_handle, pending_seqs
from .handlers import Handlers
from .mapping import load_mapping
from .routes import build_router
from .state import CardsState

__all__ = ["REQUIRES_API", "STATIC", "CardsSettings", "plugin"]

logger = logging.getLogger(__name__)

#: The plugin API this plugin is written against (1.7: readable, list_dir and history, for the catalog and the summary).
REQUIRES_API = "1.7"
#: The front end: main.js (the entry module), controller.js, cardmodel.js.
STATIC = Path(__file__).parent / "static"


class CardsSettings(BaseModel):
    """The ``[plugins.project_cards]`` settings table.

    ``catalog_dir`` is a folder of ProjectCard files (inside ``io.allowed_roots``) opened as the catalog when the
    session starts; a folder that cannot be opened is logged and the session starts without one.
    """

    model_config = ConfigDict(extra="forbid")
    catalog_dir: str | None = None


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin whose drafts live in this closure (one per Workbench session)."""
    state = CardsState()
    mapping = load_mapping()
    handlers = Handlers(state, mapping)

    def on_load(host: Host) -> None:
        settings = host.settings
        folder = settings.catalog_dir if isinstance(settings, CardsSettings) else None
        if not folder:
            return
        try:
            state.set_catalog(load_catalog(host, folder, mapping))
        except ActionError as exc:  # PathNotAllowed, or no folder there: the session starts without a catalog
            logger.warning("project_cards: the catalog_dir setting %r was not opened: %s", folder, exc)

    return WorkbenchPlugin(
        id="project_cards",
        name="Project cards",
        version=__version__,
        requires_api=REQUIRES_API,
        actions=(*handlers.specs(), *CatalogEdits(state, mapping).specs()),
        router=lambda host: build_router(host, state, mapping),
        static_dir=STATIC,
        settings_model=CardsSettings,
        state=lambda host: state.snapshot(
            lambda net_id: open_handle(host, net_id), lambda net_id: pending_seqs(host, state, net_id)
        ),
        on_load=on_load,
    )
