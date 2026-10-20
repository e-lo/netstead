"""Workbench plugins: separately installed packages that add Actions, routes, settings, state and UI.

A plugin package exposes a zero-argument factory returning a :class:`WorkbenchPlugin` under the
``netstead.workbench.plugins`` entry-point group (the entry-point name must equal the plugin id).
Plugins import only what this package exports. See the cookbook page "Write a Workbench plugin".
``EditPlan`` / ``EditRefused`` come from core's edit planner (``host.plan_*``). A handler raises ``ActionError``
(or ``NotSupportedYet``) to refuse with a message the user sees; any other exception is an internal error.
"""

from ..actions import BaseAction
from ..editing import EditPlan, EditRefused
from ..errors import ActionError, NotSupportedYet
from .discovery import ENTRY_POINT_GROUP, PluginStatus, discover
from .host import Host, Mutation, PendingEdits
from .spec import HOST_API, ActionSpec, WorkbenchPlugin

__all__ = [
    "ENTRY_POINT_GROUP",
    "HOST_API",
    "ActionError",
    "ActionSpec",
    "BaseAction",
    "EditPlan",
    "EditRefused",
    "Host",
    "Mutation",
    "NotSupportedYet",
    "PendingEdits",
    "PluginStatus",
    "WorkbenchPlugin",
    "discover",
]
