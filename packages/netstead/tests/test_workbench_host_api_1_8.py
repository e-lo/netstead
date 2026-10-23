"""Host API 1.8: a plugin's read-only table in the table view (``host.register_table``), served by the table routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.actions import EditCells, OpenNetwork
from netstead.workbench.plugins import HOST_API, Host, WorkbenchPlugin
from netstead.workbench.plugins.spec import api_compatible
from netstead.workbench.registry import NETWORK_TABLE_NAMES, PluginTables

SRC = str(leavenworth.parquet_dir())
NET = "leavenworth"
TABLE = f"/api/n/{NET}/roadway/table/things"

ROWS = [
    {"thing_id": "a.yml", "project": "Bridge", "tags": "bike,safety", "count": 3, "fits": True},
    {"thing_id": "b.yml", "project": "Arterial", "tags": "transit", "count": 1, "fits": False},
    {"thing_id": "c.yml", "project": "Canal", "tags": "bike", "count": 7, "fits": None},
]


class Probe:
    """A plugin that publishes ``things``; ``rows`` is what its rows_fn returns, ``calls`` how often it ran."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, fail_load: bool = False) -> None:
        self.rows = list(ROWS if rows is None else rows)
        self.calls = 0
        self.host: Host | None = None
        self.fail_load = fail_load

    def rows_fn(self) -> list[dict[str, Any]]:
        self.calls += 1
        return self.rows

    def plugin(self) -> WorkbenchPlugin:
        def on_load(host: Host) -> None:
            self.host = host
            host.register_table(
                "things",
                "Things",
                self.rows_fn,
                primary_key="thing_id",
                columns=("thing_id", "project", "tags", "count", "fits"),
            )
            if self.fail_load:
                raise RuntimeError("boom after registering")

        return WorkbenchPlugin(id="probe", name="Probe", version="0", requires_api="1.8", on_load=on_load)


def _session(tmp_path: Path, env: dict[str, str], *plugins: WorkbenchPlugin) -> Session:
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=list(plugins))
    s.dispatch(OpenNetwork(source=SRC))
    return s


@pytest.fixture
def probe() -> Probe:
    return Probe()


@pytest.fixture
def client(tmp_path: Path, isolated_env: dict[str, str], probe: Probe) -> TestClient:
    return TestClient(build_app(_session(tmp_path, isolated_env, probe.plugin())))


