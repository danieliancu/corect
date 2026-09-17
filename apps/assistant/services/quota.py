"""PRODUCT QUOTA: each plan's daily „Vreau să sune natural!” allowance (anonymous 5, Free 20, Pro 200 by default).

One use is one successful naturalisation, whether the text was typed, pasted or spoken: /naturalize/ never knows. The
microphone, transcription and British speech never call this module; their technical guardrails live in limits.py.

Flow for one submission: reserve() before the model call (atomic, so the last free use goes to exactly one request),
commit() once a result is produced, release() on any failure. Days are London calendar days (localday.py).
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.assistant.models import NaturalizeUsage
from apps.core.monitoring import DATABASE, log_event
from apps.core.plans import ANONYMOUS, FREE, PRO
from apps.core.text import romanian_count
from .localday import local_day, seconds_until_reset
from .openai_client import AssistantError

logger = logging.getLogger("apps.assistant")
QUOTA_EXHAUSTED = "quota_exhausted"
RESET = "Limita se resetează la miezul nopții (ora Regatului Unit)."
# A reservation older than this belongs to a request that can no longer be running (the process stopped between reserve
# and commit), so its use is given back.
STALE_AFTER = timedelta(minutes=5)


def daily_limit(tier):
    return settings.NATURALIZE_DAILY_LIMITS[tier]


def uses(count):
    return romanian_count(count, "utilizare", "utilizări")


def exhausted_message(tier):
    if tier == ANONYMOUS:
        return (f"Ai folosit cele {uses(daily_limit(ANONYMOUS))} gratuite de azi. Creează un cont gratuit și primești "
                f"{daily_limit(FREE)} pe zi.")
    if tier == FREE:
        return f"Ai folosit cele {uses(daily_limit(FREE))} de azi. Cu Pro ai cereri nelimitate, în regim Fair Use."
    return f"Ai atins limita Fair Use de {uses(daily_limit(PRO))} pentru astăzi. {RESET}"


@dataclass(frozen=True)
class Reservation:
    usage_id: int


@dataclass(frozen=True)
class QuotaStatus:
    tier: str
    limit: int
    used: int

    @property
    def remaining(self):
        return max(0, self.limit - self.used)

    @property
    def exhausted(self):
        return self.remaining == 0

    @property
    def note(self):
        """„3 din 5 utilizări rămase astăzi”, or "" for Pro, whose allowance is not counted down in the interface."""
        return "" if self.tier == PRO else f"{self.remaining} din {uses(self.limit)} rămase astăzi"


def reserve(actor, tier):
    """Reserves one use for a request about to call the model, or raises AssistantError("quota_exhausted")."""
    now = timezone.now()
    limit = daily_limit(tier)
    with transaction.atomic():
        usage, _ = NaturalizeUsage.objects.get_or_create(actor=actor, day=local_day(now))
        stale = now - timedelta(seconds=float(settings.OPENAI_TIMEOUT)) - STALE_AFTER
        # reserved_at is the newest reservation, so when it is stale every older one is too.
        NaturalizeUsage.objects.filter(pk=usage.pk, reserved__gt=0, reserved_at__lt=stale).update(reserved=0)
        # One conditional UPDATE: the database locks and re-checks the row, so concurrent requests cannot pass the limit.
        if not NaturalizeUsage.objects.filter(pk=usage.pk, used__lt=Value(limit) - F("reserved")).update(
                reserved=F("reserved") + 1, reserved_at=now):
            raise AssistantError(QUOTA_EXHAUSTED, exhausted_message(tier), retry_after=seconds_until_reset(now))
    return Reservation(usage.pk)


def commit(reservation):
    """A result was produced: the reserved use becomes a used one (exactly one per successful naturalisation)."""
    NaturalizeUsage.objects.filter(pk=reservation.usage_id).update(used=F("used") + 1,
                                                                   reserved=Greatest(F("reserved") - 1, 0))


def release(reservation):
    """The request failed: give the reserved use back. Never below zero, never raises."""
    try:
        NaturalizeUsage.objects.filter(pk=reservation.usage_id, reserved__gt=0).update(reserved=F("reserved") - 1)
    except DatabaseError:
        log_event(logger, logging.ERROR, "naturalize_quota_release_unavailable", DATABASE)


def status(actor, tier, now=None):
    """Today's allowance for the actor, without creating a row."""
    used = NaturalizeUsage.objects.filter(actor=actor, day=local_day(now)).values_list("used", flat=True).first()
    return QuotaStatus(tier=tier, limit=daily_limit(tier), used=used or 0)
