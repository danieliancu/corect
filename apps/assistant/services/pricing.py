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


@dataclass(frozen=True)
class AudioPrice:
    """USD. per_minute bills audio duration; the token prices bill text input and audio output tokens."""

    per_minute: Decimal | None = None
    input_per_1m: Decimal | None = None
    output_per_1m: Decimal | None = None


AUDIO_PRICE_KEYS = ("per_minute", "input_per_1m", "output_per_1m")


def parse_audio_pricing(raw) -> dict[str, AudioPrice]:
    """Validates {model: {per_minute and/or input_per_1m, output_per_1m}}; raises ValueError when malformed."""
    if not isinstance(raw, dict):
        raise ValueError("expected an object keyed by model name")
    prices = {}
    for model, values in raw.items():
        if not isinstance(values, dict) or not any(key in values for key in AUDIO_PRICE_KEYS):
            raise ValueError(f"{model} needs at least one of {', '.join(AUDIO_PRICE_KEYS)}")
        amounts = {}
        for key in AUDIO_PRICE_KEYS:
            if key not in values:
                continue
            try:
                amount = Decimal(str(values[key]))
            except InvalidOperation:
                raise ValueError(f"{model} {key} must be a number") from None
            if not amount.is_finite() or amount < 0:
                raise ValueError(f"{model} prices must be non-negative numbers")
            amounts[key] = amount
        prices[str(model)] = AudioPrice(**amounts)
    return prices


def _audio_price(usage):
    return settings.OPENAI_AUDIO_PRICING.get(usage.model) if usage is not None and usage.model else None


def _audio_token_cost(usage, price):
    if None in (usage.input_tokens, usage.output_tokens, price.input_per_1m, price.output_per_1m):
        return None
    return ((usage.input_tokens * price.input_per_1m + usage.output_tokens * price.output_per_1m) / MILLION
            ).quantize(COST_PLACES)


def estimate_transcription_cost(usage) -> Decimal | None:
    """Duration-billed: seconds ÷ 60 × per-minute price. Token-billed when the provider reports tokens and prices exist."""
    price = _audio_price(usage)
    if price is None:
        return None
    if usage.audio_seconds is not None and price.per_minute is not None:
        return (usage.audio_seconds / 60 * price.per_minute).quantize(COST_PLACES)
    return _audio_token_cost(usage, price)


def estimate_speech_cost(usage) -> Decimal | None:
    """(text input tokens × input price + audio output tokens × output price) ÷ 1M, from provider-reported usage."""
    price = _audio_price(usage)
    return None if price is None else _audio_token_cost(usage, price)


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
