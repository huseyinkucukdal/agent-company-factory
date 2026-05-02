"""Pricing catalogue + LLM rate tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from modules.cost import (
    Category,
    LlmRate,
    Money,
    Pricing,
    TokenUsage,
    UnknownTool,
    load_default_llm_rates,
)
from modules.cost.exceptions import InvalidPricingFormula


def test_estimate_llm_input_output(pricing: Pricing) -> None:
    # 1000 in tokens at $0.003/1k + 500 out at $0.015/1k
    cost = pricing.estimate_llm("test-model", 1000, 500)
    expected = Decimal("0.003") + (Decimal("0.015") * Decimal("0.5"))
    assert cost.amount_usd == Money.of(expected).amount_usd


def test_actual_llm_uses_real_token_count(pricing: Pricing) -> None:
    actual = pricing.actual_llm("test-model", TokenUsage(2000, 250))
    expected = (Decimal("0.003") * 2) + (Decimal("0.015") * Decimal("0.25"))
    assert actual.amount_usd == Money.of(expected).amount_usd


def test_estimate_llm_unknown_model_raises(pricing: Pricing) -> None:
    with pytest.raises(UnknownTool):
        pricing.estimate_llm("nope", 1, 1)


def test_estimate_tool_via_registered_estimator(pricing: Pricing) -> None:
    def email_estimator(args: dict[str, object]) -> Money:
        recipients = args.get("to", [])
        n = len(recipients) if isinstance(recipients, list) else 0
        return Money.of(Decimal("0.001") * n)

    pricing.register_tool("email.send", email_estimator, Category.EMAIL)
    cost = pricing.estimate("email.send", {"to": ["a", "b", "c"]})
    assert cost == Money.of("0.003")


def test_unknown_tool_raises(pricing: Pricing) -> None:
    with pytest.raises(UnknownTool):
        pricing.estimate("ghost", {})


def test_estimator_must_return_money(pricing: Pricing) -> None:
    pricing.register_tool(
        "broken",
        lambda _args: 1.5,  # type: ignore[arg-type,return-value]
        Category.OTHER,
    )
    with pytest.raises(InvalidPricingFormula):
        pricing.estimate("broken", {})


def test_negative_token_counts_rejected(pricing: Pricing) -> None:
    with pytest.raises(ValueError):
        pricing.estimate_llm("test-model", -1, 0)


def test_default_pricing_toml_loads() -> None:
    rates = load_default_llm_rates()
    # Smoke: at least one model present and rate is Decimal.
    assert rates
    for _model, rate in rates.items():
        assert isinstance(rate, LlmRate)
        assert isinstance(rate.input_per_1k, Decimal)
        assert isinstance(rate.output_per_1k, Decimal)
        assert rate.input_per_1k > 0
        assert rate.output_per_1k > 0
