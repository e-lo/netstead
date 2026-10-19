"""The project cards plugin's entry point, ``project_cards = "netstead_project_cards:plugin"``.

Selection → verb → form → preview → commit (the plugins design's UX principle 2). Every network change goes through
core's edit planner and ``host.mutate``, so core's history, Edits panel, live warnings and dirty badge see it. The
package imports nothing from netstead except ``netstead.workbench.plugins`` (an import-linter contract).
"""

from __future__ import annotations

from netstead.workbench.plugins import WorkbenchPlugin

from ._version import __version__

__all__ = ["REQUIRES_API", "plugin"]

#: The plugin API this plugin is written against (1.5: ActionError and NotSupportedYet).
REQUIRES_API = "1.5"


def plugin() -> WorkbenchPlugin:
    """The entry point: a fresh plugin for one Workbench session."""
    return WorkbenchPlugin(id="project_cards", name="Project cards", version=__version__, requires_api=REQUIRES_API)
