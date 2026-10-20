"""Validation in the workbench: RunValidation as a recorded job; issues tied to record keys and map anchors."""

import pytest
from corral.reports import Category, Issue, Severity, ValidationReport
from netstead import Network
from netstead.config import ValidationSettings
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import EditCells, RunValidation, SetSetting
from netstead.workbench.errors import ActionError
from netstead.workbench.issues import locate_issues, rule_configs
from pydantic import ValidationError

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch({"type": "open_network", "source": SRC})
    return s


def test_run_validation_is_a_recorded_job_with_counts(session):
    result = session.dispatch(RunValidation())
    # Leavenworth: 16 structural infos + 1 fk.unverifiable warning, and 271 high_speed_residential warnings.
    assert result["counts"] == {"error": 0, "warning": 272, "info": 16}
    assert result["version"] == 0 and result["stale"] is False and result["unknown_rules"] == []
    entry = session.history[-1]
    assert entry.ok and entry.python == "app.do(RunValidation())"
    assert entry.imports == "from netstead.workbench import RunValidation"
    assert session.state()["issues"]["leavenworth"] == {"version": 0, "stale": False, "counts": result["counts"]}


def test_quality_issues_carry_their_link_key_and_anchor(session):
    session.dispatch(RunValidation())
    issues = session.issues["leavenworth"].issues
    links = session.registry.get("leavenworth").links_df()
    speed = [i for i in issues if i["code"] == "quality.high_speed_residential"]
    first = speed[0]
    assert first["key"] == int(links.link_id.iloc[first["row"]])
    assert first["anchor"] == {"link": first["key"]} and first["fixable"] is True
    structural = [i for i in issues if i["code"].startswith("structural.")]
    assert structural and all(i["anchor"] is None and i["key"] is None for i in structural)


def test_rule_settings_reach_the_rules(session):
    session.dispatch(SetSetting(key="validation.rules", value={"quality.high_speed_residential": {"enabled": False}}))
    assert session.dispatch(RunValidation())["counts"]["warning"] == 1


def test_unknown_rule_codes_are_reported(session):
    session.dispatch(SetSetting(key="validation.rules", value={"quality.no_such_rule": {"enabled": False}}))
    assert session.dispatch(RunValidation())["unknown_rules"] == ["quality.no_such_rule"]


def test_new_quality_rules_are_known_to_validation_settings(session):
    # Settings key rules by code: a code with no installed rule comes back in unknown_rules.
    rules = {"quality.non_unique_combination": {"enabled": False}, "quality.overlapping_links": {"enabled": False}}
    session.dispatch(SetSetting(key="validation.rules", value=rules))
    assert session.dispatch(RunValidation())["unknown_rules"] == []


@pytest.mark.parametrize(
    "rules",
    [
        {"quality.duplicate_near_nodes": {"thresholds": {"epsilon_units": -1}}},
        {"quality.duplicate_near_nodes": {"thresholds": {"epsilon_units": "close"}}},
        {"quality.non_unique_combination": {"thresholds": {"combinations": [["link_id", "time_day"]]}}},
        {"quality.non_unique_combination": {"thresholds": {"combinations": {"link_tod": "link_id"}}}},
    ],
)
def test_bad_quality_thresholds_are_rejected_when_settings_load(rules):
    with pytest.raises(ValidationError):
        ValidationSettings(rules=rules)


def test_set_setting_rejects_a_bad_threshold_up_front(session):
    bad = {"quality.duplicate_near_nodes": {"thresholds": {"epsilon_units": -1}}}
    with pytest.raises(ActionError, match="epsilon_units"):
        session.dispatch(SetSetting(key="validation.rules", value=bad))
    good = {
        "quality.duplicate_near_nodes": {"thresholds": {"epsilon_units": 0.5}},
        "quality.non_unique_combination": {"thresholds": {"combinations": {"link_tod": [["link_id", "time_day"]]}}},
    }
    session.dispatch(SetSetting(key="validation.rules", value=good))
    assert session.dispatch(RunValidation())["unknown_rules"] == []


