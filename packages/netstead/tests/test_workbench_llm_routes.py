"""Tests for the /api/llm routes: status only, write-only keys, guards, and the canary (no key ever leaks)."""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netstead.llm.secrets import KEYRING_SERVICE
from netstead.workbench import OpenNetwork, Session, build_app

pytestmark = pytest.mark.usefixtures("no_network")

SECRETS = {"X-Netstead-Secrets": "1"}
KEY = "sk-ant-api03-ROUTEKEYabcdefghijklmnop"
UTTERANCE = "I-40 EB between South Miami Boulevard and Airport Boulevard"
ROW_KEYS = {
    "provider", "label", "kind", "base_url", "local", "default_model", "key_env", "sends",
    "configured", "source", "usable", "error", "models", "model",
}  # fmt: skip
TOOL_REPLY = {
    "content": [
        {
            "type": "tool_use",
            "id": "t",
            "name": "emit_selection_intent",
            "input": {
                "facility": {"ref": "I 40", "direction": "EB"},
                "from_anchor": "South Miami Boulevard",
                "to_anchor": "Airport Boulevard",
            },
        }
    ],
    "stop_reason": "tool_use",
}


@pytest.fixture
def isolated_env(isolated_env):
    """These tests drive the language-model parser: the offline grammar must not answer first."""
    return {**isolated_env, "NETSTEAD_SELECT__GRAMMAR_FIRST": "false"}


@pytest.fixture
def session(tmp_path, isolated_env, fake_keyring, fake_api):
    return Session(project_dir=tmp_path, environ=isolated_env, keyring=fake_keyring, llm_transport=fake_api.transport())


@pytest.fixture
def client(session):
    return TestClient(build_app(session))


def test_providers_status_shape_has_no_key_fields(client, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "qwen3:8b"}]})
    data = client.get("/api/llm/providers").json()
    assert [r["provider"] for r in data["providers"]] == ["anthropic", "openai", "gemini", "ollama"]
    assert all(set(row) == ROW_KEYS for row in data["providers"])
    ollama = data["providers"][-1]
    assert (ollama["usable"], ollama["local"]) == (True, True)
    assert (data["keyring"], data["key_writes"], data["selected"]) == (True, True, {"provider": "stub", "model": None})


def test_put_key_stores_in_keyring_and_returns_status_only(client, fake_keyring):
    r = client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    assert r.status_code == 200 and "ROUTEKEY" not in r.text
    assert fake_keyring.store[(KEYRING_SERVICE, "anthropic")] == KEY
    row = r.json()["providers"][0]
    assert (row["configured"], row["source"], row["usable"]) == (True, "keyring", True)


def test_key_writes_need_the_secrets_header(client):
    assert client.put("/api/llm/keys/anthropic", json={"key": KEY}).status_code == 403
    assert client.delete("/api/llm/keys/anthropic").status_code == 403
    assert client.post("/api/llm/test", json={"provider": "anthropic"}).status_code == 403


def test_cross_origin_key_write_is_rejected_by_the_middleware(client):
    r = client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers={**SECRETS, "origin": "http://evil.example"})
    assert r.status_code == 403 and "Cross-origin" in r.text


def test_bad_key_bodies_are_422_without_echo(client):
    short = client.put("/api/llm/keys/anthropic", json={"key": "abc1234"}, headers=SECRETS)
    extra = client.put("/api/llm/keys/anthropic", json={"key": KEY, "note": 1}, headers=SECRETS)
    assert (short.status_code, extra.status_code) == (422, 422)
    assert "abc1234" not in short.text and "ROUTEKEY" not in extra.text


def test_local_provider_takes_no_key_and_unknown_is_404(client):
    assert client.put("/api/llm/keys/ollama", json={"key": KEY}, headers=SECRETS).status_code == 400
    assert client.put("/api/llm/keys/mistral", json={"key": KEY}, headers=SECRETS).status_code == 404


def test_delete_key(client, fake_keyring):
    client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    r = client.delete("/api/llm/keys/anthropic", headers=SECRETS)
    assert r.status_code == 200 and r.json()["providers"][0]["configured"] is False and fake_keyring.store == {}


