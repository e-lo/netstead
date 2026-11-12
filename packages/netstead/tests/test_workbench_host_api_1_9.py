"""Host API 1.9: the bundled sample ProjectCard catalogs (``sample_catalogs``), readable but never writable."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.errors import ActionError, PathNotAllowed
from netstead.workbench.examples import in_sample_catalog, sample_catalogs
from netstead.workbench.files import list_dir
from netstead.workbench.paths import resolve_readable
from netstead.workbench.plugins import HOST_API, WorkbenchPlugin
from netstead.workbench.plugins.spec import api_compatible

CARDS = leavenworth.cards_dir()


def _session(tmp_path: Path, roots: list[Path]) -> Session:
    env = {
        "NETSTEAD_CONFIG_DIR": str(tmp_path / "user"),
        "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([str(r.resolve()) for r in roots]),
    }
    probe = WorkbenchPlugin(id="probe", name="Probe", version="0", requires_api="1.9")
    return Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[probe], keyring=None)


def test_this_netstead_provides_host_api_1_9_or_newer():
    assert api_compatible("1.9", HOST_API)


def test_cards_dir_is_the_packaged_folder_of_yaml_cards():
    assert CARDS == leavenworth.ROOT / "cards" and CARDS.is_dir()
    assert len(list(CARDS.glob("*.yml"))) == 9 and (CARDS / "README.md").is_file()


def test_the_wheel_includes_the_cards():
    config = tomllib.loads((Path(leavenworth.__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    globs = config["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    assert "netstead/fixtures/**/cards/*.yml" in globs  # README.md is covered by the fixtures' README glob


def test_the_host_lists_the_sample_catalogs_as_copies(tmp_path):
    host = _session(tmp_path, [tmp_path])._hosts["probe"]
    (sample,) = host.sample_catalogs()
    assert sample == {
        "id": "leavenworth",
        "name": "Sample cards (Leavenworth)",
        "path": str(CARDS),
        "network": "leavenworth",
        "cards": 9,
    }
    sample["cards"] = 0  # a copy: the next call is unaffected
    assert host.sample_catalogs()[0]["cards"] == 9


def test_readable_and_list_dir_reach_the_catalog_outside_the_roots(tmp_path):
    host = _session(tmp_path, [tmp_path])._hosts["probe"]
    card = CARDS / "commercial-street-road-diet.yml"
    assert host.readable(CARDS) == CARDS.resolve() and host.readable(card) == card.resolve()
    names = [e["name"] for e in host.list_dir(CARDS)]
    assert "commercial-street-road-diet.yml" in names and all(not n.startswith(".") for n in names)


def test_nothing_around_the_catalog_opens(tmp_path):
    host = _session(tmp_path, [tmp_path])._hosts["probe"]
    for path in (CARDS.parent, CARDS / "..", leavenworth.csv_dir() / "link.csv", CARDS.parent / "datapackage.json"):
        with pytest.raises(PathNotAllowed):
            host.readable(path)
    with pytest.raises(PathNotAllowed):
        host.list_dir(CARDS.parent)
    assert not in_sample_catalog((CARDS / "sub" / "x.yml").resolve())  # one level: a nested file is not reachable
    assert in_sample_catalog((CARDS / "sub" / "x.yml").resolve(), whole_tree=True)


def test_a_catalog_folder_listing_has_no_parent_to_climb_to(tmp_path):
    from netstead.config import Settings

    settings = Settings(io={"allowed_roots": [str(tmp_path)]})
    listing = list_dir(str(CARDS), settings, sample_catalogs=True)
    assert listing["parent"] is None and listing["path"] == str(CARDS.resolve())
    with pytest.raises(PathNotAllowed):
        list_dir(str(CARDS), settings)  # the core file browser still stays inside the roots
    assert resolve_readable(CARDS, settings) == CARDS.resolve()


def test_a_missing_sample_file_is_the_usual_error(tmp_path):
    host = _session(tmp_path, [tmp_path])._hosts["probe"]
    with pytest.raises(ActionError, match="no such file or folder"):
        host.readable(CARDS / "nope.yml")


@pytest.mark.parametrize("where", ["folder", "card", "new", "inside_roots"])
def test_writable_refuses_the_catalog_with_a_clear_message(tmp_path, where):
    roots = [CARDS.parent] if where == "inside_roots" else [tmp_path]
    host = _session(tmp_path, roots)._hosts["probe"]
    path = {"folder": CARDS, "card": CARDS / "commercial-street-road-diet.yml", "new": CARDS / ".trash" / "x.yml"}.get(
        where, CARDS / "new.yml"
    )
    with pytest.raises(ActionError, match=r"bundled sample catalog.*copy its folder"):
        host.writable(path)


def test_writable_still_works_inside_the_roots_and_refuses_outside(tmp_path):
    host = _session(tmp_path, [tmp_path])._hosts["probe"]
    assert host.writable(tmp_path / "mine" / "card.yml") == (tmp_path / "mine" / "card.yml").resolve()
    with pytest.raises(PathNotAllowed):
        host.writable(CARDS.parent / "elsewhere.yml")
    with pytest.raises(PathNotAllowed):
        host.writable("~nosuchuser-for-netstead/x")  # an unusable path is the roots check's to refuse


def test_the_listing_is_computed_once():
    assert sample_catalogs() is sample_catalogs()
