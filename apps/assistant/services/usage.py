"""Provider token usage, kept separate from request and response content."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

Result = TypeVar("Result", bound=BaseModel)


@dataclass(frozen=True)
class ProviderUsage:
    """Token counts the provider reported for one call. Never holds submitted text or output."""

    model: str
    response_model: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None = None  # Wall time of the provider call, measured by the server.


@dataclass(frozen=True)
class ParsedResponse(Generic[Result]):
    output: Result
    usage: ProviderUsage | None


_collected: ContextVar[list[ProviderUsage] | None] = ContextVar("provider_usage", default=None)


@contextmanager
def collect_provider_usage():
    """Collects the usage of every provider call made inside the block (a Naturalise request makes exactly one)."""
    calls: list[ProviderUsage] = []
    token = _collected.set(calls)
    try:
        yield calls
    finally:
        _collected.reset(token)


def report_usage(usage: ProviderUsage | None) -> None:
    calls = _collected.get()
    if usage is not None and calls is not None:
        calls.append(usage)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def usage_from_response(response, model: str, duration_ms: int | None = None) -> ProviderUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return ProviderUsage(
        duration_ms=duration_ms,
        model=model,
        response_model=str(getattr(response, "model", "") or "")[:100],
        input_tokens=_count(getattr(usage, "input_tokens", None)),
        cached_input_tokens=_count(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)),
        output_tokens=_count(getattr(usage, "output_tokens", None)),
        reasoning_tokens=_count(getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None)),
        total_tokens=_count(getattr(usage, "total_tokens", None)),
    )
