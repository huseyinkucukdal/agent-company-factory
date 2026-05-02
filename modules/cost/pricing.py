"""Pricing catalogue: tool estimators + LLM token rates.

Tool estimators are plain callables registered in-memory; nothing is
persisted because formulas are code references. LLM rates ship in
``pricing.toml`` and may be overridden at construction time.
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from typing import Any

from .exceptions import InvalidPricingFormula, UnknownTool
from .models import Category, Money, TokenUsage, _to_decimal

CostEstimator = Callable[[dict[str, Any]], Money]


@dataclass(frozen=True)
class LlmRate:
    """Per-1000-token pricing for one model."""

    input_per_1k: Decimal
    output_per_1k: Decimal


@dataclass(frozen=True)
class _ToolEntry:
    estimator: CostEstimator
    category: Category


def load_default_llm_rates() -> dict[str, LlmRate]:
    """Read the bundled ``pricing.toml`` and return its ``[llm.*]`` table."""
    raw = resources.files("modules.cost").joinpath("pricing.toml").read_bytes()
    parsed = tomllib.loads(raw.decode("utf-8"))
    return _parse_llm_rates(parsed)


def _parse_llm_rates(parsed: dict[str, Any]) -> dict[str, LlmRate]:
    out: dict[str, LlmRate] = {}
    llm_section: dict[str, Any] = parsed.get("llm", {})
    for model, body in llm_section.items():
        try:
            out[model] = LlmRate(
                input_per_1k=_to_decimal(body["input_per_1k"]),
                output_per_1k=_to_decimal(body["output_per_1k"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidPricingFormula(
                f"bad llm rate entry {model!r}: {exc}"
            ) from exc
    return out


class Pricing:
    """Tool estimator registry plus LLM-rate lookups."""

    def __init__(
        self,
        *,
        llm_rates: dict[str, LlmRate] | None = None,
    ) -> None:
        self._tools: dict[str, _ToolEntry] = {}
        self._llm_rates: dict[str, LlmRate] = (
            dict(llm_rates) if llm_rates is not None else load_default_llm_rates()
        )

    # ----------------------------------------------------------------- tools

    def register_tool(
        self,
        name: str,
        estimator: CostEstimator,
        category: Category,
    ) -> None:
        if not name:
            raise ValueError("tool name must be non-empty")
        self._tools[name] = _ToolEntry(estimator, category)

    def category_of(self, tool_name: str) -> Category:
        try:
            return self._tools[tool_name].category
        except KeyError as exc:
            raise UnknownTool(tool_name) from exc

    def list_tools(self) -> list[str]:
        return sorted(self._tools)

    def estimate(self, tool_name: str, args: dict[str, Any]) -> Money:
        try:
            entry = self._tools[tool_name]
        except KeyError as exc:
            raise UnknownTool(tool_name) from exc
        result = entry.estimator(args)
        if not isinstance(result, Money):
            raise InvalidPricingFormula(
                f"{tool_name!r} estimator must return Money, got {type(result).__name__}"
            )
        return result

    # ------------------------------------------------------------------- LLM

    def has_llm_rate(self, model: str) -> bool:
        return model in self._llm_rates

    def estimate_llm(
        self, model: str, input_tokens: int, output_tokens_max: int
    ) -> Money:
        if input_tokens < 0 or output_tokens_max < 0:
            raise ValueError("token counts must be non-negative")
        rate = self._rate(model)
        amount = (
            Decimal(input_tokens) * rate.input_per_1k
            + Decimal(output_tokens_max) * rate.output_per_1k
        ) / Decimal(1000)
        return Money.of(amount)

    def actual_llm(self, model: str, usage: TokenUsage) -> Money:
        return self.estimate_llm(
            model,
            input_tokens=usage.input_tokens,
            output_tokens_max=usage.output_tokens,
        )

    def _rate(self, model: str) -> LlmRate:
        try:
            return self._llm_rates[model]
        except KeyError as exc:
            raise UnknownTool(f"llm:{model}") from exc
