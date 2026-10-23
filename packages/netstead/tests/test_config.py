"""Tests for netstead.config — layered settings."""

import sys
import tomllib
from pathlib import Path

import pytest
from netstead.config import (
    Settings,
    SettingsError,
    dumps_toml,
    get_value,
    is_local_url,
    load_settings,
    save_setting,
    user_config_path,
)
from netstead.spec import DEFAULT_SPEC


def _write_user(env: dict[str, str], text: str) -> Path:
    path = Path(env["NETSTEAD_CONFIG_DIR"]) / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_defaults_when_no_files(tmp_path, isolated_env):
    loaded = load_settings(project_dir=tmp_path, environ=isolated_env)
    assert loaded.settings.select.provider == "stub"
    assert loaded.settings.io.spec_version == DEFAULT_SPEC
    assert loaded.settings.app.port == 8850
    assert loaded.sources["select.provider"] == "default"


def test_precedence_user_project_env_session(tmp_path, isolated_env):
    _write_user(isolated_env, '[app]\nport = 9001\nhost = "0.0.0.0"\n[viz]\nbasemap = "esri"\n')
    (tmp_path / "netstead.toml").write_text("[app]\nport = 9002\n")
    env = {**isolated_env, "NETSTEAD_SELECT__PROVIDER": "claude", "NETSTEAD_APP__PORT": "9003"}
    loaded = load_settings(project_dir=tmp_path, environ=env, overrides={"app.port": 9004})
    s = loaded.settings
    assert (s.viz.basemap, s.app.host, s.select.provider, s.app.port) == ("esri", "0.0.0.0", "anthropic", 9004)
    assert loaded.sources["viz.basemap"] == "user"
    assert loaded.sources["select.provider"] == "env"
    assert loaded.sources["app.port"] == "session"


def test_project_beats_user(tmp_path, isolated_env):
    _write_user(isolated_env, "[app]\nport = 9001\n")
    (tmp_path / "netstead.toml").write_text("[app]\nport = 9002\n")
    loaded = load_settings(project_dir=tmp_path, environ=isolated_env)
    assert loaded.settings.app.port == 9002
    assert loaded.sources["app.port"] == "project"


def test_env_json_list_and_unrelated_vars_ignored(tmp_path, isolated_env):
    env = {**isolated_env, "NETSTEAD_IO__ALLOWED_ROOTS": '["/data", "/tmp"]', "NETSTEAD_AUTO_INDEX_THRESHOLD": "5"}
    loaded = load_settings(project_dir=tmp_path, environ=env)
    assert loaded.settings.io.allowed_roots == ["/data", "/tmp"]


@pytest.mark.parametrize("roots", ['[""]', '["/data", "   "]'])
def test_blank_allowed_roots_entry_is_rejected(tmp_path, isolated_env, roots):
    """A blank entry would resolve to the current folder, not mean "unset"; refuse it by name."""
    env = {**isolated_env, "NETSTEAD_IO__ALLOWED_ROOTS": roots}
    with pytest.raises(SettingsError, match="blank"):
        load_settings(project_dir=tmp_path, environ=env)


def test_rule_settings_nest_under_validation(tmp_path, isolated_env):
    (tmp_path / "netstead.toml").write_text(
        '[validation.rules.dangling-node]\nenabled = false\nseverity_override = "warning"\n'
    )
    rules = load_settings(project_dir=tmp_path, environ=isolated_env).settings.validation.rules
    assert rules["dangling-node"].enabled is False
    assert rules["dangling-node"].severity_override == "warning"


def test_unknown_key_in_file_rejected(tmp_path, isolated_env):
    (tmp_path / "netstead.toml").write_text("[select]\nbogus = 1\n")
    with pytest.raises(SettingsError, match="project"):
        load_settings(project_dir=tmp_path, environ=isolated_env)


def test_invalid_toml_names_the_file(tmp_path, isolated_env):
    (tmp_path / "netstead.toml").write_text("[app\n")
    with pytest.raises(SettingsError, match=r"netstead\.toml"):
        load_settings(project_dir=tmp_path, environ=isolated_env)


