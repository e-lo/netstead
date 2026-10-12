"""Validation and edit routes: read-only views of what actions produced (never recorded)."""

import json

import pytest
from corral.reports import Category, Issue, Severity, ValidationReport
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.issues import IssueSet, locate_issues

BASE = "/api/n/leavenworth/roadway"
SRC = str(leavenworth.parquet_dir())
EVIL = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"


def _session(tmp):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([SRC, str(tmp)])}
    s = Session(project_dir=tmp, environ=env, parser=StubParser(), plugins=[])
    s.dispatch({"type": "open_network", "source": SRC})
    return s


@pytest.fixture(scope="module")
def validated(tmp_path_factory):
    s = _session(tmp_path_factory.mktemp("wb"))
    s.dispatch({"type": "run_validation"})
    return s


@pytest.fixture(scope="module")
def client(validated):
    return TestClient(build_app(validated))


@pytest.fixture
def fresh(tmp_path):
    return TestClient(build_app(_session(tmp_path)))


def test_issues_before_any_validation(fresh):
    j = fresh.get(f"{BASE}/issues").json()
    assert j["version"] is None and j["issues"] == [] and j["total"] == 0
    assert fresh.get(f"{BASE}/issues/markers").json()["markers"] == []


def test_a_page_with_facets(client):
    j = client.get(f"{BASE}/issues?limit=10").json()
    assert (j["total"], len(j["issues"]), j["unlocated"], j["stale"]) == (288, 10, 17, False)
    assert j["facets"]["code"]["quality.high_speed_residential"] == 271
    assert j["facets"]["severity"] == {"error": 0, "warning": 272, "info": 16}


def test_filters(client):
    assert client.get(f"{BASE}/issues?severity=info").json()["total"] == 16
    assert client.get(f"{BASE}/issues?severity=error,info").json()["total"] == 16
    assert client.get(f"{BASE}/issues?code=fk.unverifiable").json()["total"] == 1
    assert client.get(f"{BASE}/issues?located=no").json()["total"] == 17
    first = client.get(f"{BASE}/issues?located=yes&limit=1").json()["issues"][0]
    rec = client.get(f"{BASE}/issues?record=link:{first['key']}").json()
    assert rec["total"] >= 1 and all(i["key"] == first["key"] for i in rec["issues"])


def test_markers_and_the_record_index(client):
    j = client.get(f"{BASE}/issues/markers").json()
    assert len(j["markers"]) == 271 and j["truncated"] is False
    assert set(j["markers"][0]) == {"i", "severity", "anchor", "table", "key"}
    assert sum(len(v) for v in j["records"]["link"].values()) == 271


def test_bad_queries(client):
    assert client.get(f"{BASE}/issues?limit=501").status_code == 422
    assert client.get(f"{BASE}/issues?record=link").status_code == 422
    assert client.get("/api/n/leavenworth/transit/issues").status_code == 501
    assert client.get("/api/n/nope/roadway/issues").status_code == 404


def test_the_report_downloads(client):
    r = client.get(f"{BASE}/report.html")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "<html" in r.text.lower() and "high_speed_residential" in r.text


def test_the_report_needs_a_current_validation(fresh):
    assert fresh.get(f"{BASE}/report.html").status_code == 409


def test_the_report_and_issues_escape_data_values(tmp_path):
    # A validation message can quote a data value (a street name): the report must never let it out as markup.
    s = _session(tmp_path)
    h = s.network()
    report = ValidationReport(
        issues=[
            Issue(Severity.WARNING, Category.DATA_QUALITY, "t.evil", EVIL, table="link", column="name", row=0),
            Issue(Severity.INFO, Category.DATA_QUALITY, "t.evil2", EVIL, fix_hint=EVIL),
        ]
    )
    s.issues[h.id] = IssueSet(h.id, h.version, locate_issues(report, h.roadway), report)
    c = TestClient(build_app(s))
    page = c.get(f"{BASE}/report.html").text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert c.get(f"{BASE}/issues").json()["issues"][0]["message"] == EVIL  # JSON carries it as data
    assert c.get(f"{BASE}/issues").headers["content-type"].startswith("application/json")


def test_reads_are_not_recorded(validated, client):
    before = len(validated.history)
    client.get(f"{BASE}/issues")
    client.get(f"{BASE}/issues/markers")
    client.get(f"{BASE}/report.html")
    assert len(validated.history) == before


def test_routes_sit_behind_the_host_guard(client):
    assert client.get(f"{BASE}/issues", headers={"host": "evil.example"}).status_code == 400
