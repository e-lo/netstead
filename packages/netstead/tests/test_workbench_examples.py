"""The bundled example networks: the /api/examples route, and opening them from outside io.allowed_roots."""

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.errors import PathNotAllowed
from netstead.workbench.examples import examples
from netstead.workbench.paths import open_locator


@pytest.fixture
def session(tmp_path):
    # allowed_roots excludes the installed fixtures, as it does for a user whose venv is outside their home.
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user"), "NETSTEAD_IO__ALLOWED_ROOTS": f'["{tmp_path}"]'}
    return Session(project_dir=tmp_path, environ=env, parser=StubParser())


def test_examples_route_lists_the_bundled_networks(session):
    body = TestClient(build_app(session)).get("/api/examples").json()
    by_id = {e["id"]: e for e in body}
    assert set(by_id) == {"leavenworth", "rdu_i40", "market_street"}
    leaven = by_id["leavenworth"]
    assert (leaven["links"], leaven["nodes"]) == (339, 121) and leaven["name"] and leaven["description"]
    assert leaven["source"] == str(leavenworth.parquet_dir())
    assert by_id["rdu_i40"]["links"] == 178 and by_id["rdu_i40"]["nodes"] == 143


def test_market_street_is_listed_with_its_overture_counts(session):
    by_id = {e["id"]: e for e in TestClient(build_app(session)).get("/api/examples").json()}
    market = by_id["market_street"]
    assert (market["links"], market["nodes"]) == (186, 129) and "Lotta's Fountain" in market["description"]


def test_examples_are_computed_once():
    assert examples() is examples()


@pytest.mark.parametrize("example_id", ["leavenworth", "rdu_i40", "market_street"])
def test_each_example_opens_through_open_network_outside_the_allowed_roots(session, example_id):
    example = next(e for e in examples() if e["id"] == example_id)
    session.dispatch({"type": "open_network", "source": example["source"], "label": example["name"]})
    net = session.state()["networks"][0]
    assert (net["links"], net["nodes"]) == (example["links"], example["nodes"])


def test_only_the_listed_example_folders_bypass_the_allowed_roots(session):
    settings = session.settings
    assert open_locator(examples()[0]["source"], settings)
    for outside in (leavenworth.ROOT, leavenworth.csv_dir(), leavenworth.parquet_dir() / "link.parquet"):
        with pytest.raises(PathNotAllowed):
            open_locator(str(outside), settings)
