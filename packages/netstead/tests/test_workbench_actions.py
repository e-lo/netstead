"""Tests for workbench Action schemas."""

import pytest
from netstead.workbench.actions import (
    BuildNetwork,
    Navigate,
    OpenNetwork,
    Select,
    SetSetting,
    Style,
    action_json_schema,
    is_secret_name,
    parse_action,
    to_python,
)
from pydantic import ValidationError


def test_parse_action_discriminates_on_type():
    a = parse_action({"type": "open_network", "source": "/x"})
    assert isinstance(a, OpenNetwork) and a.source == "/x"


def test_unknown_type_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        parse_action({"type": "launch_rockets"})
    with pytest.raises(ValidationError):
        parse_action({"type": "open_network", "source": "/x", "bogus": 1})


def test_select_needs_exactly_one_of_utterance_or_link_ids():
    with pytest.raises(ValidationError):
        Select()
    with pytest.raises(ValidationError):
        Select(utterance="I-40", link_ids=[1])
    assert Select(link_ids=[1, 2]).component == "roadway"


def test_select_accepts_transit_component_in_schema():
    assert Select(utterance="route 38", component="transit").component == "transit"


def test_navigate_needs_exactly_one_target():
    with pytest.raises(ValidationError):
        Navigate()
    with pytest.raises(ValidationError):
        Navigate(to_network=True, to_selection=True)
    assert Navigate(bbox=(-79, 35, -78, 36)).bbox == (-79, 35, -78, 36)


def test_style_colors_must_be_rgb_triples():
    with pytest.raises(ValidationError):
        Style(colors={"links": [1, 2]})
    assert Style(show={"nodes": False}).show == {"nodes": False}


def test_set_setting_is_mutating():
    assert SetSetting.mutates is True and Select.mutates is False


def test_to_python_omits_defaults_and_type():
    assert to_python(OpenNetwork(source="/x")) == "app.do(OpenNetwork(source='/x'))"
    assert to_python(Select(utterance="I-40 EB")) == "app.do(Select(utterance='I-40 EB'))"
    assert to_python(Style(show={"nodes": False})) == "app.do(Style(show={'nodes': False}))"


def test_action_json_schema_lists_every_type():
    schema = action_json_schema()
    types = {v["properties"]["type"]["const"] for v in schema["$defs"].values() if "type" in v.get("properties", {})}
    assert {"open_network", "select", "style", "navigate", "set_setting"} <= types


def test_open_network_is_a_job_action():
    assert OpenNetwork.runs_as_job and not Select.runs_as_job and not OpenNetwork.mutates
    assert OpenNetwork.replay_overrides == {}


_BUILD = {"source": "osm", "output_dir": "/out", "output_format": "parquet", "name": "durham"}


def test_build_network_is_a_mutating_job_action():
    assert BuildNetwork.runs_as_job and BuildNetwork.mutates and BuildNetwork.replay_overrides == {"approved": True}


def test_job_labels_are_per_action():
    assert BuildNetwork(**_BUILD, input_file="/x.osm").job_label() == "build durham"
    assert OpenNetwork(source="/data/durham").job_label() == "open durham"


def test_build_network_needs_exactly_one_of_area_or_input_file():
    with pytest.raises(ValidationError, match="exactly one of area or input_file"):
        BuildNetwork(**_BUILD)
    with pytest.raises(ValidationError, match="exactly one of area or input_file"):
        BuildNetwork(**_BUILD, input_file="/x.osm", area={"kind": "bbox", "bbox": (-79, 35, -78, 36)})
    assert BuildNetwork(**_BUILD, input_file="/x.osm").approved is False


def test_build_network_name_is_a_plain_file_name():
    for bad in ("../escape", "a/b", ".hidden", "", "trailing.", "a.."):
        with pytest.raises(ValidationError):
            BuildNetwork(**{**_BUILD, "name": bad}, input_file="/x.osm")


def test_overture_release_only_with_overture():
    with pytest.raises(ValidationError, match="overture_release"):
        BuildNetwork(**_BUILD, input_file="/x.osm", overture_release="2025-12-17.0")


def test_build_network_parses_from_json_with_area_union():
    a = parse_action(
        {"type": "build_network", **_BUILD, "area": {"kind": "point", "lat": 36, "lon": -79, "buffer_m": 500}}
    )
    assert isinstance(a, BuildNetwork) and a.area.kind == "point"


def test_build_snippet_keeps_area_kind_and_forces_approval():
    a = BuildNetwork(**_BUILD, area={"kind": "bbox", "bbox": (-79, 35, -78, 36)})
    py = to_python(a)
    assert py == (
        "app.do(BuildNetwork(source='osm', area={'kind': 'bbox', 'bbox': (-79.0, 35.0, -78.0, 36.0)}, "
        "output_dir='/out', output_format='parquet', name='durham', approved=True))"
    )
    replayed = eval(py.removeprefix("app.do(").removesuffix(")"), {"BuildNetwork": BuildNetwork})
    assert replayed == a.model_copy(update={"approved": True})


