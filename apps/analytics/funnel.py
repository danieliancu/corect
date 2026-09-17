"""Business funnel: are people coming, using Corect, coming back and interested in paying?

Taxonomy (stable names; each step is counted once per identity — an account, or an anonymous visitor with analytics
on — per day):

    visitor           FunnelEvent site_visit          (browser beacon, analytics consent only)
    uses Corect       UsageEvent status=success       (existing text ledger)
    returns           site_visit on 2+ different days in the window
    starts signup     FunnelEvent signup_viewed
    creates account   User.date_joined                (existing)
    hits Free limit   UsageEvent quota_exhausted, plan=free / anonymous (existing)
    sees plans        FunnelEvent pricing_viewed
    clicks Pro        FunnelEvent pro_cta_clicked
    starts checkout   FunnelEvent checkout_started      (for billing; not emitted yet)
    pays              FunnelEvent subscription_started  (for billing; not emitted yet)

Identities are counted separately as accounts and anonymous visitors: a browser that signs up is one visitor before and
one account after. Visitors without analytics consent are not counted, so rates describe consenting visitors.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from apps.assistant.services.quota import QUOTA_EXHAUSTED
from .filters import day_start
from .formatting import rate
from .models import FunnelEvent, UsageEvent

Name = FunnelEvent.Name
WINDOWS = (("today", "Today", 0), ("last_7", "Last 7 days", 6), ("last_30", "Last 30 days", 29))
STEPS = (Name.SITE_VISIT, Name.SIGNUP_VIEWED, Name.PRICING_VIEWED, Name.PRO_CTA_CLICKED, Name.CHECKOUT_STARTED,
         Name.SUBSCRIPTION_STARTED)


def _identities(name):
    """Distinct accounts and distinct anonymous visitors for one step, as two aggregate expressions."""
    return {f"{name}_users": Count("user", filter=Q(name=name), distinct=True),
            f"{name}_visitors": Count("visitor", filter=Q(name=name), distinct=True)}


def _returning(events):
    visits = events.filter(name=Name.SITE_VISIT)
    users = visits.filter(user__isnull=False).values("user").annotate(days=Count("day", distinct=True))
    visitors = visits.filter(visitor__isnull=False).values("visitor").annotate(days=Count("day", distinct=True))
    return users.filter(days__gte=2).count() + visitors.filter(days__gte=2).count()


def window_funnel(start):
    events = FunnelEvent.objects.filter(created_at__gte=start)
    figures = events.aggregate(**{key: value for step in STEPS for key, value in _identities(step).items()})
    steps = {step: figures[f"{step}_users"] + figures[f"{step}_visitors"] for step in STEPS}
    visits = events.filter(name=Name.SITE_VISIT)
    successes = UsageEvent.objects.filter(created_at__gte=start, status=UsageEvent.Status.SUCCESS)
    used = (visits.filter(user__in=successes.filter(user__isnull=False).values("user")).values("user").distinct().count()
            + visits.filter(visitor__in=successes.filter(visitor__isnull=False).values("visitor"))
            .values("visitor").distinct().count())
    quota_hit = Q(status=UsageEvent.Status.REJECTED, error_code=QUOTA_EXHAUSTED)
    plans = UsageEvent.objects.filter(created_at__gte=start).aggregate(
        free_active=Count("user", filter=Q(plan="free"), distinct=True),
        free_hit=Count("user", filter=Q(plan="free") & quota_hit, distinct=True),
        anonymous_active=Count("visitor", filter=Q(plan="anonymous"), distinct=True),
        anonymous_hit=Count("visitor", filter=Q(plan="anonymous") & quota_hit, distinct=True))
    visitors, returning = steps[Name.SITE_VISIT], _returning(events)
    signups = get_user_model().objects.filter(date_joined__gte=start).count()
    return {
        "visitors": visitors,
        "anonymous_visitors": figures[f"{Name.SITE_VISIT}_visitors"],
        "registered_visitors": figures[f"{Name.SITE_VISIT}_users"],
        "used": used, "use_rate": rate(used, visitors),
        "returning": returning, "return_rate": rate(returning, visitors),
        "signup_viewed": steps[Name.SIGNUP_VIEWED],
        "signups": signups, "signup_rate": rate(signups, figures[f"{Name.SITE_VISIT}_visitors"]),
        "free_hit_rate": rate(plans["free_hit"], plans["free_active"]), "free_hit": plans["free_hit"],
        "anonymous_hit_rate": rate(plans["anonymous_hit"], plans["anonymous_active"]),
        "pricing_viewed": steps[Name.PRICING_VIEWED], "pricing_rate": rate(steps[Name.PRICING_VIEWED], visitors),
        "pro_clicks": steps[Name.PRO_CTA_CLICKED], "pro_click_rate": rate(steps[Name.PRO_CTA_CLICKED], visitors),
        "pro_click_after_pricing": rate(steps[Name.PRO_CTA_CLICKED], steps[Name.PRICING_VIEWED]),
        "checkouts": steps[Name.CHECKOUT_STARTED], "subscriptions": steps[Name.SUBSCRIPTION_STARTED],
        "checkout_rate": rate(steps[Name.SUBSCRIPTION_STARTED], steps[Name.CHECKOUT_STARTED]),
    }


def funnel_summary():
    """The funnel for today, the last 7 days and the last 30 days (London days), plus whether billing data exists."""
    windows = [{"key": key, "label": label, **window_funnel(day_start(days))} for key, label, days in WINDOWS]
    billing_live = any(window["checkouts"] or window["subscriptions"] for window in windows)
    return {"funnel_windows": windows, "billing_live": billing_live}