def test_bad_env_json_rejected(tmp_path, isolated_env):
    with pytest.raises(SettingsError, match="NETSTEAD_IO__ALLOWED_ROOTS"):
        load_settings(project_dir=tmp_path, environ={**isolated_env, "NETSTEAD_IO__ALLOWED_ROOTS": "[oops"})


def test_user_config_path_override():
    assert user_config_path({"NETSTEAD_CONFIG_DIR": "/cfg"}) == Path("/cfg/config.toml")


@pytest.mark.skipif(sys.platform == "win32", reason="XDG applies off Windows only")
def test_user_config_path_xdg():
    assert user_config_path({"XDG_CONFIG_HOME": "/x"}) == Path("/x/netstead/config.toml")


def test_get_value():
    assert get_value(Settings(), "app.port") == 8850
    with pytest.raises(SettingsError, match="unknown setting"):
        get_value(Settings(), "app.nope")


def test_save_setting_round_trips_and_coerces(tmp_path, isolated_env):
    path = save_setting("app.port", "9100", scope="user", environ=isolated_env)
    assert path == Path(isolated_env["NETSTEAD_CONFIG_DIR"]) / "config.toml"
    assert "port = 9100" in path.read_text()
    assert load_settings(project_dir=tmp_path, environ=isolated_env).settings.app.port == 9100


def test_save_setting_project_scope_keeps_other_keys(tmp_path, isolated_env):
    (tmp_path / "netstead.toml").write_text('[viz]\nbasemap = "esri"\n')
    save_setting("select.provider", "claude", scope="project", project_dir=tmp_path, environ=isolated_env)
    s = load_settings(project_dir=tmp_path, environ=isolated_env).settings
    assert (s.viz.basemap, s.select.provider) == ("esri", "anthropic")
    assert (
        'provider = "anthropic"' in (tmp_path / "netstead.toml").read_text()
    )  # the alias is stored under its new name


def test_save_setting_none_resets_to_default(tmp_path, isolated_env):
    save_setting("app.port", 9100, scope="user", environ=isolated_env)
    save_setting("app.port", None, scope="user", environ=isolated_env)
    loaded = load_settings(project_dir=tmp_path, environ=isolated_env)
    assert loaded.settings.app.port == 8850 and loaded.sources["app.port"] == "default"


def test_save_setting_rejects_unknown_key_without_writing(tmp_path, isolated_env):
    with pytest.raises(SettingsError):
        save_setting("select.bogus", 1, scope="user", environ=isolated_env)
    assert not (Path(isolated_env["NETSTEAD_CONFIG_DIR"]) / "config.toml").exists()


def test_dumps_toml_round_trips_nested_tables_and_quoting():
    data = {
        "io": {"allowed_roots": ["/a", 'b "q"'], "spec_version": "0.97"},
        "validation": {"rules": {"dangling-node": {"enabled": False, "thresholds": {"max": 2.5}}, "my rule": {}}},
    }
    assert tomllib.loads(dumps_toml(data)) == data


def test_approval_threshold_and_build_defaults(tmp_path, isolated_env):
    s = load_settings(project_dir=tmp_path, environ=isolated_env).settings
    assert s.app.approve_above_s == 90.0
    assert (s.build.network_type, s.build.buffer_m, s.build.extra_tags) == (["roads"], 1000.0, [])


def test_negative_approval_threshold_rejected(tmp_path, isolated_env):
    (tmp_path / "netstead.toml").write_text("[app]\napprove_above_s = -1\n")
    with pytest.raises(SettingsError, match="approve_above_s"):
        load_settings(project_dir=tmp_path, environ=isolated_env)


def test_select_provider_claude_alias_new_providers_and_model_default(tmp_path, isolated_env):
    for given, stored in (("claude", "anthropic"), ("openai", "openai"), ("gemini", "gemini"), ("ollama", "ollama")):
        s = load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"select.provider": given}).settings
        assert s.select.provider == stored
    assert load_settings(project_dir=tmp_path, environ=isolated_env).settings.select.model is None


