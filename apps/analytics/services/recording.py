"""Writes one usage ledger row per validated submission. Never stores submitted or generated text."""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError

from apps.analytics.models import UsageEvent
from apps.assistant.services.pricing import estimate_cost
from apps.assistant.services.prompts import PROMPT_VERSION

logger = logging.getLogger("apps.analytics")
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")


def _total(values):
    """Sum only when every provider call reported the value; otherwise the total is unknown."""
    return None if not values or None in values else sum(values)


def record_usage_event(*, request, kind, status, calls=(), error_code="", visitor=None, assistant_request=None,
                       auto_translated=False):
    user = request.user if request.user.is_authenticated else None
    if status == UsageEvent.Status.REJECTED:
        # Rejected before any provider call, so nothing was consumed.
        tokens, cost = dict.fromkeys(TOKEN_FIELDS, 0), Decimal(0)
    else:
        tokens = {field: _total([getattr(call, field) for call in calls]) for field in TOKEN_FIELDS}
        costs = [estimate_cost(call) for call in calls]
        cost = None if not costs or None in costs else sum(costs, Decimal(0))
    try:
        return UsageEvent.objects.create(
            audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS,
            user=user, visitor=visitor, request_type=kind, auto_translated=auto_translated,
            model=settings.OPENAI_MODEL[:100], response_model=next((c.response_model for c in calls if c.response_model), ""),
            prompt_version=PROMPT_VERSION, status=status, error_code=error_code, provider_calls=len(calls),
            estimated_cost=cost, assistant_request=assistant_request, **tokens)
    except DatabaseError:
        logger.error("usage_event_unavailable")
        return None
