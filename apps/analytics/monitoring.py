"""Operational figures from the usage ledgers, for alerts (`manage.py check_production_health`), the staff insights
and the learning AI budget (apps/learning/services/guardrails.py). Aggregates only: no content, no identities."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.assistant.services.localday import local_day
from apps.core.monitoring import GUARDRAIL, PROVIDER, QUOTA, category_for_code
from .models import AudioUsageEvent, LearningUsageEvent, UsageEvent

LEDGERS = {"text": UsageEvent, "audio": AudioUsageEvent, "learning": LearningUsageEvent}
ZERO = Decimal(0)


def day_start(now=None):
    """The start of the current London day, as an aware datetime."""
    return datetime.combine(local_day(now), time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))


@dataclass(frozen=True)
class Spend:
    cost: Decimal  # USD, priced calls only
    by_ledger: dict
    unpriced: int  # Rows that made provider calls but have no cost estimate (missing price row).


def ai_spend(since, until=None, ledgers=None):
    until = until or timezone.now()
    by_ledger, unpriced = {}, 0
    for name, model in LEDGERS.items():
        if ledgers is not None and name not in ledgers:
            continue
        rows = model.objects.filter(created_at__gte=since, created_at__lt=until)
        totals = rows.aggregate(cost=Sum("estimated_cost"),
                                unpriced=Count("id", filter=Q(estimated_cost__isnull=True, provider_calls__gt=0)))
        by_ledger[name] = totals["cost"] or ZERO
        unpriced += totals["unpriced"]
    return Spend(cost=sum(by_ledger.values(), ZERO), by_ledger=by_ledger, unpriced=unpriced)


def learning_spend_today(now=None):
    now = now or timezone.now()
    return ai_spend(day_start(now), now + timedelta(seconds=1), ledgers=("learning",)).cost


@dataclass(frozen=True)
class ErrorRate:
    requests: int
    provider_failures: int

    @property
    def percent(self):
        return 100 * self.provider_failures / self.requests if self.requests else 0.0


def provider_error_rate(since, until=None):
    """Provider failures (timeouts, outages, unusable output) among AI requests. Quota and guardrail refusals are the
    system working, not an outage, so they are excluded from both sides."""
    until = until or timezone.now()
    requests = failures = 0
    for model in LEDGERS.values():
        rows = model.objects.filter(created_at__gte=since, created_at__lt=until)
        for row in rows.values("status", "error_code").annotate(total=Count("id")):
            category = category_for_code(row["error_code"]) if row["error_code"] else None
            if row["status"] == UsageEvent.Status.REJECTED or category in (QUOTA, GUARDRAIL):
                continue
            requests += row["total"]
            if row["status"] == UsageEvent.Status.FAILED and category == PROVIDER:
                failures += row["total"]
    return ErrorRate(requests=requests, provider_failures=failures)
