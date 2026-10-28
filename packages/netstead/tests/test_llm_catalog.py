"""Tests for netstead.llm.catalog — the maintained provider/model catalog."""

import pytest
from netstead.llm.catalog import CATALOG_OVERLAY, load_catalog


def test_packaged_catalog_has_the_four_providers_in_order():
    cat = load_catalog()
    assert cat.names() == ["anthropic", "openai", "gemini", "ollama"]
    assert cat["ollama"].kind == "local" and cat["anthropic"].kind == "remote"
    assert cat["anthropic"].key_env == ("NETSTEAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def test_anthropic_models_are_the_current_ids():
    anthropic = load_catalog()["anthropic"]
    assert [m.id for m in anthropic.models] == ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert [m.tier for m in anthropic.models] == ["fast", "balanced", "best"]
    assert anthropic.default_model == "claude-haiku-5-5"


def test_gpt_6_1_sol_has_no_tool_calling_but_gpt_6_luna_does():
    """gpt-6.1-sol's docs say Chat Completions is supported without tool calling, so the catalog
    marks it tools=false; make_parser reads this to start those models in JSON mode directly."""
    openai = load_catalog()["openai"]
    assert openai.model("gpt-6.1-sol").tools is False
    assert openai.model("gpt-6-luna").tools is True


def test_qwen3_models_are_marked_thinking_and_others_default_to_false():
    ollama = load_catalog()["ollama"]
    assert ollama.model("qwen3:4b").thinking is True
    assert ollama.model("qwen3:8b").thinking is True
    assert ollama.model("qwen2.5:7b").thinking is False and ollama.model("qwen2.5:7b").tools is True
    anthropic = load_catalog()["anthropic"]
    assert anthropic.model("claude-haiku-5-5").thinking is False


def test_every_default_model_is_listed_and_every_model_has_a_label():
    for info in load_catalog().providers.values():
        assert info.model(info.default_model) is not None, info.name
        assert all(m.label for m in info.models)


def test_user_overlay_adds_and_relabels_but_cannot_move_keys(tmp_path):
    (tmp_path / CATALOG_OVERLAY).write_text(
        "[openai]\n"
        'default_model = "my-model"\n'
        'base_url = "https://evil.example/v1"\n'
        'key_env = ["EVIL"]\n'
        "[[openai.models]]\n"
        'id = "my-model"\n'
        'label = "Mine"\n'
        'tier = "fast"\n'
        "[mystery]\n"
        'label = "No adapter"\n'
    )
    cat = load_catalog(tmp_path)
    openai = cat["openai"]
    assert openai.default_model == "my-model" and openai.model("my-model").label == "Mine"
    assert openai.base_url == "https://api.openai.com/v1" and openai.key_env == (
        "NETSTEAD_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    )
    assert "mystery" not in cat.names()


def test_unknown_provider_and_bad_entries_raise(tmp_path):
    with pytest.raises(KeyError, match="unknown LLM provider 'mistral'"):
        load_catalog()["mistral"]
    (tmp_path / CATALOG_OVERLAY).write_text('[[anthropic.models]]\nlabel = "no id"\n')
    with pytest.raises(ValueError, match="every model needs an id"):
        load_catalog(tmp_path)


def test_overlay_invalid_toml_raises_with_the_path(tmp_path):
    overlay = tmp_path / CATALOG_OVERLAY
    overlay.write_text("this is not [ valid toml")
    with pytest.raises(ValueError, match=r"invalid TOML") as info:
        load_catalog(tmp_path)
    assert str(overlay) in str(info.value)


def test_overlay_provider_body_must_be_a_table(tmp_path):
    (tmp_path / CATALOG_OVERLAY).write_text("openai = 'not a table'\n")
    with pytest.raises(ValueError, match=r"provider 'openai' must be a table"):
        load_catalog(tmp_path)


def test_overlay_models_must_be_a_list_of_tables(tmp_path):
    (tmp_path / CATALOG_OVERLAY).write_text('[openai]\nmodels = ["not", "a", "table"]\n')
    with pytest.raises(ValueError, match=r"models must be a list|has a model entry that isn't a table"):
        load_catalog(tmp_path)

    (tmp_path / CATALOG_OVERLAY).write_text('[openai]\nmodels = "not a list"\n')
    with pytest.raises(ValueError, match=r"models must be a list"):
        load_catalog(tmp_path)


def test_overlay_invalid_tier_becomes_none(tmp_path):
    (tmp_path / CATALOG_OVERLAY).write_text('[[openai.models]]\nid = "gpt-4.1-mini"\ntier = "legendary"\n')
    cat = load_catalog(tmp_path)
    assert cat["openai"].model("gpt-4.1-mini").tier is None


def test_anthropic_models_say_what_their_requests_may_carry():
    anthropic = load_catalog()["anthropic"]
    haiku, sonnet = anthropic.model("claude-haiku-5-5"), anthropic.model("claude-sonnet-5-5")
    assert (haiku.sampling, haiku.forced_tools, haiku.effort) == (False, True, "low")
    assert (sonnet.sampling, sonnet.forced_tools, sonnet.effort) == (False, False, "low")
    assert load_catalog()["ollama"].model("qwen2.5:7b").sampling is True  # the default elsewhere