def test_connection_test_reports_catalog_drift(client, fake_api):
    client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    fake_api.add("GET", "/v1/models", body={"data": [{"id": "claude-sonnet-5"}]})
    result = client.post("/api/llm/test", json={"provider": "anthropic"}, headers=SECRETS).json()
    assert result["ok"] and result["catalog_missing"] == ["claude-haiku-4-5-20251001", "claude-opus-5-5"]


def test_connection_test_invalid_key(client, fake_api):
    client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    fake_api.add("GET", "/v1/models", status=401, body={"error": {"message": "invalid x-api-key"}})
    result = client.post("/api/llm/test", json={"provider": "anthropic"}, headers=SECRETS).json()
    assert (result["ok"], result["error_type"]) == (False, "InvalidKey")


def test_exposed_bind_refuses_key_writes(tmp_path, isolated_env, fake_keyring, fake_api):
    exposed = Session(
        project_dir=tmp_path,
        environ=isolated_env,
        overrides={"app.host": "0.0.0.0"},
        keyring=fake_keyring,
        llm_transport=fake_api.transport(),
    )
    client = TestClient(build_app(exposed))
    r = client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    assert r.status_code == 403 and "netstead llm set-key" in r.text and fake_keyring.store == {}
    assert client.get("/api/llm/providers").json()["key_writes"] is False


def test_models_route(client, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "qwen3:8b"}]})
    assert [m["id"] for m in client.get("/api/llm/models", params={"provider": "anthropic"}).json()["models"]] == [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
        "claude-opus-5-5",
    ]
    assert client.get("/api/llm/models", params={"provider": "ollama"}).json()["models"][0]["label"] == "Qwen 3 8B"
    assert client.get("/api/llm/models", params={"provider": "mistral"}).status_code == 404


def test_key_change_publishes_a_status_only_event_and_resets_the_parser(session, client, monkeypatch):
    published = []
    monkeypatch.setattr(session.events, "publish", published.append)
    session._parser = object()
    client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    (event,) = [e for e in published if e["type"] == "llm"]
    assert "ROUTEKEY" not in json.dumps(event) and session._parser is None


