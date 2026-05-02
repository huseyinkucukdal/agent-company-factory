"""Build an :data:`LLMFactory` for the Company Factory bootstrap.

The bootstrap config takes ``Callable[[company_id, agent_id], LLMClient]``;
this module turns :class:`LLMSettings` into one. Provider-specific clients
are constructed lazily so a missing optional dependency only fails when
that provider is actually selected.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from modules.agent_runtime import LLMClient

from .exceptions import LLMConfigError
from .mock import MockLLMClient
from .settings import LLMProvider, LLMSettings

_log = logging.getLogger(__name__)

LLMFactory = Callable[[str, str], LLMClient]
"""Shape produced by :func:`build_llm_factory`. Matches the alias in
:mod:`modules.factory.bootstrap`; restated here to avoid an import cycle.
"""


def build_llm_factory(
    settings: LLMSettings | None = None,
    *,
    fallback_to_mock: bool = True,
) -> LLMFactory:
    """Return a callable that produces an :class:`LLMClient` per agent.

    A new client instance is created for every ``(company_id, agent_id)``
    pair so per-agent state (pending tool-call futures) does not leak
    between agents.

    If ``fallback_to_mock`` is true (default) and the chosen provider's
    optional dependency is missing or its credentials are absent, the
    factory logs a warning and falls back to :class:`MockLLMClient`. This
    keeps the dev server bootable without any keys; production deploys
    should pass ``fallback_to_mock=False`` and surface the error.
    """
    cfg = settings if settings is not None else LLMSettings.from_env()

    def _factory(company_id: str, agent_id: str) -> LLMClient:
        try:
            return _construct(cfg)
        except LLMConfigError as exc:
            if not fallback_to_mock:
                raise
            _log.warning(
                "llm.config_error company=%s agent=%s provider=%s err=%s "
                "\u2014 falling back to MockLLMClient",
                company_id, agent_id, cfg.provider.value, exc,
            )
            return MockLLMClient()

    return _factory


def _construct(cfg: LLMSettings) -> LLMClient:
    if cfg.provider is LLMProvider.MOCK:
        return MockLLMClient()
    if cfg.provider is LLMProvider.GITHUB_MODELS:
        from .github_models import GitHubModelsClient
        return GitHubModelsClient(cfg)
    if cfg.provider is LLMProvider.ANTHROPIC:
        from .anthropic_client import AnthropicClient
        return AnthropicClient(cfg)
    if cfg.provider is LLMProvider.OPENAI:
        from .openai_client import OpenAIClient
        return OpenAIClient(cfg)
    raise LLMConfigError(f"unknown provider: {cfg.provider!r}")


__all__ = ["build_llm_factory"]