def test_llm_section_defaults_env_and_base_url_validation(tmp_path, isolated_env):
    s = load_settings(project_dir=tmp_path, environ=isolated_env).settings
    assert (s.llm.ollama.base_url, s.llm.ollama.timeout_s) == ("http://localhost:11434", 120.0)
    assert (s.llm.openai.base_url, s.llm.openai.timeout_s) == (None, 60.0)
    env = {
        **isolated_env,
        "NETSTEAD_LLM__OLLAMA__BASE_URL": "http://gpu-box:11434/",
        "NETSTEAD_LLM__OPENAI__TIMEOUT_S": "30",
    }
    s = load_settings(project_dir=tmp_path, environ=env).settings
    assert (s.llm.ollama.base_url, s.llm.openai.timeout_s) == ("http://gpu-box:11434", 30.0)
    with pytest.raises(SettingsError, match="base_url must be an http"):
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.openai.base_url": "ftp://x"})
    with pytest.raises(SettingsError):  # there is no place for a key in settings
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.openai.api_key": "nope"})


@pytest.mark.parametrize("url", ["https://user:SECRETTOK@llm.example.org/v1", "http://SECRETTOK@localhost:11434"])
def test_llm_base_url_with_userinfo_rejected_without_echo(tmp_path, isolated_env, url):
    with pytest.raises(SettingsError, match="must not contain a username") as info:
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.ollama.base_url": url})
    assert "SECRETTOK" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_llm_quality_defaults_and_bounds(tmp_path, isolated_env):
    q = load_settings(project_dir=tmp_path, environ=isolated_env).settings.llm.quality
    assert (q.grounding, q.project_context, q.assistant_context, q.few_shot) == ("auto", "auto", True, False)
    # match_retry is "auto": on for a local endpoint, an explicit opt-in ("on") for a remote one.
    assert (q.max_repairs, q.temperature, q.match_retry, q.grounding_max_names) == (1, 0.0, "auto", 200)
    with pytest.raises(SettingsError):
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.quality.max_repairs": 9})
    with pytest.raises(SettingsError):
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.quality.grounding": "always"})
    with pytest.raises(SettingsError):
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.quality.match_retry": "maybe"})


def test_match_retry_bool_aliases_and_resolution(tmp_path, isolated_env):
    def llm(**overrides):
        return load_settings(project_dir=tmp_path, environ=isolated_env, overrides=overrides).settings.llm

    assert (
        llm(**{"llm.quality.match_retry": True}).quality.match_retry,
        llm(**{"llm.quality.match_retry": False}).quality.match_retry,
    ) == ("on", "off")
    auto = llm()
    assert (auto.match_retry_on("ollama"), auto.match_retry_on("anthropic"), auto.match_retry_on("stub")) == (
        True,
        False,
        False,
    )
    assert llm(**{"llm.openai.base_url": "http://127.0.0.1:8000/v1"}).match_retry_on("openai")
    assert not llm(**{"llm.ollama.base_url": "http://gpu-box:11434"}).match_retry_on("ollama")
    assert llm(**{"llm.quality.match_retry": "on"}).match_retry_on("gemini")
    assert not llm(**{"llm.quality.match_retry": "off"}).match_retry_on("ollama")


def test_is_local_url_recognises_the_whole_loopback_range():
    assert is_local_url("http://localhost:11434")
    assert is_local_url("http://127.0.0.1:11434")
    assert is_local_url("http://127.0.0.2")  # still 127.0.0.0/8, not just .1
    assert is_local_url("http://[::1]:8000/v1")
    assert not is_local_url("http://0.0.0.0")  # bind-all, not loopback: stays remote
    assert not is_local_url("http://gpu-box:11434")
    assert not is_local_url("not-a-url")


@pytest.mark.parametrize("url", ["https://llm.example.org/v1?key=QMARKER", "https://llm.example.org/v1#QMARKER"])
def test_base_url_query_or_fragment_rejected(tmp_path, isolated_env, url):
    with pytest.raises(SettingsError, match="query string or fragment") as info:
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"llm.openai.base_url": url})
    assert "QMARKER" not in str(info.value)


