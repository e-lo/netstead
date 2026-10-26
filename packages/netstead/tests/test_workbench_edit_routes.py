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
    assert (j["total"], len(j["issues"]), j["unlocated"], j["stale"]) == (287, 10, 16, False)
    assert j["facets"]["code"]["quality.high_speed_residential"] == 271
    assert j["facets"]["severity"] == {"error": 0, "warning": 271, "info": 16}


def test_filters(client):
    assert client.get(f"{BASE}/issues?severity=info").json()["total"] == 16
    assert client.get(f"{BASE}/issues?severity=error,info").json()["total"] == 16
    assert client.get(f"{BASE}/issues?code=fk.unverifiable").json()["total"] == 0
    assert client.get(f"{BASE}/issues?located=no").json()["total"] == 16
    first = client.get(f"{BASE}/issues?located=yes&limit=1").json()["issues"][0]
    rec = client.get(f"{BASE}/issues?record=link:{first['key']}").json()
    assert rec["total"] >= 1 and all(i["key"] == first["key"] for i in rec["issues"])


def test_the_summary_folds_the_whole_set_by_code_and_severity(client):
    j = client.get(f"{BASE}/issues/summary").json()
    assert (j["total"], j["stale"]) == (287, False)
    assert j["facets"]["severity"] == {"error": 0, "warning": 271, "info": 16}
    first = j["groups"][0]
    assert (first["code"], first["severity"], first["count"]) == ("quality.high_speed_residential", "warning", 271)
    assert first["fix_hint"] and first["tables"] == ["link"]
    assert sum(g["count"] for g in j["groups"]) == 287
    ranks = [{"error": 0, "warning": 1, "info": 2}[g["severity"]] for g in j["groups"]]
    assert ranks == sorted(ranks)  # error, then warning, then info
    info = client.get(f"{BASE}/issues/summary?severity=info").json()
    assert {g["severity"] for g in info["groups"]} == {"info"} and info["total"] == 16
    # a group's members are paged through /issues with the group's own code and severity
    page = client.get(f"{BASE}/issues?code={first['code']}&severity={first['severity']}&limit=5&offset=5").json()
    assert page["total"] == 271 and len(page["issues"]) == 5


def test_the_summary_before_any_validation(fresh):
    j = fresh.get(f"{BASE}/issues/summary").json()
    assert j["version"] is None and j["groups"] == [] and j["total"] == 0


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


def test_the_report_without_the_reports_extra(validated, monkeypatch):
    import importlib

    real = importlib.import_module

    def missing(name, *args):
        if name == "netstead.map":
            raise ImportError("No module named 'jinja2'")
        return real(name, *args)

    monkeypatch.setattr(importlib, "import_module", missing)
    r = TestClient(build_app(validated)).get(f"{BASE}/report.html")
    assert r.status_code == 501 and "[reports]" in r.json()["detail"]


def test_the_report_needs_a_current_validation(fresh):
    assert fresh.get(f"{BASE}/report.html").status_code == 409


def test_the_report_draws_the_network_the_run_read(tmp_path, monkeypatch):
    from netstead import map as netstead_map

    s = _session(tmp_path)
    s.dispatch({"type": "run_validation"})
    drawn = []
    monkeypatch.setattr(netstead_map, "render_validation_html", lambda net, report, title: drawn.append(net) or "")
    c = TestClient(build_app(s))
    assert c.get(f"{BASE}/report.html").status_code == 200
    snapshot = s.issue_set("leavenworth").network
    assert len(drawn) == 1 and drawn[0] is snapshot and snapshot is not s.network().roadway
    s.dispatch({"type": "edit_cells", "ids": [1], "values": {"free_speed": 30}})
    assert c.get(f"{BASE}/report.html").status_code == 409  # stale: refused, not drawn on the edited tables


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


@pytest.fixture
def edited(tmp_path):
    s = _session(tmp_path)
    s.dispatch({"type": "edit_cells", "table": "node", "ids": [1], "values": {"ctrl_type": "bogus"}, "note": "test"})
    return s, TestClient(build_app(s))


