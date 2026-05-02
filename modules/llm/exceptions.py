"""Domain exceptions raised by LLM providers."""
from __future__ import annotations


class LLMError(Exception):
    """Base class for everything in :mod:`modules.llm`."""


class LLMConfigError(LLMError):
    """Configuration is missing or invalid (e.g. no API key)."""


class LLMProviderUnavailable(LLMError):  # noqa: N818 - public API name
    """The optional dependency for the chosen provider is not installed."""


class LLMProviderError(LLMError):
    """The provider returned an unexpected response (parse / shape error)."""


__all__ = [
    "LLMConfigError",
    "LLMError",
    "LLMProviderError",
    "LLMProviderUnavailable",
]