def rows(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(f"{TABLE}/rows", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload: dict[str, Any]) -> list[str]:
    key = payload["columns"].index("thing_id")
    return [r[key] for r in payload["rows"]]


def test_this_netstead_provides_host_api_1_8():
    assert HOST_API == "1.8" and api_compatible("1.8") and api_compatible("1.7")


def test_the_table_is_listed_after_the_networks_marked_read_only(client):
    tables = client.get(f"/api/n/{NET}/roadway/tables").json()["tables"]
    assert [t["name"] for t in tables][:2] == ["link", "node"]
    (things,) = [t for t in tables if t["name"] == "things"]
    assert tables[-1] is not None and tables[-1]["name"] == "things"
    assert things == {
        "name": "things",
        "rows": 3,
        "columns": ["thing_id", "project", "tags", "count", "fits"],
        "plugin": "probe",
        "title": "Things",
        "primary_key": "thing_id",
        "read_only": True,
    }


def test_the_schema_names_the_key_and_no_foreign_keys(client):
    schema = client.get(f"{TABLE}/schema").json()
    assert schema["primary_key"] == "thing_id" and schema["foreign_keys"] == [] and schema["read_only"] is True
    kinds = {c["name"]: c["kind"] for c in schema["columns"]}
    assert kinds == {"thing_id": "str", "project": "str", "tags": "str", "count": "num", "fits": "bool"}
    assert schema["rows"] == 3 and schema["plugin"] == "probe" and schema["title"] == "Things"


def test_rows_sort_and_filter_as_a_network_tables_do(client):
    assert ids(rows(client)) == ["a.yml", "b.yml", "c.yml"]
    assert ids(rows(client, sort="project")) == ["b.yml", "a.yml", "c.yml"]
    assert ids(rows(client, sort="count", dir="desc")) == ["c.yml", "a.yml", "b.yml"]
    bike = rows(client, filter=[{"col": "tags", "op": "any_of", "val": ["bike"]}])
    assert ids(bike) == ["a.yml", "c.yml"] and bike["total"] == 2
    assert ids(rows(client, filter=[{"col": "count", "op": "between", "val": [2, 5]}])) == ["a.yml"]
    assert ids(rows(client, filter=[{"col": "tags", "op": "none_of", "val": ["bike"]}])) == ["b.yml"]
    assert ids(rows(client, ids=["b.yml"])) == ["b.yml"]  # by its key
    assert ids(rows(client, filter=[{"col": "fits", "op": "in", "val": [True]}])) == ["a.yml"]  # a yes/no column
    assert ids(rows(client, filter=[{"col": "fits", "op": "isnull", "val": None}])) == ["c.yml"]
    paged = rows(client, sort="project", offset=1, limit=1)
    assert ids(paged) == ["a.yml"] and paged["total"] == 3
    query = {"filter": json.dumps([{"col": "project", "op": "contains", "val": "al"}]), "sort": "project"}
    got = client.get(f"{TABLE}/rows", params=query).json()
    assert ids(got) == ["b.yml", "c.yml"]
    bad = client.post(f"{TABLE}/rows", json={"filter": [{"col": "nope", "op": "eq", "val": 1}]})
    assert bad.status_code == 400


def test_distinct_lists_a_comma_columns_tokens(client):
    got = client.get(f"{TABLE}/distinct", params={"col": "tags"}).json()
    assert got == {"values": ["bike", "safety", "transit"], "truncated": False, "list_like": True}
    assert client.get(f"{TABLE}/distinct", params={"col": "fits"}).json()["values"] == ["False", "True"]


def test_locate_finds_a_row_in_the_views_order(client):
    got = client.post(f"{TABLE}/locate", json={"id": "c.yml", "sort": "project", "dir": "desc"}).json()
    assert got == {"index": 0}


def test_the_frame_is_kept_until_the_plugin_publishes_or_the_network_changes(tmp_path, isolated_env, probe):
    session = _session(tmp_path, isolated_env, probe.plugin())
    client = TestClient(build_app(session))
    events: list[dict[str, Any]] = []
    session.events.publish = events.append
    rows(client)
    rows(client, sort="project")
    assert probe.calls == 1  # built once for the network as it is
    probe.rows = ROWS[:1]
    assert ids(rows(client)) == ["a.yml", "b.yml", "c.yml"]  # still the kept frame
    probe.host.publish("updated", {"n": 1})
    assert ids(rows(client)) == ["a.yml"] and probe.calls == 2
    assert events[-1] == {
        "type": "plugin",
        "plugin": "core",
        "name": "table_changed",
        "payload": {"plugin": "probe", "tables": ["things"]},
    }
    assert events[-2]["name"] == "updated"  # the plugin's own event first
    session.dispatch(EditCells(net_id=NET, ids=[1], values={"lanes": 5}))  # a new network version
    rows(client)
    assert probe.calls == 3


def test_an_empty_table_keeps_its_columns(tmp_path, isolated_env):
    client = TestClient(build_app(_session(tmp_path, isolated_env, Probe([]).plugin())))
    payload = rows(client)
    assert payload["columns"] == ["thing_id", "project", "tags", "count", "fits"] and payload["total"] == 0


def test_rows_without_their_key_or_a_failing_rows_fn_are_a_500_naming_the_plugin(tmp_path, isolated_env):
    probe = Probe([{"thing_id": "a"}, {"thing_id": "a"}])
    session = _session(tmp_path, isolated_env, probe.plugin())
    client = TestClient(build_app(session), raise_server_exceptions=False)
    response = client.post(f"{TABLE}/rows", json={})
    assert response.status_code == 500 and "probe plugin's table 'things' failed" in response.json()["detail"]
    tables = client.get(f"/api/n/{NET}/roadway/tables").json()["tables"]
    (things,) = [t for t in tables if t["name"] == "things"]
    assert things["rows"] == 0 and "every row needs its own 'thing_id'" in things["error"]  # the rest still list

    def broken() -> list[dict[str, Any]]:
        raise RuntimeError("no rows today")

    probe.host.register_table("things", "Things", broken, primary_key="thing_id")
    assert "no rows today" in client.get(f"{TABLE}/schema").json()["detail"]


def test_an_unknown_table_is_still_a_404(client):
    assert client.post(f"/api/n/{NET}/roadway/table/nope/rows", json={}).status_code == 404


def test_register_refuses_bad_names_and_another_plugins_table():
    tables = PluginTables()
    rows_fn = list
    for bad in ("Things", "1st", "has-dash", "", None):
        with pytest.raises(ValueError, match="a table name matches"):
            tables.register("probe", bad, "T", rows_fn, primary_key="id")  # type: ignore[arg-type]
    for name in sorted(NETWORK_TABLE_NAMES):
        with pytest.raises(ValueError, match="network table's name"):
            tables.register("probe", name, "T", rows_fn, primary_key="id")
    with pytest.raises(ValueError, match="needs a title"):
        tables.register("probe", "t", " ", rows_fn, primary_key="id")
    with pytest.raises(ValueError, match="needs a primary_key"):
        tables.register("probe", "t", "T", rows_fn, primary_key="")
    with pytest.raises(TypeError, match="must be callable"):
        tables.register("probe", "t", "T", [], primary_key="id")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must include its primary key"):
        tables.register("probe", "t", "T", rows_fn, primary_key="id", columns=["x"])
    tables.register("probe", "t", "T", rows_fn, primary_key="id")
    with pytest.raises(ValueError, match="the probe plugin's"):
        tables.register("other", "t", "T", rows_fn, primary_key="id")
    replaced = tables.register("probe", "t", "Again", rows_fn, primary_key="id")
    assert tables.get("t") is replaced and [t.title for t in tables.all()] == ["Again"]
    assert tables.unregister("other", "t") is False and tables.get("t") is replaced
    assert tables.changed("other") == [] and tables.changed("probe") == ["t"]


def test_a_frame_from_rows_fn_or_a_dataframe():
    import pandas as pd

    tables = PluginTables()
    frame = pd.DataFrame({"id": [2, 1], "v": ["x", "y"]}, index=[10, 11])
    table = tables.register("probe", "t", "T", lambda: frame, primary_key="id")
    built = table.frame("key")
    assert list(built.index) == [0, 1] and built is not frame  # a copy, its index reset
    assert table.frame("key") is built and table.frame("other") is not built  # one frame per key
    empty = tables.register("probe", "e", "E", list, primary_key="id")
    assert list(empty.frame(None).columns) == ["id"]
    keyless = tables.register("probe", "k", "K", lambda: [{"v": 1}], primary_key="id")
    with pytest.raises(ValueError, match="has no 'id' column"):
        keyless.frame(None)


def test_unregister_takes_the_table_out(client, probe):
    assert probe.host.unregister_table("things") is True
    assert probe.host.unregister_table("things") is False
    assert "things" not in [t["name"] for t in client.get(f"/api/n/{NET}/roadway/tables").json()["tables"]]
    assert client.post(f"{TABLE}/rows", json={}).status_code == 404


def test_unloading_the_plugin_takes_its_tables(tmp_path, isolated_env, probe):
    session = _session(tmp_path, isolated_env, probe.plugin())
    assert session.plugin_tables.get("things") is not None
    session.unload_plugin("probe", "its mount failed")
    assert session.plugin_tables.get("things") is None


def test_a_plugin_that_fails_to_install_leaves_no_table(tmp_path, isolated_env):
    session = _session(tmp_path, isolated_env, Probe(fail_load=True).plugin())
    (status,) = [s for s in session.plugin_status if s.id == "probe"]
    assert status.state == "error" and session.plugin_tables.get("things") is None


# ---------------------------------------------------------------------------- the browser side


def test_wb_show_table_asks_the_table_view_and_refuses_without_one(node_module):
    got = node_module(
        "wbhost.js",
        ["createWb"],
        """await (async () => {
  const shown = [];
  const base = { onError() {}, store: { get: () => ({}) } };
  const { wb } = createWb("probe", { ...base, showTable: async name => { shown.push(name); return "shown"; } });
  const bare = createWb("probe", base).wb;
  let refused = null;
  try { await bare.showTable("things"); } catch (e) { refused = e.message; }
  return { result: await wb.showTable("things"), shown, refused };
})()""",
    )
    assert got == {
        "result": "shown",
        "shown": ["things"],
        "refused": "probe: this Workbench has no table view to show things in",
    }


def test_the_rail_lists_plugin_tables_apart_and_queries_them_plainly(node_module):
    got = node_module(
        "plugintables.js",
        ["splitTables", "pluginTableTip", "pluginRowsQuery", "pluginHint", "tableRowEvent", "tableChanged"],
        """(() => {
  const tables = [{ name: "link", rows: 2 }, { name: "things", rows: 3, plugin: "probe", title: "Things" },
                  { name: "bad", rows: 0, plugin: "probe", title: "Bad", error: "boom" }];
  const { core, plugin } = splitTables(tables);
  const t = plugin[0];
  return {
    core: core.map(x => x.name), plugin: plugin.map(x => x.name), none: splitTables(null),
    tips: plugin.map(pluginTableTip),
    query: pluginRowsQuery({ offset: 0, limit: 100, sort: "project", dir: "asc", conditions: [] }),
    filtered: pluginRowsQuery({ offset: 0, limit: 100, sort: null, dir: "asc",
      conditions: [{ col: "tags", op: "any_of", val: ["bike"] }] }).filter,
    hints: [pluginHint(t, 3), pluginHint(t, 0)],
    event: tableRowEvent(t, "a.yml"),
    changed: [tableChanged({ tables: ["things"] }, "things"), tableChanged({ tables: [] }, "things"),
              tableChanged(null, "things")],
  };
})()""",
    )
    assert got["core"] == ["link"] and got["plugin"] == ["things", "bad"]
    assert got["none"] == {"core": [], "plugin": []}
    assert got["tips"] == [
        "Things: read-only, from the probe plugin",
        "Bad: read-only, from the probe plugin (it failed: boom)",
    ]
    assert got["query"] == {"offset": 0, "limit": 100, "sort": "project", "dir": "asc", "filter": None}
    assert got["filtered"] == [{"col": "tags", "op": "any_of", "val": ["bike"]}]
    assert got["hints"] == [
        "Read-only, from the probe plugin: click a row to show it there.",
        "Read-only, from the probe plugin.",
    ]
    assert got["event"] == {"plugin": "probe", "table": "things", "id": "a.yml"}
    assert got["changed"] == [True, False, False]