def test_build_network_name_suffix_must_match_the_format():
    for name, fmt in (("net.zip", "parquet"), ("net.duckdb", "zip"), ("net.CSV", "parquet"), ("net.parquet", "csv")):
        with pytest.raises(ValidationError, match="output_format"):
            BuildNetwork(**{**_BUILD, "name": name, "output_format": fmt}, input_file="/x.osm")
    for name, fmt in (("net.zip", "zip"), ("net.duckdb", "duckdb"), ("v1.2", "parquet")):
        assert BuildNetwork(**{**_BUILD, "name": name, "output_format": fmt}, input_file="/x.osm").name == name


def test_set_setting_refuses_key_shaped_values():
    with pytest.raises(ValidationError, match="looks like an API key"):
        SetSetting(key="select.model", value="sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
    nested = {
        "type": "set_setting",
        "key": "llm",
        "value": {"openai": {"base_url": "AIzaSyA-abcdefghijklmnopqrstuvwxyz012"}},
    }
    with pytest.raises(ValidationError, match="looks like an API key"):
        parse_action(nested)
    assert SetSetting(key="select.model", value="claude-sonnet-5").value == "claude-sonnet-5"


def test_set_setting_refuses_urls_with_userinfo():
    with pytest.raises(ValidationError, match="must not contain a username"):
        SetSetting(key="llm.openai.base_url", value="https://me:tok@llm.example.org/v1")
    with pytest.raises(ValidationError, match="must not contain a username"):
        SetSetting(key="llm", value={"ollama": {"base_url": "http://tok@gpu-box:11434"}})
    assert SetSetting(key="llm.ollama.base_url", value="http://gpu-box:11434/a@b").value.endswith("a@b")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("llm.openai.api_key", "ghp_MARKERtok"),
        ("llm", {"openai": {"api_key": "MARKERtok"}}),
        ("llm.openai.token", "MARKERtok"),
        ("credentials", {"github": {"password": "MARKERtok"}}),
        ("x.Authorization", "Bearer MARKERtok"),
    ],
)
def test_set_setting_refuses_secret_named_settings_without_echo(key, value):
    with pytest.raises(ValidationError, match="never settings") as info:
        SetSetting(key=key, value=value)
    assert "MARKER" not in str(info.value)
    with pytest.raises(ValidationError) as info:
        parse_action({"type": "set_setting", "key": key, "value": value})
    assert "MARKER" not in str(info.value)


def test_set_setting_allows_legitimate_names():
    assert SetSetting(key="credentials.keyring_hosts", value=["api.example.org"]).key == "credentials.keyring_hosts"
    assert SetSetting(key="credentials", value={"keyring_hosts": []}).key == "credentials"
    assert SetSetting(key="llm.quality.match_retry", value="on").value == "on"


def test_action_validation_errors_hide_inputs():
    with pytest.raises(ValidationError) as info:
        SetSetting(key="select.model", value="x", scope="MARKERscope")
    assert "MARKER" not in str(info.value)
    with pytest.raises(ValidationError) as info:
        parse_action({"type": "set_setting", "key": "select.model", "value": "sk-ant-api03-MARKERabcdefghijklmnopqrst"})
    assert "MARKER" not in str(info.value)


def test_set_setting_url_checks_cover_keys_and_base_url_query():
    with pytest.raises(ValidationError, match="must not contain a username"):
        SetSetting(key="llm", value={"https://me:tok@host": 1})
    with pytest.raises(ValidationError, match="must not contain a username"):
        SetSetting(key="https://me:tok@host", value=1)
    with pytest.raises(ValidationError, match="query string or fragment"):
        SetSetting(key="llm.openai.base_url", value="https://llm.example.org/v1?key=abc")
    with pytest.raises(ValidationError, match="query string or fragment"):
        SetSetting(key="llm", value={"ollama": {"base_url": "http://localhost:11434#frag"}})


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("api_key", True),
        ("token", True),
        ("password", True),
        ("credentials", False),
        ("key_env", False),
        ("basemap", False),
        ("keyring_hosts", False),
    ],
)
def test_is_secret_name(name, expected):
    assert is_secret_name(name) is expected


def test_build_network_takes_several_network_types_and_reads_a_recorded_single_one():
    base = {**_BUILD, "input_file": "/x.osm"}
    assert BuildNetwork(**base).network_types == ["roads"]
    assert BuildNetwork(**base, network_types=["paths", "roads"]).network_types == ["paths", "roads"]
    # Retired names still replay: drive -> roads; walk, bike, all -> roads + paths.
    assert BuildNetwork.model_validate({**base, "network_type": "drive"}).network_types == ["roads"]
    assert BuildNetwork.model_validate({**base, "network_type": "walk"}).network_types == ["roads", "paths"]
    assert BuildNetwork(**base, network_types=["bike", "drive"]).network_types == ["roads", "paths"]
    assert BuildNetwork(**base, network_types=["all"]).network_types == ["roads", "paths"]
    with pytest.raises(ValidationError, match="unknown network type 'boat'"):
        BuildNetwork(**base, network_types=["boat"])
    with pytest.raises(ValidationError):
        BuildNetwork(**base, network_types=[])
    with pytest.raises(ValidationError, match="not both"):
        BuildNetwork.model_validate({**base, "network_type": "walk", "network_types": ["bike"]})
