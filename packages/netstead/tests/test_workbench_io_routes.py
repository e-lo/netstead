"""Tests for the wizard's read-only routes, the jobs routes, and job actions over HTTP (no network)."""

import os
import time
from pathlib import Path

import pytest
from corral.io import credentials as creds_mod
from fastapi.testclient import TestClient
from netstead import extras_probe
from netstead.select.parse import StubParser
from netstead.workbench import Session, build, build_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OSM_FILE = str(FIXTURES / "osm" / "tiny.osm")
WAIT = 10.0


class FakeNominatim:
    def __init__(self, hits=None, error=None):
        self.hits, self.error = hits or [], error
        self.timeouts: list[float | None] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        hits = self.hits

        class R:
            status_code = 200

            def json(self):
                return hits

            def raise_for_status(self):
                pass

        return R()


@pytest.fixture
def make_client(tmp_path, isolated_env):
    def make(http=None, **overrides):
        session = Session(
            project_dir=tmp_path, environ=isolated_env, parser=StubParser(), http=http, overrides=overrides
        )
        return session, TestClient(build_app(session))

    return make


@pytest.fixture
def out_dir(tmp_path):
    (tmp_path / "out").mkdir()
    return str(tmp_path / "out")


def _build_body(out_dir, **kw):
    return {
        "type": "build_network",
        "source": "osm",
        "input_file": OSM_FILE,
        "output_dir": out_dir,
        "output_format": "parquet",
        "name": "tiny",
        **kw,
    }


def test_fs_list_tags_entries(make_client):
    _, client = make_client()
    listing = client.get("/api/fs/list", params={"path": str(FIXTURES)}).json()
    kinds = {e["name"]: e["kind"] for e in listing["entries"]}
    assert kinds["overture"] == "overture" and kinds["osm"] is None


def test_fs_list_without_path_lists_roots(make_client, tmp_path):
    _, client = make_client()
    paths = [e["path"] for e in client.get("/api/fs/list").json()["entries"]]
    assert str(tmp_path.resolve()) in paths


def test_fs_list_outside_roots_is_403(make_client, tmp_path_factory):
    _, client = make_client()
    r = client.get("/api/fs/list", params={"path": str(tmp_path_factory.mktemp("other"))})
    assert r.status_code == 403 and "outside the allowed folders" in r.json()["detail"]


def test_fs_list_missing_is_404(make_client, tmp_path):
    _, client = make_client()
    assert client.get("/api/fs/list", params={"path": str(tmp_path / "nope")}).status_code == 404


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_fs_list_unreadable_folder_is_403(make_client, tmp_path):
    _, client = make_client()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        r = client.get("/api/fs/list", params={"path": str(locked)})
    finally:
        locked.chmod(0o755)
    assert r.status_code == 403 and r.json()["detail"] == f"permission denied: {locked}"


def test_check_url_reports_without_network(make_client, monkeypatch):
    monkeypatch.setattr(creds_mod, "_lookup_env", lambda host: {})
    monkeypatch.setattr(creds_mod, "_lookup_keyring", lambda host: {})
    monkeypatch.setattr(creds_mod, "_lookup_netrc", lambda host: {})
    _, client = make_client()
    r = client.post("/api/check-url", json={"url": "ftp://example.org/x"})
    assert r.status_code == 200 and r.json()["reachable"] is False and r.json()["credential_source"] == "none"
    assert client.post("/api/check-url", json={}).status_code == 422


