"""build_llm_factory: provider selection, fallback, per-agent isolation."""
from __future__ import annotations

import pytest

from modules.llm import (
    LLMConfigError,
    LLMProvider,
    LLMSettings,
    MockLLMClient,
    build_llm_factory,
)


def test_mock_factory_returns_fresh_clients() -> None:
    factory = build_llm_factory(LLMSettings(provider=LLMProvider.MOCK))
    a = factory("co1", "ag1")
    b = factory("co1", "ag2")
    assert isinstance(a, MockLLMClient)
    assert isinstance(b, MockLLMClient)
    assert a is not b


def test_github_models_without_token_falls_back_to_mock() -> None:
    settings = LLMSettings(provider=LLMProvider.GITHUB_MODELS, github_token=None)
    factory = build_llm_factory(settings)
    client = factory("co", "ag")
    assert isinstance(client, MockLLMClient)


def test_github_models_without_token_raises_when_no_fallback() -> None:
    settings = LLMSettings(provider=LLMProvider.GITHUB_MODELS, github_token=None)
    factory = build_llm_factory(settings, fallback_to_mock=False)
    with pytest.raises(LLMConfigError):
        factory("co", "ag")


def test_anthropic_without_key_falls_back_to_mock() -> None:
    settings = LLMSettings(provider=LLMProvider.ANTHROPIC, anthropic_api_key=None)
    factory = build_llm_factory(settings)
    assert isinstance(factory("co", "ag"), MockLLMClient)
