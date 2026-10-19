"""The package installs as the Workbench plugin ``project_cards`` and loads on this netstead."""

from importlib.metadata import entry_points

import netstead_project_cards
from netstead.workbench.plugins import ENTRY_POINT_GROUP, HOST_API
from netstead.workbench.plugins.spec import api_compatible
from netstead_project_cards import plugin


def test_the_entry_point_names_the_factory():
    (ep,) = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == "project_cards"]
    assert ep.value == "netstead_project_cards:plugin" and ep.load() is plugin


def test_the_plugin_needs_host_api_1_5_and_runs_on_this_one():
    built = plugin()
    assert (built.id, built.requires_api, built.version) == ("project_cards", "1.5", netstead_project_cards.__version__)
    assert api_compatible(built.requires_api, HOST_API)


def test_it_loads_in_a_session(make_session):
    session = make_session(open_network=False)
    (status,) = [s for s in session.plugin_status if s.id == "project_cards"]
    assert status.state == "loaded", status.error


def test_projectcard_imports_under_the_warnings_policy():
    import projectcard  # its utils.py has an invalid escape: see the SyntaxWarning filter in pyproject.toml

    assert projectcard.__version__.startswith("0.3.")