def test_severity_overrides_are_checked_when_settings_load():
    cfg = rule_configs(ValidationSettings(rules={"quality.high_speed_residential": {"severity_override": "info"}}))
    assert cfg["quality.high_speed_residential"].severity_override == Severity.INFO
    with pytest.raises(ValidationError):
        ValidationSettings(rules={"x": {"severity_override": "fatal"}})


def test_a_column_read_alone_keeps_the_order_rules_read():
    # locate_issues rests on this: rule row positions come from Table.to_pandas(); keys from select(pk).
    net = Network.from_source(leavenworth.parquet_dir())
    for name, pk in (("link", "link_id"), ("node", "node_id"), ("lane", "lane_id")):
        table = net.tables[name]
        assert table.select(pk).to_pandas()[pk].tolist() == table.to_pandas()[pk].tolist()


def test_a_lane_issue_anchors_on_its_link_and_coordinates_anchor_themselves():
    net = Network.from_source(leavenworth.parquet_dir())
    lanes = net.tables["lane"].to_pandas()
    report = ValidationReport(
        issues=[
            Issue(Severity.WARNING, Category.DATA_QUALITY, "t.lane", "m", table="lane", column="width", row=0),
            Issue(Severity.INFO, Category.DATA_QUALITY, "t.xy", "m", extra={"lon": -120.6, "lat": 47.6}),
        ]
    )
    lane, xy = locate_issues(report, net)
    assert lane["key"] == int(lanes.lane_id.iloc[0]) and lane["anchor"] == {"link": int(lanes.link_id.iloc[0])}
    assert lane["fixable"] is True  # lane has a key, and width is a number the editor can set
    assert xy["anchor"] == {"lonlat": [-120.6, 47.6]} and xy["key"] is None


def test_an_edit_makes_the_issue_set_stale_and_a_rerun_sees_it(session):
    session.dispatch(RunValidation())
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    assert session.state()["issues"]["leavenworth"]["stale"] is True
    assert session.dispatch(RunValidation())["counts"]["warning"] == 271  # link 1 is no longer flagged


def test_a_validation_that_finishes_after_a_newer_one_is_superseded(session, monkeypatch):
    import threading

    from netstead.workbench import session as session_module

    real, started, release = session_module.run_validation, threading.Event(), threading.Event()
    calls = []

    def first_waits(net, rules, *, quality=True):
        calls.append(net)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=10)
        return real(net, rules, quality=quality)

    monkeypatch.setattr(session_module, "run_validation", first_waits)
    old = session.submit(RunValidation())  # reads version 0, then waits
    assert started.wait(timeout=10)
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    current = session.dispatch(RunValidation())  # version 1, stored
    release.set()
    old.wait()
    assert current["superseded"] is False and current["stale"] is False
    assert old.result["superseded"] is True and old.result["stale"] is True and old.result["version"] == 0
    assert session.issue_set("leavenworth").version == 1
    assert session.state()["issues"]["leavenworth"]["stale"] is False


def test_issues_located_after_an_undo_point_at_the_restored_rows(session):
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    session.dispatch({"type": "undo_edit"})
    result = session.dispatch(RunValidation())
    assert result["stale"] is False and result["counts"]["warning"] == 272  # link 1 is flagged again
    issues = session.issue_set("leavenworth")
    hit = issues.query(code="quality.high_speed_residential", record=("link", "1"))
    assert [(i["key"], i["anchor"]) for i in hit] == [(1, {"link": 1})]


def test_closing_the_network_drops_its_issues(session):
    session.dispatch(RunValidation())
    session.dispatch({"type": "close_network", "net_id": "leavenworth"})
    assert session.issues == {} and "leavenworth" not in session.state()["issues"]