def test_settings_error_carries_no_input_or_context(tmp_path, isolated_env):
    with pytest.raises(SettingsError) as info:
        load_settings(project_dir=tmp_path, environ=isolated_env, overrides={"select.model": ["MARKERtok"]})
    assert "MARKER" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_plugin_tables_are_free_form_and_layered(tmp_path):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "cfg"), "NETSTEAD_PLUGINS__HELLO__PREFIX": "Hi"}
    (tmp_path / "netstead.toml").write_text("[plugins.hello]\ncount = 3\n", encoding="utf-8")
    loaded = load_settings(project_dir=tmp_path, environ=env)
    assert loaded.settings.plugins == {"hello": {"count": 3, "prefix": "Hi"}}
    assert loaded.sources["plugins.hello.prefix"] == "env" and loaded.sources["plugins.hello.count"] == "project"


def test_disabled_plugins_defaults_empty(tmp_path):
    loaded = load_settings(project_dir=tmp_path, environ={"NETSTEAD_CONFIG_DIR": str(tmp_path / "cfg")})
    assert loaded.settings.app.disabled_plugins == []


_KEY = "sk-ant-api03-" + "MARKER" * 6


@pytest.mark.parametrize(
    ("name", "value", "where"),
    [
        ("NETSTEAD_PLUGINS__FOO__API_KEY", "MARKERvalue", "plugins.foo.api_key"),
        ("NETSTEAD_PLUGINS__FOO__PREFIX", _KEY, "plugins.foo.prefix"),
        ("NETSTEAD_PLUGINS__FOO__ENDPOINT", "https://user:MARKER@example.com/", "plugins.foo.endpoint"),
        ("NETSTEAD_PLUGINS__FOO__AUTH", '{"password": "MARKER"}', "plugins.foo.auth.password"),
        ("NETSTEAD_PLUGINS__FOO__BASE_URL", "https://example.com/v1?sig=MARKER", "plugins.foo.base_url"),
    ],
)
def test_a_secret_in_plugin_settings_from_env_is_refused(tmp_path, name, value, where):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "cfg"), name: value}
    with pytest.raises(SettingsError) as info:
        load_settings(project_dir=tmp_path, environ=env)
    assert where in str(info.value) and "env layer" in str(info.value)
    assert "MARKER" not in str(info.value) and info.value.__context__ is None


@pytest.mark.parametrize(
    "toml",
    [
        '[plugins.foo]\ntoken = "MARKER"\n',
        f'[plugins.foo]\nprefix = "{_KEY}"\n',
        '[plugins.foo.nested]\nclient_secret = "MARKER"\n',
        '[plugins.foo]\nmirrors = ["https://ok.example.com", "https://u:MARKER@example.com"]\n',
        f'[plugins.foo]\n"{_KEY}" = 1\n',  # a key-shaped key is not named either
    ],
)
def test_a_secret_in_plugin_settings_from_toml_is_refused(tmp_path, isolated_env, toml):
    (tmp_path / "netstead.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(SettingsError) as info:
        load_settings(project_dir=tmp_path, environ=isolated_env)
    assert "plugins" in str(info.value) and "project layer" in str(info.value)
    assert "MARKER" not in str(info.value) and info.value.__context__ is None


def test_save_setting_refuses_a_plugin_secret_before_writing(tmp_path, isolated_env):
    with pytest.raises(SettingsError, match=r"plugins\.foo\.token"):
        save_setting("plugins.foo.token", "MARKER", scope="project", project_dir=tmp_path, environ=isolated_env)
    assert not (tmp_path / "netstead.toml").exists()


def test_assistant_settings_defaults():
    from netstead.config import DEFAULT_ASSISTANT_ACTIONS, Settings

    a = Settings().assistant
    assert a.actions == list(DEFAULT_ASSISTANT_ACTIONS) and a.plugins == []
    assert "open_network" not in a.actions and "close_network" not in a.actions
    assert (a.output_token_limit, a.history_turns, a.remote_token_budget) == (1024, 6, 200_000)
    assert (a.context_max_chars, a.max_turns, a.max_steps) == (12000, 50, 6)


def test_no_assistant_setting_reads_as_a_secret_name():
    from netstead.config import AssistantSettings
    from netstead.llm.secrets import is_secret_name

    assert not [name for name in AssistantSettings.model_fields if is_secret_name(name)]
