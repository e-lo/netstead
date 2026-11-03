"""Tests for `netstead llm`: key status, set, remove, test and models from the terminal."""

import json

import httpx
import pytest
from netstead.cli.app import app
from netstead.llm.secrets import KEYRING_SERVICE
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("no_network")

KEY = "sk-test-cli-abcdefghijklmnopqrstuvwxyz"
KEY_ENV = (
    "NETSTEAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "NETSTEAD_OPENAI_API_KEY",
    "OPENAI_API_KEY", "NETSTEAD_GEMINI_API_KEY", "GEMINI_API_KEY",
)  # fmt: skip
runner = CliRunner()


@pytest.fixture
def ring(tmp_path, monkeypatch, fake_keyring, fake_api):
    """Isolated config dir and env, the fake keyring as the 'system' one, and the fake API as the network."""
    import netstead.llm

    monkeypatch.setenv("NETSTEAD_CONFIG_DIR", str(tmp_path / "user"))
    monkeypatch.chdir(tmp_path)
    for name in KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("netstead.llm.secrets.system_keyring", lambda: fake_keyring)
    real = netstead.llm.build_registry
    monkeypatch.setattr(
        netstead.llm, "build_registry", lambda settings, **kw: real(settings, transport=fake_api.transport(), **kw)
    )
    return fake_keyring


def _llm(*args, input=None):
    return runner.invoke(app, ["llm", *args], input=input)


def test_status_json_lists_providers(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "qwen3:8b"}]})
    result = _llm("status", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [r["provider"] for r in data["providers"]] == ["anthropic", "openai", "gemini", "ollama"]
    assert data["keyring"] is True and data["providers"][-1]["usable"] is True


def test_set_key_prompts_hidden_and_never_echoes(ring):
    result = _llm("set-key", "openai", input=KEY + "\n")
    assert result.exit_code == 0, result.output
    assert "stored the OpenAI key in the keyring" in result.output and KEY not in result.output
    assert ring.store[(KEYRING_SERVICE, "openai")] == KEY


def test_set_key_from_stdin(ring):
    assert _llm("set-key", "anthropic", "--stdin", input=KEY + "\n").exit_code == 0
    assert ring.store[(KEYRING_SERVICE, "anthropic")] == KEY


def test_remove_key(ring):
    _llm("set-key", "gemini", input=KEY + "\n")
    result = _llm("remove-key", "gemini")
    assert result.exit_code == 0 and "removed the Gemini key from: keyring" in result.output and ring.store == {}


def test_local_provider_needs_no_key_and_unknown_provider_exits_2(ring):
    local = _llm("set-key", "ollama", input="x\n")
    unknown = _llm("test", "mistral")
    assert (local.exit_code, unknown.exit_code) == (2, 2)
    assert "needs no API key" in local.output and "unknown provider 'mistral'" in unknown.output


def test_test_exit_codes(ring, fake_api):
    ring.set_password(KEYRING_SERVICE, "anthropic", "k")
    fake_api.add("GET", "/v1/models", body={"data": [{"id": "claude-sonnet-5"}]})
    ok = _llm("test", "anthropic")
    missing = _llm("test", "openai")
    assert ok.exit_code == 0 and "Anthropic: connected (1 models available)." in ok.output
    assert "catalog ids not served here" in ok.output
    assert missing.exit_code == 1 and "OpenAI: no API key is configured" in missing.output


def test_models_lists_the_catalog(ring):
    result = _llm("models", "anthropic")
    assert result.exit_code == 0 and "claude-haiku-5-5" in result.output and "fast" in result.output


def test_status_text_shows_source_not_key(ring, fake_api):
    ring.set_password(KEYRING_SERVICE, "openai", KEY)
    fake_api.add("GET", "/api/tags", raises=httpx.ConnectError("refused"))
    result = _llm("status")
    assert result.exit_code == 0 and "key set (keyring)" in result.output and KEY not in result.output
    assert "could not reach Ollama" in result.output


def test_set_key_without_a_keyring_names_the_env_vars(ring, monkeypatch):
    monkeypatch.setattr("netstead.llm.secrets.system_keyring", lambda: None)
    result = _llm("set-key", "gemini", input=KEY + "\n")
    assert result.exit_code == 1 and "set NETSTEAD_GEMINI_API_KEY or GEMINI_API_KEY" in result.output
    assert "key storage: none (no OS keyring)" in _llm("status").output


# ------------------------------------------------------------------ netstead llm pull / status next steps

PULL_LINES = b"".join(
    json.dumps(line).encode() + b"\n"
    for line in (
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "sha256:abc", "total": 2000, "completed": 1000},
        {"status": "pulling abc", "digest": "sha256:abc", "total": 2000, "completed": 2000},
        {"status": "success"},
    )
)