def test_check_url_respects_the_origin_guard(make_client):
    _, client = make_client()
    r = client.post("/api/check-url", json={"url": "s3://b/k"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_reads_respect_the_host_guard(make_client):
    _, client = make_client()
    assert client.get("/api/fs/list", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/jobs", headers={"Host": "evil.example"}).status_code == 400


def test_geocode_returns_candidates(make_client):
    hit = {"display_name": "Durham", "type": "city", "boundingbox": ["35.8", "36.1", "-79.0", "-78.7"]}
    _, client = make_client(http=FakeNominatim([hit]))
    (cand,) = client.get("/api/geocode", params={"q": "Durham"}).json()["candidates"]
    assert cand["bbox"] == [-79.0, 35.8, -78.7, 36.1] and cand["polygon"] is None


def test_geocode_failure_is_502(make_client):
    _, client = make_client(http=FakeNominatim(error=OSError("nominatim down")))
    r = client.get("/api/geocode", params={"q": "Durham"})
    assert r.status_code == 502 and "nominatim down" in r.json()["detail"]


def test_geocode_uses_short_timeout_and_no_retries(make_client):
    # The route is an interactive wizard step, so it fails fast rather than using
    # geocode_candidates' library defaults (timeout=30, retries=3).
    fake = FakeNominatim(
        [{"display_name": "Durham", "type": "city", "boundingbox": ["35.8", "36.1", "-79.0", "-78.7"]}]
    )
    _, client = make_client(http=fake)
    assert client.get("/api/geocode", params={"q": "Durham"}).status_code == 200
    assert fake.timeouts == [10]


def test_extras_reports_what_imports_and_the_install_commands(make_client):
    _, client = make_client()
    info = client.get("/api/extras").json()
    assert info["installed"] == {"osm": True, "overture": True}  # the dev environment has every extra
    uv = info["install"]["osm"]["uv"]
    assert uv.startswith("uv add ") and "dbcorral @ " in uv and "netstead[osm] @ " in uv
    assert info["install"]["osm"]["pip"].startswith("pip install ")
    assert "netstead[osm,overture] @ " in info["install"]["osm,overture"]["uv"]
    assert info["docs_url"] == "https://e-lo.github.io/netstead/netstead/install/"


def test_extras_flags_a_missing_extra(make_client, monkeypatch):
    real = extras_probe.probe_extra

    def probe(module):
        return (False, "ModuleNotFoundError") if module == "yaml" else real(module)

    monkeypatch.setattr(extras_probe, "probe_extra", probe)
    _, client = make_client()
    assert client.get("/api/extras").json()["installed"] == {"osm": False, "overture": False}


def test_estimate_local_osm(make_client, out_dir):
    _, client = make_client()
    j = client.post("/api/estimate", json=_build_body(out_dir)).json()
    assert j["estimate"]["basis"].startswith("osm_file:") and j["needs_approval"] is False and j["threshold_s"] == 90.0


def test_estimate_over_threshold_flags_approval(make_client, out_dir):
    _, client = make_client(**{"app.approve_above_s": 0})
    assert client.post("/api/estimate", json=_build_body(out_dir)).json()["needs_approval"] is True


def test_estimate_invalid_and_unplannable(make_client, out_dir):
    _, client = make_client()
    assert client.post("/api/estimate", json={"source": "osm"}).status_code == 422
    # out_dir is a folder, not a .osm/.json file: plan_build rejects it as the wrong kind of input.
    r = client.post("/api/estimate", json=_build_body(out_dir, input_file=out_dir))
    assert r.status_code == 400 and r.json()["error_type"] == "ActionError"


def test_estimate_is_not_recorded(make_client, out_dir):
    session, client = make_client()
    client.post("/api/estimate", json=_build_body(out_dir))
    assert session.history == []


def test_estimate_does_not_delete_stale_partials(make_client, out_dir):
    _, client = make_client()
    stale = Path(out_dir) / ".partial-0123abcd-old"
    stale.mkdir()
    old = time.time() - build.STALE_PARTIAL_S - 60
    os.utime(stale, (old, old))
    assert client.post("/api/estimate", json=_build_body(out_dir)).status_code == 200
    assert stale.exists()  # only a build job cleans up


def test_job_action_answers_202_then_finishes(make_client, out_dir):
    session, client = make_client()
    r = client.post("/api/actions", json=_build_body(out_dir))
    assert r.status_code == 202
    job_id = r.json()["result"]["job_id"]
    assert r.json()["job"]["id"] == job_id and r.json()["job"]["kind"] == "build_network"
    assert session.jobs.get(job_id).wait(WAIT)
    (listed,) = client.get("/api/jobs").json()["jobs"]
    assert listed["status"] == "done" and listed["history_seq"] == 1
    assert session.history[0].ok and len(session.registry) == 1


def test_cancel_route(make_client, out_dir):
    session, client = make_client()
    job_id = client.post("/api/actions", json=_build_body(out_dir)).json()["result"]["job_id"]
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["id"] == job_id
    assert session.jobs.get(job_id).wait(WAIT)
    assert client.post("/api/jobs/job-404/cancel").status_code == 404
