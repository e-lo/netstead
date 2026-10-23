"""POST /api/llm/ollama/pull: guards, model-name validation, the background job, cancel, and status refresh."""

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from netstead.workbench import Session, build_app

pytestmark = pytest.mark.usefixtures("no_network")

SECRETS = {"X-Netstead-Secrets": "1"}
PULL = "/api/llm/ollama/pull"


def ndjson(*lines):
    return b"".join(json.dumps(line).encode() + b"\n" for line in lines)


PULL_OK = ndjson(
    {"status": "pulling manifest"},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 0},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 500},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 1000},
    {"status": "verifying sha256 digest"},
    {"status": "success"},
)


def _session(tmp_path, isolated_env, fake_keyring, transport, **overrides):
    return Session(
        project_dir=tmp_path,
        environ=isolated_env,
        overrides=overrides,
        keyring=fake_keyring,
        llm_transport=transport,
    )


@pytest.fixture
def session(tmp_path, isolated_env, fake_keyring, fake_api):
    return _session(tmp_path, isolated_env, fake_keyring, fake_api.transport())


@pytest.fixture
def client(session):
    return TestClient(build_app(session))


@pytest.fixture
def published(session, monkeypatch):
    events = []
    real = session.events.publish
    monkeypatch.setattr(session.events, "publish", lambda e: (events.append(e), real(e)))
    return events


def _pulls(fake_api):
    return [r for r in fake_api.requests if r.url.path == "/api/pull"]


def _wait(session, job_id):
    job = session.jobs.get(job_id)
    assert job.wait(5)
    return session.jobs.snapshot(job)


def test_pull_needs_the_secrets_header(client, fake_api):
    r = client.post(PULL, json={"model": "qwen3:4b"})
    assert r.status_code == 403 and "X-Netstead-Secrets" in r.text and _pulls(fake_api) == []


def test_pull_is_refused_on_an_exposed_bind(tmp_path, isolated_env, fake_keyring, fake_api):
    exposed = _session(tmp_path, isolated_env, fake_keyring, fake_api.transport(), **{"app.host": "0.0.0.0"})
    client = TestClient(build_app(exposed))
    r = client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS)
    assert r.status_code == 403 and "netstead llm pull" in r.text and _pulls(fake_api) == []
    fake_api.add("GET", "/api/tags", body={"models": []})
    assert client.get("/api/llm/providers").json()["ollama_pull"]["allowed"] is False


def test_pull_is_refused_for_a_remote_ollama(tmp_path, isolated_env, fake_keyring, fake_api):
    remote = _session(
        tmp_path,
        isolated_env,
        fake_keyring,
        fake_api.transport(),
        **{"llm.ollama.base_url": "http://gpu.example:11434"},
    )
    client = TestClient(build_app(remote))
    r = client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS)
    assert r.status_code == 400 and "not this machine" in r.text and _pulls(fake_api) == []
    fake_api.add("GET", "/api/tags", body={"models": []})
    assert client.get("/api/llm/providers").json()["ollama_pull"]["allowed"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"model": "evil.example/ns/model:latest"},
        {"model": "http://evil.example/x"},
        {"model": "qwen3 4b"},
        {"model": "x" * 129},
        {"model": ""},
        {"model": 4},
        {"model": "qwen3:4b", "insecure": True},
        ["qwen3:4b"],
        None,
    ],
)
def test_bad_model_names_and_bodies_are_422(client, fake_api, body):
    r = client.post(PULL, json=body, headers=SECRETS)
    assert r.status_code == 422 and _pulls(fake_api) == []
    assert "evil" not in r.text  # never echoed