def _pulls(fake_api):
    return [r for r in fake_api.requests if r.url.path == "/api/pull"]


def test_pull_yes_streams_and_reports(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    fake_api.add("POST", "/api/pull", body=PULL_LINES)
    result = _llm("pull", "qwen3:4b", "--yes")
    assert result.exit_code == 0, result.output
    assert "pulled qwen3:4b" in result.stdout and "[y/N]" not in result.output
    assert json.loads(_pulls(fake_api)[0].content) == {"model": "qwen3:4b", "stream": True}


def test_pull_asks_first_with_the_catalog_size(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    fake_api.add("POST", "/api/pull", body=PULL_LINES)
    result = _llm("pull", "qwen3:8b", input="n\n")
    assert result.exit_code == 1 and "Download qwen3:8b (about 5.2 GB)" in result.output
    assert _pulls(fake_api) == []  # declined: nothing downloaded
    result = _llm("pull", "qwen3:8b", input="y\n")
    assert result.exit_code == 0, result.output
    assert len(_pulls(fake_api)) == 1


def test_pull_bare_name_matches_an_installed_latest_tag(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "qwen3:latest"}]})
    fake_api.add("POST", "/api/pull", body=PULL_LINES)
    result = _llm("pull", "qwen3", "--yes")
    assert result.exit_code == 0, result.output
    assert "qwen3 is already installed; pulling again checks for an update." in result.output


def test_pull_unknown_size_says_several_gb(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    result = _llm("pull", "llama3.2:3b", input="n\n")
    assert "Download llama3.2:3b (several GB)" in result.output


def test_pull_unreachable_prints_install_and_start_hint(ring, fake_api):
    fake_api.add("GET", "/api/tags", raises=httpx.ConnectError("refused"))
    result = _llm("pull", "qwen3:4b", "--yes")
    assert result.exit_code == 1
    for hint in ("https://ollama.com/download", "ollama serve", "local-llm-ollama"):
        assert hint in result.output
    assert _pulls(fake_api) == []


def test_pull_failure_mid_stream_exits_1(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": []})
    fake_api.add("POST", "/api/pull", body=b'{"error":"pull model manifest: file does not exist"}\n')
    result = _llm("pull", "nosuch:1b", "--yes")
    assert result.exit_code == 1 and "no model called 'nosuch:1b'" in result.output


def test_pull_rejects_a_registry_host_or_url(ring, fake_api):
    result = _llm("pull", "evil.example/ns/model", "--yes")
    assert result.exit_code == 2 and fake_api.requests == []


def test_status_says_what_to_do_next_for_ollama(ring, fake_api):
    fake_api.add("GET", "/api/tags", raises=httpx.ConnectError("refused"))
    assert "next step: start Ollama" in _llm("status").output
    fake_api.routes.clear()
    fake_api.add("GET", "/api/tags", body={"models": []})
    assert "next step: run: netstead llm pull qwen3:4b" in _llm("status").output


def test_status_and_test_name_the_installed_stand_in_for_ollama(ring, fake_api):
    tags = {"models": [{"name": "qwen2.5:7b", "capabilities": ["completion", "tools"]}]}
    fake_api.add("GET", "/api/tags", body=tags)
    status = _llm("status")
    assert status.exit_code == 0, status.output
    assert "selections use qwen2.5:7b (qwen3:4b is not installed)" in status.output
    test = _llm("test", "ollama")
    assert test.exit_code == 0 and "Selections use qwen2.5:7b" in test.output


def test_status_says_pull_when_no_installed_model_has_tools(ring, fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "gemma2:2b", "capabilities": ["completion"]}]})
    status = _llm("status")
    assert "note: no installed model supports tool calling" in status.output and "ollama pull qwen3:4b" in status.output
    assert _llm("test", "ollama").exit_code == 1
