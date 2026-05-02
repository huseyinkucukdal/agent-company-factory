"""LLMSettings: env parsing and defaults."""
from __future__ import annotations

from modules.llm import LLMProvider, LLMSettings


def test_defaults_to_mock_provider() -> None:
    s = LLMSettings()
    assert s.provider is LLMProvider.MOCK
    assert s.model == "gpt-4o-mini"
    assert s.timeout_seconds == 30.0


def test_from_env_picks_provider_and_keys() -> None:
    env = {
        "LLM_PROVIDER": "github_models",
        "LLM_MODEL": "gpt-4o",
        "LLM_TEMPERATURE": "0.2",
        "LLM_MAX_TOKENS": "1024",
        "GITHUB_TOKEN": "ghp_xxx",
    }
    s = LLMSettings.from_env(env)
    assert s.provider is LLMProvider.GITHUB_MODELS
    assert s.model == "gpt-4o"
    assert s.temperature == 0.2
    assert s.max_tokens == 1024
    assert s.github_token == "ghp_xxx"


def test_from_env_unknown_provider_falls_back_to_mock() -> None:
    s = LLMSettings.from_env({"LLM_PROVIDER": "ollama"})
    assert s.provider is LLMProvider.MOCK


def test_anthropic_creds_picked_up() -> None:
    s = LLMSettings.from_env(
        {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
    )
    assert s.provider is LLMProvider.ANTHROPIC
    assert s.anthropic_api_key == "sk-ant-xxx"


def test_invalid_numeric_env_uses_default() -> None:
    s = LLMSettings.from_env({"LLM_TEMPERATURE": "not-a-number"})
    assert s.temperature == 0.7
