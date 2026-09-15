"""The London calendar day that daily plan quotas and daily guardrails use (TIME_ZONE, Europe/London).

A day starts at local midnight, so it is 23 hours long when the clocks go forward and 25 hours when they go back; a
fixed 86,400-second bucket would reset at UTC midnight, an hour early during British Summer Time.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


def local_day(now=None):
    return timezone.localdate(now or timezone.now())


def next_reset(now=None):
    """The next local midnight, as an aware datetime."""
    return datetime.combine(local_day(now) + timedelta(days=1), time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))


def seconds_until_reset(now=None):
    now = now or timezone.now()
    return max(1, int((next_reset(now) - now).total_seconds()))
