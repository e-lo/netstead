"""/api/assistant: read the conversation, ask, approve, reject, clear; busy is 409; cross-origin is refused."""

import json

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def setup(tmp_path):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([SRC, str(tmp_path)])}
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[])  # stub provider: offline planner
    s.dispatch({"type": "open_network", "source": SRC})
    return s, TestClient(build_app(s))


def test_ask_then_read(setup):
    _, client = setup
    turn = client.post("/api/assistant/ask", json={"text": "color by lanes"}).json()
    assert turn["steps"][0]["status"] == "done"
    j = client.get("/api/assistant").json()
    assert [t["role"] for t in j["turns"]] == ["user", "assistant"] and j["status"]["provider"] == "stub"


def test_draft_approve_and_reject_routes(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1, 2]})
    turn = client.post("/api/assistant/ask", json={"text": "set lanes to 3 on the selection"}).json()
    did = turn["draft_id"]
    assert turn["steps"][0]["status"] == "drafted"
    r = client.post(f"/api/assistant/drafts/{did}/approve")
    assert r.status_code == 200 and r.json()["steps"][0]["status"] == "done"
    assert client.post(f"/api/assistant/drafts/{did}/approve").status_code == 409
    assert client.post("/api/assistant/drafts/999/reject").status_code == 409


def test_bad_bodies_and_busy(setup):
    s, client = setup
    assert client.post("/api/assistant/ask", json={"text": ""}).status_code == 422
    assert client.post("/api/assistant/ask", json={"text": "   "}).status_code == 422
    assert client.post("/api/assistant/ask", json={"text": "x" * 2001}).status_code == 422
    assert client.post("/api/assistant/ask", json={"text": "hi", "extra": 1}).status_code == 422
    s.assistant._busy.acquire()
    try:
        assert client.post("/api/assistant/ask", json={"text": "hi"}).status_code == 409
        assert client.post("/api/assistant/drafts/1/approve").status_code == 409
    finally:
        s.assistant._busy.release()


def test_clear(setup):
    _, client = setup
    client.post("/api/assistant/ask", json={"text": "color by lanes"})
    assert client.post("/api/assistant/clear").status_code == 200
    assert client.get("/api/assistant").json()["turns"] == []


def test_a_cross_origin_ask_is_refused(setup):
    s, client = setup
    r = client.post("/api/assistant/ask", json={"text": "color by lanes"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and s.assistant.turns == []


def test_approve_is_409_when_stale_and_422_for_a_bad_id(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1, 2]})
    turn = client.post("/api/assistant/ask", json={"text": "set lanes to 3 on the selection"}).json()
    s.dispatch({"type": "edit_cells", "ids": [1], "values": {"lanes": 1}})
    r = client.post(f"/api/assistant/drafts/{turn['draft_id']}/approve")
    assert r.status_code == 409 and "changed since" in r.json()["detail"]
    assert client.get("/api/assistant").json()["draft"]["status"] == "stale"
    assert client.post("/api/assistant/drafts/abc/approve").status_code == 422
    assert client.post("/api/assistant/drafts/abc/reject").status_code == 422