def test_the_edits_view(edited):
    _, c = edited
    j = c.get(f"{BASE}/edits").json()
    (entry,) = j["entries"]
    assert entry["source"] == "workbench" and entry["rows"] == {"node": 1} and entry["note"].endswith("(test)")
    assert (j["version"], j["pending"], j["unsaved"], j["dirty"], j["can_undo"]) == (1, 1, 1, True, True)
    found = [(w["code"], w["table"], w["key"], w["column"]) for w in j["warnings"]]
    assert found == [("edit.enum", "node", 1, "ctrl_type")]


def test_the_edits_view_before_any_edit(fresh):
    j = fresh.get(f"{BASE}/edits").json()
    assert j["entries"] == [] and j["warnings"] == [] and j["dirty"] is False and j["can_undo"] is False


def test_the_delete_plan_is_a_dry_run(edited):
    s, c = edited
    before = len(s.history)
    j = c.post(f"{BASE}/table/link/delete-plan", json={"ids": [1]}).json()
    assert j == {
        "table": "link",
        "version": s.network("leavenworth").version,
        "refused": None,
        "rows": {"lane": 1, "link": 1},
        "summary": "delete link 1 (and 1 lane rows that depend on it)",
    }
    refused = c.post(f"{BASE}/table/node/delete-plan", json={"ids": [1]}).json()["refused"]
    assert refused.startswith("node 1 is still used")
    assert len(s.history) == before and len(s.registry.get("leavenworth").links_df()) == 339


def test_the_delete_plan_reads_a_snapshot_without_the_lock(tmp_path, monkeypatch):
    from netstead.workbench import session as session_module

    s = _session(tmp_path)
    real, held = session_module.plan_delete, []

    def spy(net, *args, **kwargs):
        held.append((s._lock._is_owned(), net is s.network().roadway))
        return real(net, *args, **kwargs)

    monkeypatch.setattr(session_module, "plan_delete", spy)
    assert s.delete_preview(s.network(), "link", [1])["rows"] == {"lane": 1, "link": 1}
    assert held == [(False, False)]  # not under the lock, and not on the live tables


def test_bad_delete_plans(edited):
    _, c = edited
    assert c.post(f"{BASE}/table/link/delete-plan", json={"ids": []}).status_code == 422
    assert c.post(f"{BASE}/table/link/delete-plan", json={"ids": [1], "x": 1}).status_code == 422
    assert c.post("/api/n/nope/roadway/table/link/delete-plan", json={"ids": [1]}).status_code == 404
    assert c.post("/api/n/leavenworth/transit/table/link/delete-plan", json={"ids": [1]}).status_code == 501


def test_the_delete_plan_sits_behind_the_origin_guard(edited):
    _, c = edited
    cross = c.post(f"{BASE}/table/link/delete-plan", json={"ids": [1]}, headers={"origin": "https://evil.example"})
    assert cross.status_code == 403


def test_edit_reads_are_not_recorded(edited):
    s, c = edited
    before = len(s.history)
    c.get(f"{BASE}/edits")
    c.post(f"{BASE}/table/link/delete-plan", json={"ids": [1]})
    assert len(s.history) == before


def test_each_summary_group_counts_its_findings_on_the_map(client):
    groups = {g["code"]: g for g in client.get(f"{BASE}/issues/summary").json()["groups"]}
    assert groups["structural.missing_optional_resource"]["located"] == 0  # table-level: no map anchor
    speed = groups["quality.high_speed_residential"]
    assert speed["located"] == speed["count"]  # every finding sits on a link


def test_anchors_lists_every_link_of_one_finding_type(client):
    a = client.get(f"{BASE}/issues/anchors", params={"code": "quality.high_speed_residential", "severity": "warning"})
    assert a.status_code == 200
    body = a.json()
    assert len(body["links"]) == len(set(body["links"])) > 200 and body["nodes"] == [] and body["points"] == []
    none = client.get(f"{BASE}/issues/anchors", params={"code": "structural.missing_optional_resource"}).json()
    assert none == {"links": [], "nodes": [], "points": []}  # table-level findings have no map anchor
