"""Records business funnel steps (apps/analytics/funnel.py), only with analytics consent.

Browser events (site visit, signup page, plans seen, Pro clicks) arrive through `funnel_event`; billing will call
`record_funnel_event` directly for checkout and subscription. A step is stored once per identity, placement and London
day (the unique constraints make repeats and concurrent requests harmless).
"""
import logging

from django.db import DatabaseError
from django.utils import timezone

from apps.analytics.models import FunnelEvent, UsageEvent
from apps.assistant.services.localday import local_day
from apps.core.consent import analytics_allowed
from apps.core.monitoring import DATABASE, log_event
from apps.core.plans import tier_for
from .visitors import existing_visitor, get_or_create_visitor

logger = logging.getLogger("apps.analytics")
Name = FunnelEvent.Name
# Steps a browser may report, with the placements each accepts ("" = none). Anything else is ignored.
CLIENT_EVENTS = {
    Name.SITE_VISIT: {""},
    Name.SIGNUP_VIEWED: {""},
    Name.PRICING_VIEWED: {"home", "about"},
    # "pricing_section" is for the plan card's Pro button once payments exist (it is disabled today).
    Name.PRO_CTA_CLICKED: {"quota_box", "pricing_section"},
}
BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "headless", "monitor", "curl", "wget", "python-")


def looks_automated(request) -> bool:
    agent = request.headers.get("User-Agent", "").lower()
    return not agent or any(marker in agent for marker in BOT_MARKERS)


def record_funnel_event(request, name, placement="", *, visitor=None, create_visitor=False):
    """Stores one funnel step for this request's account or anonymous visitor. Returns the visitor used (so the caller
    can set its cookie) or None. Never raises for database trouble."""
    if not analytics_allowed(request):
        return None
    user = request.user if request.user.is_authenticated else None
    try:
        if user is None and visitor is None:
            visitor = get_or_create_visitor(request) if create_visitor else existing_visitor(request)
        if user is None and visitor is None:
            return None
        now = timezone.now()
        FunnelEvent.objects.bulk_create([FunnelEvent(
            name=name, placement=placement, user=user, visitor=None if user else visitor, day=local_day(now),
            created_at=now, plan=tier_for(request.user),
            audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS)],
            ignore_conflicts=True)
    except DatabaseError:
        log_event(logger, logging.ERROR, "funnel_event_unavailable", DATABASE)
        return None
    return None if user else visitor
