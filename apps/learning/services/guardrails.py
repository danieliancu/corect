"""Cost guardrails for learning AI (exercise generation, open-answer checks), checked before every provider call.

Per account: LEARNING_AI_RATE_LIMIT_MINUTE calls a minute and LEARNING_AI_DAY_LIMITS calls a London day by plan.
For the whole service: LEARNING_AI_GLOBAL_DAY_CALLS calls a day (an exact, atomic counter) and
LEARNING_AI_GLOBAL_DAY_COST_USD of recorded learning spend a day (read from the usage ledger; calls already running when
the cap is reached can still finish, so the spend can pass it by at most those calls).

Counters use the same database buckets as the other guardrails (apps/assistant/services/limits.py); every window is
counted in one transaction, so a refusal in a later window undoes the earlier ones. A call that later fails still
counts: a failing provider in a loop must not be able to retry for free.
"""
from django.conf import settings
from django.db import transaction

from apps.analytics.monitoring import learning_spend_today
from apps.assistant.services.limits import DAY, MINUTE, consume_quota
from apps.assistant.services.openai_client import AssistantError
from apps.core.plans import tier_for

RATE_LIMIT = "learning_rate_limit"
QUOTA_EXHAUSTED = "learning_quota_exhausted"
BUDGET_EXHAUSTED = "learning_budget_exhausted"
LIMIT_CODES = frozenset({RATE_LIMIT, QUOTA_EXHAUSTED, BUDGET_EXHAUSTED})
GLOBAL_KEY = "learn-ai:global"


class LearningLimitReached(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _count(key, window, limit, code):
    try:
        consume_quota(key, [(window, limit)], code, "")
    except AssistantError:
        raise LearningLimitReached(code) from None


@transaction.atomic
def reserve_learning_call(user, now=None):
    """Counts one learning AI call, or raises LearningLimitReached with the first limit that is full."""
    if learning_spend_today(now) >= settings.LEARNING_AI_GLOBAL_DAY_COST_USD:
        raise LearningLimitReached(BUDGET_EXHAUSTED)
    key = f"learn-ai:user:{user.pk}"
    _count(key, MINUTE, settings.LEARNING_AI_RATE_LIMIT_MINUTE, RATE_LIMIT)
    _count(key, DAY, settings.LEARNING_AI_DAY_LIMITS[tier_for(user)], QUOTA_EXHAUSTED)
    _count(GLOBAL_KEY, DAY, settings.LEARNING_AI_GLOBAL_DAY_CALLS, BUDGET_EXHAUSTED)
