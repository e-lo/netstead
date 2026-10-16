"""HTTP surface of Workbench plugins: status listing, plugin routers, plugin static files, plugin actions."""

from typing import Literal

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin

SECRET = "top secret, outside the plugin's static dir"


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


class Poke(BaseAction):
    type: Literal["broken.poke"] = "broken.poke"


class Prod(BaseAction):
    type: Literal["missing.prod"] = "missing.prod"


class Nudge(BaseAction):
    type: Literal["wrongtype.nudge"] = "wrongtype.nudge"


def _router(host) -> APIRouter:
    router = APIRouter()

    @router.get("/whoami")
    def whoami() -> dict:
        return {"plugin": host.plugin_id}

    @router.post("/echo")
    def echo(body: dict) -> dict:
        return body

    return router


def _broken_router(host) -> APIRouter:
    raise RuntimeError("router exploded")


@pytest.fixture
def client(tmp_path, isolated_env):
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    static = tmp_path / "hello_static"
    static.mkdir()
    (static / "main.js").write_text("export function activate(wb) {}\n", encoding="utf-8")
    hello = WorkbenchPlugin(
        id="hello",
        name="Hello",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Greet, lambda host, a: f"Hello, {a.name}!"),),
        router=_router,
        static_dir=static,
    )
    alive = {"state": lambda host: {"alive": True}}
    broken = WorkbenchPlugin(
        id="broken",
        name="Broken",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Poke, lambda host, a: "poked"),),
        router=_broken_router,
        static_dir=static,
        **alive,
    )
    missing = WorkbenchPlugin(
        id="missing",
        name="Missing",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Prod, lambda host, a: "prodded"),),
        static_dir=tmp_path / "no_such_dir",
        **alive,
    )
    wrongtype = WorkbenchPlugin(  # its router factory returns something that isn't an APIRouter
        id="wrongtype",
        name="Wrong type",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Nudge, lambda host, a: "nudged"),),
        router=lambda host: object(),
        **alive,
    )
    plugins = [hello, broken, missing, wrongtype]
    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=plugins)
    return TestClient(build_app(session))


def test_plugins_listing(client):
    body = client.get("/api/plugins").json()
    assert body["host_api"] == HOST_API
    by_id = {p["id"]: p for p in body["plugins"]}
    assert by_id["hello"]["state"] == "loaded" and by_id["hello"]["frontend"] == "/plugins/hello/main.js"
    assert by_id["broken"]["state"] == "error" and "router exploded" in by_id["broken"]["error"]
    assert by_id["broken"]["frontend"] is None
    assert by_id["missing"]["state"] == "error" and by_id["missing"]["frontend"] is None
    assert "not a directory" in by_id["missing"]["error"]
    assert by_id["wrongtype"]["state"] == "error" and "APIRouter" in by_id["wrongtype"]["error"]


@pytest.mark.parametrize("action", ["broken.poke", "missing.prod", "wrongtype.nudge"])
def test_a_plugin_that_cannot_mount_is_not_installed_at_all(client, action):
    """A failed router or static dir is an install failure: no Actions and no state, not a half-alive plugin."""
    assert client.post("/api/actions", json={"type": action}).status_code == 422
    assert set(client.get("/api/state").json()["plugins"]) == set()


def test_a_mount_that_fails_while_attaching_unloads_the_plugin(tmp_path, isolated_env, monkeypatch):
    import netstead.workbench.server as server

    static = tmp_path / "static"
    static.mkdir()
    real = server.StaticFiles

    def refuse_plugin_dirs(*, directory):
        if directory == static:
            raise RuntimeError("cannot serve")
        return real(directory=directory)

    monkeypatch.setattr(server, "StaticFiles", refuse_plugin_dirs)
    hello = WorkbenchPlugin(
        id="hello",
        name="Hello",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Greet, lambda host, a: "hi"),),
        router=_router,
        static_dir=static,
    )
    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[hello])
    client = TestClient(build_app(session))
    (status,) = client.get("/api/plugins").json()["plugins"]
    assert status["state"] == "error" and "cannot serve" in status["error"] and status["frontend"] is None
    assert client.get("/api/plugins/hello/whoami").status_code == 404  # neither half is attached
    assert client.post("/api/actions", json={"type": "hello.greet", "name": "Ada"}).status_code == 422


def test_plugin_router_and_static_are_mounted(client):
    assert client.get("/api/plugins/hello/whoami").json() == {"plugin": "hello"}
    r = client.get("/plugins/hello/main.js")
    assert r.status_code == 200 and "activate" in r.text


def test_plugin_action_over_http(client):
    r = client.post("/api/actions", json={"type": "hello.greet", "name": "Ada"})
    assert r.status_code == 200 and r.json()["result"] == "Hello, Ada!"
    assert r.json()["entry"]["imports"].endswith("import Greet")


@pytest.mark.parametrize(
    "path",
    [
        "/api/plugins/nope/whoami",  # never installed
        "/plugins/nope/main.js",
        "/api/plugins/broken/whoami",  # its router factory raised: nothing of it is mounted
        "/plugins/broken/main.js",
        "/plugins/missing/main.js",
        "/api/plugins/wrongtype/anything",
    ],
)
def test_a_plugin_that_is_not_loaded_is_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/plugins/hello/../secret.txt",
        "/plugins/hello/../../secret.txt",
        "/plugins/hello/%2e%2e/secret.txt",
        "/plugins/hello/%2E%2E%2Fsecret.txt",
        "/plugins/hello/..%2fsecret.txt",
        "/plugins/hello/..%5csecret.txt",
        "/plugins/hello/%2e%2e%2f%2e%2e%2fsecret.txt",
        "/plugins/hello/%252e%252e/secret.txt",
    ],
)
def test_plugin_static_files_cannot_escape_the_static_dir(client, path):
    """``secret.txt`` sits one level above the static dir. The test client folds a literal ``..`` away
    itself, but the percent-encoded forms reach the app decoded (``/plugins/hello/../secret.txt``),
    exactly as a raw ``..`` from ``curl --path-as-is`` would."""
    r = client.get(path)
    assert r.status_code == 404 and SECRET not in r.text


def test_plugin_router_writes_sit_behind_the_origin_guard(client):
    assert client.post("/api/plugins/hello/echo", json={"a": 1}).json() == {"a": 1}
    foreign = client.post("/api/plugins/hello/echo", json={"a": 1}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    cross = client.post("/api/plugins/hello/echo", json={"a": 1}, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403


def test_plugin_routes_sit_behind_the_host_guard(client):
    assert client.get("/api/plugins/hello/whoami", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/plugins/hello/main.js", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/plugins/hello/echo", json={}, headers={"Host": "evil.example"}).status_code == 400


def test_actions_lists_core_and_plugin_actions_with_owner_and_schema(client):
    actions = {a["type"]: a for a in client.get("/api/actions").json()["actions"]}
    core = actions["open_network"]
    assert core["plugin"] is None and core["name"] == "OpenNetwork" and core["description"]
    greet = actions["hello.greet"]
    assert greet["plugin"] == "hello" and greet["name"] == "Greet" and greet["mutates"] is False
    assert greet["schema"]["required"] == ["name"] and "name" in greet["schema"]["properties"]


def test_plugins_report_host_api_1_1(client):
    assert client.get("/api/plugins").json()["host_api"] == "1.1"
