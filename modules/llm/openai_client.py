"""OpenAI Chat Completions provider.

Reuses :class:`modules.llm.github_models.GitHubModelsClient` (the OpenAI
SDK is OpenAI-compatible by definition); only the constructor differs —
auth comes from ``OPENAI_API_KEY`` and the base URL defaults to
``https://api.openai.com/v1``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .exceptions import LLMConfigError
from .github_models import GitHubModelsClient, _import_openai
from .settings import LLMSettings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import AsyncOpenAI  # type: ignore[import-not-found,unused-ignore]


class OpenAIClient(GitHubModelsClient):
    """OpenAI provider — same tool-use bridge, different credentials."""

    def __init__(self, settings: LLMSettings) -> None:
        if not settings.openai_api_key:
            raise LLMConfigError(
                "OPENAI_API_KEY is required for the openai provider"
            )
        # Skip the parent's GitHub-token validation by setting up directly.
        self._settings = settings
        openai_pkg: Any = _import_openai()
        self._client: AsyncOpenAI = openai_pkg.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.timeout_seconds,
        )
        self._pending = {}
        self._on_request = None  # set later by the factory if a counter is wired

    def _provider_name(self) -> str:
        return "openai"

    def _backoff_delays(self) -> list[float]:
        # Paid OpenAI tier rarely 429s; shorter retries are fine.
        return [1.0, 4.0, 12.0]


__all__ = ["OpenAIClient"]