def test_pull_runs_as_a_job_with_progress_and_refreshes_status(session, client, fake_api, published):
    fake_api.add("GET", "/api/tags", body={"models": []}).add(
        "GET", "/api/tags", body={"models": [{"name": "qwen3:4b"}]}
    )
    assert client.get("/api/llm/providers").json()["providers"][-1]["models"] == 0  # probe now cached
    fake_api.add("POST", "/api/pull", body=PULL_OK)
    r = client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS)
    assert r.status_code == 202
    job = _wait(session, r.json()["job"]["id"])
    assert (job["status"], job["kind"], job["result"]) == ("done", "ollama_pull", {"model": "qwen3:4b", "bytes": 1000})
    assert json.loads(_pulls(fake_api)[0].content) == {"model": "qwen3:4b", "stream": True}
    progress = [e["job"]["progress"] for e in published if e["type"] == "job" and e["job"]["status"] == "running"]
    assert 0.5 in progress and "downloading" in {e["job"]["stage"] for e in published if e["type"] == "job"}
    # The probe cache was dropped and fresh status published, so the picker sees the new model.
    (llm_event,) = [e for e in published if e["type"] == "llm"]
    assert llm_event["providers"][-1]["models"] == 1
    assert client.get("/api/llm/providers").json()["providers"][-1]["models"] == 1
    # Not an Action: nothing in history, so replaying the session never re-downloads.
    assert session.history == []


def test_failed_pull_is_a_failed_job_with_the_reason(session, client, fake_api):
    fake_api.add(
        "POST",
        "/api/pull",
        body=ndjson({"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}),
    )
    r = client.post(PULL, json={"model": "nosuch:1b"}, headers=SECRETS)
    job = _wait(session, r.json()["job"]["id"])
    assert job["status"] == "failed" and "no model called 'nosuch:1b'" in job["error"]


def test_unreachable_ollama_fails_the_job(session, client, fake_api):
    fake_api.add("POST", "/api/pull", raises=httpx.ConnectError("refused"))
    job = _wait(session, client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS).json()["job"]["id"])
    assert job["status"] == "failed" and "could not reach Ollama" in job["error"]


def test_cancel_stops_at_the_next_chunk(tmp_path, isolated_env, fake_keyring):
    first_sent, release, read_on = threading.Event(), threading.Event(), threading.Event()

    def body():
        yield ndjson({"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 10})
        first_sent.set()
        release.wait(5)
        yield ndjson({"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 20})
        read_on.set()  # only reached if the job asked for a line after the cancel checkpoint
        yield ndjson({"status": "success"})

    def handler(request):
        if request.url.path == "/api/pull":
            return httpx.Response(200, content=body())
        return httpx.Response(200, json={"models": []})

    session = _session(tmp_path, isolated_env, fake_keyring, httpx.MockTransport(handler))
    client = TestClient(build_app(session))
    job_id = client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS).json()["job"]["id"]
    assert first_sent.wait(5)
    client.post(f"/api/jobs/{job_id}/cancel", json={})
    release.set()
    job = _wait(session, job_id)
    assert job["status"] == "canceled" and not read_on.is_set()


def test_a_second_pull_of_the_same_model_while_running_is_409(tmp_path, isolated_env, fake_keyring):
    release = threading.Event()

    def body():
        release.wait(5)
        yield ndjson({"status": "success"})

    def handler(request):
        if request.url.path == "/api/pull":
            return httpx.Response(200, content=body())
        return httpx.Response(200, json={"models": []})

    session = _session(tmp_path, isolated_env, fake_keyring, httpx.MockTransport(handler))
    client = TestClient(build_app(session))
    first = client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS).json()["job"]["id"]
    assert client.post(PULL, json={"model": "qwen3:4b"}, headers=SECRETS).status_code == 409
    release.set()
    assert _wait(session, first)["status"] == "done"


def test_status_snapshot_offers_the_catalog_pull_choices(client, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    pull = client.get("/api/llm/providers").json()["ollama_pull"]
    assert pull["allowed"] is True
    choices = [(c["id"], c["size_gb"]) for c in pull["choices"]]
    assert choices == [("qwen3:4b", 2.5), ("qwen3:8b", 5.2), ("qwen2.5:7b", 4.7)]
