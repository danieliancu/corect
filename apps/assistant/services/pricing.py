"""Model-aware cost estimates. Prices come only from settings.OPENAI_PRICING, never from call sites."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

PRICE_KEYS = ("input_per_1m", "cached_input_per_1m", "output_per_1m")
MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class ModelPrice:
    """USD per one million tokens."""

    input_per_1m: Decimal
    cached_input_per_1m: Decimal
    output_per_1m: Decimal


def parse_pricing(raw) -> dict[str, ModelPrice]:
    """Validates {model: {input_per_1m, cached_input_per_1m, output_per_1m}}; raises ValueError when malformed."""
    if not isinstance(raw, dict):
        raise ValueError("expected an object keyed by model name")
    prices = {}
    for model, values in raw.items():
        try:
            amounts = [Decimal(str(values[key])) for key in PRICE_KEYS]
        except (KeyError, TypeError, InvalidOperation):
            raise ValueError(f"{model} needs numeric {', '.join(PRICE_KEYS)}") from None
        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ValueError(f"{model} prices must be non-negative numbers")
        prices[str(model)] = ModelPrice(*amounts)
    return prices


def price_for(*models: str) -> ModelPrice | None:
    for model in models:
        if model and model in settings.OPENAI_PRICING:
            return settings.OPENAI_PRICING[model]
    return None


def estimate_cost(usage) -> Decimal | None:
    """Estimated USD cost of one provider call, or None when tokens or the model's price are unknown."""
    if usage is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    price = price_for(usage.model, usage.response_model)
    if price is None:
        return None
    cached = min(usage.cached_input_tokens or 0, usage.input_tokens)
    cost = ((usage.input_tokens - cached) * price.input_per_1m + cached * price.cached_input_per_1m
            + usage.output_tokens * price.output_per_1m) / MILLION
    return cost.quantize(COST_PLACES)