def test_canary_key_never_leaves_the_secret_store(
    session, client, fake_api, fake_keyring, rdu_source, tmp_path, caplog, monkeypatch
):
    """Set, use, test, replace and remove a key; it must appear nowhere but the provider's request headers.

    Searched: every route response, every published (SSE) event and the live SSE snapshot, every log
    record, every history entry (with its ``to_python`` snippet), and every file under ``tmp_path``
    (the project dir and the user config dir).
    """
    canary = "sk-ant-api03-CANARYabcdefghijklmnopqrst"
    replacement = "sk-ant-api03-CANARYsecondkeyabcdefghij"
    fake_api.add("POST", "/v1/messages", body=TOOL_REPLY)
    fake_api.add("GET", "/v1/models", body={"data": [{"id": "claude-sonnet-5"}]})
    published = []
    real_publish = session.events.publish
    monkeypatch.setattr(
        session.events, "publish", lambda e: (published.append(json.dumps(e, default=str)), real_publish(e))
    )
    caplog.set_level(logging.DEBUG)
    seen = []

    def call(method, url, **kw):
        response = client.request(method, url, **kw)
        seen.append(response.text)
        return response

    session.dispatch(OpenNetwork(source=rdu_source))  # waits for the open job (POST /api/actions answers 202)
    history_before = len(session.history)
    assert call("PUT", "/api/llm/keys/anthropic", json={"key": canary}, headers=SECRETS).status_code == 200
    assert len(session.history) == history_before  # a key write is not an Action: nothing recorded
    call(
        "POST",
        "/api/actions",
        json={"type": "set_setting", "key": "select.provider", "value": "anthropic", "scope": "user"},
    )
    assert call("POST", "/api/actions", json={"type": "select", "utterance": UTTERANCE}).json()["ok"]
    call("POST", "/api/llm/test", json={"provider": "anthropic"}, headers=SECRETS)
    assert call("PUT", "/api/llm/keys/anthropic", json={"key": replacement}, headers=SECRETS).status_code == 200
    assert call("POST", "/api/actions", json={"type": "select", "utterance": UTTERANCE}).json()["ok"]
    for url in (
        "/api/llm/providers",
        "/api/settings",
        "/api/state",
        "/api/history",
        "/api/llm/models?provider=anthropic",
    ):
        call("GET", url)
    with client.stream("GET", "/api/events", params={"max_events": 1}) as r:
        seen.append(r.read().decode())
    # Bodies FastAPI itself rejects (not an object; not JSON) get the app-wide 422, which never echoes input.
    for kw in ({"json": [canary]}, {"content": canary, "headers": {"content-type": "text/plain"}}):
        assert call("POST", "/api/actions", **kw).status_code == 422
    # A server error mid-write (after the key is stored) must not surface the key in the 500 or the logs.
    unsafe = TestClient(build_app(session), raise_server_exceptions=False)
    with monkeypatch.context() as m:
        m.setattr(session, "reset_llm", lambda: (_ for _ in ()).throw(RuntimeError("forced failure")))
        failed = unsafe.put("/api/llm/keys/anthropic", json={"key": replacement}, headers=SECRETS)
    seen.append(failed.text)
    assert failed.status_code == 500
    assert fake_keyring.store == {(KEYRING_SERVICE, "anthropic"): replacement}  # the official-endpoint slot
    assert call("DELETE", "/api/llm/keys/anthropic", headers=SECRETS).status_code == 200
    history = [json.dumps(e.to_dict(), default=str) for e in session.history]
    files = [p.read_bytes().decode(errors="replace") for p in tmp_path.rglob("*") if p.is_file()]
    assert any(Path(p).name == "config.toml" for p in tmp_path.rglob("*")), "the user scope write is searched"
    haystack = "\n".join([*seen, *published, caplog.text, *history, *files])
    assert "CANARY" not in haystack
    sent = [r.headers.get("x-api-key") for r in fake_api.requests if r.headers.get("x-api-key")]
    assert {canary, replacement} <= set(sent)  # both keys reached the provider ...
    assert all(r.url.host == "api.anthropic.com" for r in fake_api.requests if r.headers.get("x-api-key"))  # only


def test_non_object_key_body_is_422_without_echo(client):
    r = client.put("/api/llm/keys/anthropic", json=KEY, headers=SECRETS)
    assert r.status_code == 422 and "ROUTEKEY" not in r.text


def test_bodies_fastapi_rejects_get_the_app_wide_422_without_echo(client):
    for kw in ({"json": [KEY]}, {"content": KEY, "headers": {"content-type": "text/plain"}}):
        r = client.post("/api/actions", **kw)
        assert r.status_code == 422 and r.json()["error"] == "invalid request" and "ROUTEKEY" not in r.text
        assert all(set(err) <= {"type", "loc", "msg"} for err in r.json()["detail"])


def test_key_write_resets_the_registry(session, client):
    before = session.llm
    client.put("/api/llm/keys/anthropic", json={"key": KEY}, headers=SECRETS)
    assert session.llm is not before


def test_without_a_keyring_the_ui_is_told_which_env_var_to_set(tmp_path, isolated_env, fake_api):
    session = Session(project_dir=tmp_path, environ=isolated_env, keyring=None, llm_transport=fake_api.transport())
    client = TestClient(build_app(session))
    data = client.get("/api/llm/providers").json()
    assert data["keyring"] is False and data["providers"][1]["key_env"] == ["NETSTEAD_OPENAI_API_KEY", "OPENAI_API_KEY"]
    r = client.put("/api/llm/keys/openai", json={"key": KEY}, headers=SECRETS)
    assert r.status_code == 400 and "set NETSTEAD_OPENAI_API_KEY or OPENAI_API_KEY" in r.json()["detail"]


def test_status_rows_carry_the_privacy_note(client, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    rows = {r["provider"]: r for r in client.get("/api/llm/providers").json()["providers"]}
    assert not any("street names" in item for item in rows["anthropic"]["sends"])
    assert any("street names" in item for item in rows["ollama"]["sends"])
