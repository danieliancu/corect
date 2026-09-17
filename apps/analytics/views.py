"""Staff-only usage analytics. Every figure is a database aggregate over the text, learning and audio usage ledgers."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from functools import reduce
from operator import add

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import (Case, Count, DecimalField, F, Max, Min, OuterRef, Q, Subquery, Sum, Value, When)
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.assistant.services.quota import QUOTA_EXHAUSTED
from apps.learning.services.guardrails import BUDGET_EXHAUSTED as LEARNING_BUDGET_EXHAUSTED
from apps.learning.services.guardrails import LIMIT_CODES as LEARNING_LIMIT_CODES
from .filters import AUDIENCES, PERIODS, TYPES, ReportFilters, day_start
from .formatting import rate
from .identifiers import visitor_lookup
from .insights import dashboard_insights
from .monitoring import ai_spend, day_start as monitoring_day_start
from .models import AnonymousVisitor, AudioUsageEvent, LearningUsageEvent, UsageEvent

User = get_user_model()
PAGE_SIZE = 50
MONEY = DecimalField(max_digits=16, decimal_places=8)
ZERO = Value(Decimal(0), output_field=MONEY)
USER_SORTS = {"requests": "requests", "tokens": "tokens_total", "cost": "cost", "learning_cost": "learning_cost",
              "audio_cost": "audio_cost", "total_cost": "total_cost", "transcriptions": "transcriptions",
              "realtime": "realtime_sessions", "speech_plays": "speech_plays", "last_active": "last_active",
              "joined": "date_joined", "username": "username"}
VISITOR_SORTS = {"requests": "requests", "tokens": "tokens_total", "cost": "cost", "audio_cost": "audio_cost",
                 "total_cost": "total_cost", "transcriptions": "transcriptions", "realtime": "realtime_sessions",
                 "speech_plays": "speech_plays", "last_seen": "last_seen_at", "first_seen": "first_seen_at",
                 "active_days": "active_days"}


def usage_aggregates(prefix="", scope=None):
    """Conditional aggregates over text usage events, directly (prefix "") or through a relation ("usage_events__").

    Aliases never reuse UsageEvent field names: Django would resolve a later filter on that name to the aggregate.
    """
    scope = scope if scope is not None else Q()

    def where(**conditions):
        return (scope & Q(**{prefix + key: value for key, value in conditions.items()})) or None

    def count(**conditions):
        return Count(f"{prefix}id", filter=where(**conditions))

    def total(field):
        return Sum(f"{prefix}{field}", filter=where())

    return {
        "requests": count(), "corrections": count(request_type="correction"),
        "translations": count(request_type="translation"), "unclassified": count(request_type="unclassified"),
        "successes": count(status="success"),
        "failures": count(status="failed"), "rejections": count(status="rejected"),
        "polite_requests": count(polite=True),
        "tokens_in": total("input_tokens"), "tokens_cached": total("cached_input_tokens"),
        "tokens_out": total("output_tokens"), "tokens_total": total("total_tokens"), "cost": total("estimated_cost"),
        "without_tokens": count(total_tokens__isnull=True), "without_cost": count(estimated_cost__isnull=True),
    }


def audio_aggregates(scope=None):
    """Conditional aggregates over audio usage events, split by operation (speech to text, text to speech)."""
    scope = scope if scope is not None else Q()

    def where(**conditions):
        return (scope & Q(**conditions)) or None

    def count(**conditions):
        return Count("id", filter=where(**conditions))

    def total(field, **conditions):
        return Sum(field, filter=where(**conditions))

    stt, tts = {"operation": "transcription"}, {"operation": "speech"}
    live, file = {**stt, "stt_mode": "realtime"}, {**stt, "stt_mode": "file"}
    return {
        "audio_calls": count(), "transcriptions": count(**stt), "speech_plays": count(**tts),
        "stt_realtime": count(**live), "stt_realtime_seconds": total("audio_seconds", **live),
        "stt_realtime_cost": total("estimated_cost", **live),
        "stt_file": count(**file), "stt_file_seconds": total("audio_seconds", **file),
        "stt_file_cost": total("estimated_cost", **file),
        "stt_provider_metered": count(metering_source="provider", **stt),
        "stt_window_metered": count(metering_source="stream_duration", **stt),
        "speech_correction": count(speech_target="correction", **tts), "speech_native": count(speech_target="native", **tts),
        "speech_translation": count(speech_target="translation", **tts),
        "audio_failures": count(status="failed"), "audio_rejections": count(status="rejected"),
        "stt_seconds": total("audio_seconds", **stt), "stt_cost": total("estimated_cost", **stt),
        "tts_tokens_in": total("input_tokens", **tts), "tts_tokens_out": total("output_tokens", **tts),
        "tts_cost": total("estimated_cost", **tts), "audio_cost": total("estimated_cost"),
        "audio_without_cost": count(estimated_cost__isnull=True),
    }


def learning_aggregates():
    """Aggregates over learning AI calls, in total and per learning feature."""
    figures = {
        "learning_calls": Count("id"), "learning_failures": Count("id", filter=Q(status="failed")),
        "learning_rejections": Count("id", filter=Q(status="rejected")), "learning_tokens_in": Sum("input_tokens"),
        "learning_tokens_out": Sum("output_tokens"), "learning_tokens_total": Sum("total_tokens"),
        "learning_cost": Sum("estimated_cost"), "learning_without_cost": Count("id", filter=Q(estimated_cost__isnull=True)),
        "learning_users": Count("user", distinct=True),
        # Calls the learning AI guardrails prevented (per-account quota, rate limit, service-wide budget).
        "learning_limit_hits": Count("id", filter=Q(status="rejected", error_code__in=LEARNING_LIMIT_CODES)),
        "learning_budget_hits": Count("id", filter=Q(status="rejected", error_code=LEARNING_BUDGET_EXHAUSTED)),
    }
    for feature in LearningUsageEvent.Feature.values:
        figures[f"{feature}_calls"] = Count("id", filter=Q(feature=feature))
        figures[f"{feature}_tokens"] = Sum("total_tokens", filter=Q(feature=feature))
        figures[f"{feature}_cost"] = Sum("estimated_cost", filter=Q(feature=feature))
    return figures


def learning_breakdown(learning):
    totals = learning.aggregate(**learning_aggregates())
    cost, learners = totals["learning_cost"], totals["learning_users"]
    return {
        "learning_totals": totals,
        "learning_features": [{"key": key, "label": label, "calls": totals[f"{key}_calls"],
                               "tokens": totals[f"{key}_tokens"], "cost": totals[f"{key}_cost"]}
                              for key, label in LearningUsageEvent.Feature.choices],
        "learning_cost_per_learner": cost / learners if cost is not None and learners else None,
        "learning_errors": learning.exclude(status="success").values("feature", "status", "error_code")
        .annotate(total=Count("id")).order_by("-total", "error_code"),
    }


def known_sum(*values):
    """The sum of the known values, or None when none is known."""
    known = [value for value in values if value is not None]
    return sum(known, Decimal(0)) if known else None


def cost_breakdown(text_cost, stt_cost, tts_cost, realtime_cost=None, file_cost=None, learning_cost=None):
    """Grand total = text AI + learning AI + speech to text + text to speech, with each source's share of it.

    Speech to text is live (realtime) plus finished-recording (file) transcription. Each ledger is counted once.
    """
    audio = known_sum(stt_cost, tts_cost)
    total = known_sum(text_cost, learning_cost, audio)

    def share(value):
        return rate(value, total) if value is not None and total else None
    return {"total": total, "text": text_cost, "learning": learning_cost, "audio": audio, "stt": stt_cost, "tts": tts_cost,
            "stt_realtime": realtime_cost, "stt_file": file_cost,
            "text_share": share(text_cost), "learning_share": share(learning_cost), "audio_share": share(audio),
            "stt_share": share(stt_cost), "tts_share": share(tts_cost),
            "realtime_audio_share": rate(realtime_cost, audio) if realtime_cost is not None and audio else None}


def costs_with_audio(text_cost, audio_totals, learning_cost=None):
    return cost_breakdown(text_cost, audio_totals["stt_cost"], audio_totals["tts_cost"],
                          audio_totals["stt_realtime_cost"], audio_totals["stt_file_cost"], learning_cost)


def optional_total(*fields):
    """Database sum of annotated money fields, NULL only when every part is unknown, so rows can sort by it."""
    return Case(When(Q(**{f"{field}__isnull": True for field in fields}), then=Value(None, output_field=MONEY)),
                default=reduce(add, (Coalesce(F(field), ZERO) for field in fields)), output_field=MONEY)


def audio_columns(owner_field, scope):
    """Per-row audio figures as correlated subqueries, so they never multiply the row's text aggregates."""
    rows = AudioUsageEvent.objects.filter(scope, **{owner_field: OuterRef("pk")}).order_by().values(owner_field)

    def subquery(expression, **conditions):
        return Subquery(rows.filter(**conditions).annotate(value=expression).values("value")[:1])
    return {
        "transcriptions": Coalesce(subquery(Count("id"), operation="transcription"), 0),
        "realtime_sessions": Coalesce(subquery(Count("id"), operation="transcription", stt_mode="realtime"), 0),
        "realtime_seconds": subquery(Sum("audio_seconds"), operation="transcription", stt_mode="realtime"),
        "realtime_stt_cost": subquery(Sum("estimated_cost"), operation="transcription", stt_mode="realtime"),
        "file_stt_cost": subquery(Sum("estimated_cost"), operation="transcription", stt_mode="file"),
        "speech_plays": Coalesce(subquery(Count("id"), operation="speech"), 0),
        "stt_cost": subquery(Sum("estimated_cost"), operation="transcription"),
        "tts_cost": subquery(Sum("estimated_cost"), operation="speech"),
    }


def learning_columns(owner_field, scope):
    """Per-row learning AI figures as correlated subqueries (never multiplied by the row's other aggregates)."""
    rows = LearningUsageEvent.objects.filter(scope, **{owner_field: OuterRef("pk")}).order_by().values(owner_field)
    return {"learning_calls": Coalesce(Subquery(rows.annotate(value=Count("id")).values("value")[:1]), 0),
            "learning_cost": Subquery(rows.annotate(value=Sum("estimated_cost")).values("value")[:1])}


def with_costs(queryset):
    return queryset.annotate(audio_cost=optional_total("stt_cost", "tts_cost")) \
        .annotate(total_cost=optional_total("cost", "learning_cost", "stt_cost", "tts_cost"))


def windows():
    return {"today": Count("id", filter=Q(created_at__gte=day_start(0))),
            "last_7": Count("id", filter=Q(created_at__gte=day_start(6))),
            "last_30": Count("id", filter=Q(created_at__gte=day_start(29)))}


def as_float(value):
    """Money for the chart: json_script would write a Decimal as a string, which Chart.js cannot plot."""
    return float(value) if value is not None else None


def activity_series(events, start):
    """Requests, tokens and text AI cost per local day from `start` (zero-filled), or per month when there is no start."""
    figures = {"requests": Count("id"), "tokens": Sum("total_tokens"), "cost": Sum("estimated_cost")}
    if start is None:
        rows = (events.annotate(bucket=TruncMonth("created_at")).values("bucket").annotate(**figures).order_by("bucket"))
        return [{"label": row["bucket"].strftime("%b %Y"), "requests": row["requests"], "tokens": row["tokens"],
                 "cost": as_float(row["cost"])} for row in rows]
    rows = {row["bucket"]: row for row in events.filter(created_at__gte=start).annotate(bucket=TruncDate("created_at"))
            .values("bucket").annotate(**figures)}
    first = timezone.localdate(start)
    series = []
    for offset in range((timezone.localdate() - first).days + 1):
        day = first + timedelta(days=offset)
        row = rows.get(day, {})
        series.append({"label": day.strftime("%d %b"), "requests": row.get("requests", 0), "tokens": row.get("tokens"),
                       "cost": as_float(row.get("cost"))})
    return series


def breakdowns(events):
    return {
        "errors": events.exclude(status="success").values("status", "error_code")
        .annotate(total=Count("id")).order_by("-total", "error_code"),
        "by_model": events.values("model").annotate(requests=Count("id"), tokens=Sum("total_tokens"),
                                                    cost=Sum("estimated_cost")).order_by("-requests", "model"),
    }


PLAN_LABELS = {"anonymous": "Anonymous", "free": "Free", "pro": "Pro"}


def plan_summary(events):
    """Text usage per plan tier at request time: successful naturalisations, quota rejections, and how many identities used
    up their daily quota. Free and Pro count accounts; anonymous use counts visitors with analytics on (the anonymous
    quota itself is kept per network address, so visitors without the analytics cookie are not included)."""
    hit = Q(status=UsageEvent.Status.REJECTED, error_code=QUOTA_EXHAUSTED)
    rows = {row["plan"]: row for row in events.exclude(plan="").values("plan").annotate(
        successes=Count("id", filter=Q(status=UsageEvent.Status.SUCCESS)), quota_rejections=Count("id", filter=hit),
        users_active=Count("user", distinct=True), users_hit=Count("user", filter=hit, distinct=True),
        visitors_active=Count("visitor", distinct=True), visitors_hit=Count("visitor", filter=hit, distinct=True))}
    summary = []
    for plan, label in PLAN_LABELS.items():
        row = rows.get(plan, {})
        identity = "visitors" if plan == "anonymous" else "users"
        active, hitting = row.get(f"{identity}_active", 0), row.get(f"{identity}_hit", 0)
        summary.append({"plan": plan, "label": label, "successes": row.get("successes", 0),
                        "quota_rejections": row.get("quota_rejections", 0), "active": active, "hit": hitting,
                        "hit_rate": rate(hitting, active)})
    # Anonymous visitors who used up their quota and signed up or signed in afterwards.
    converted = (events.filter(hit, plan="anonymous", visitor__converted_at__gte=F("created_at"))
                 .values("visitor").distinct().count())
    return {"plan_rows": summary, "plan_conversions": converted}


def audio_breakdowns(audio):
    totals = audio.aggregate(**audio_aggregates())
    return {
        "audio_totals": totals,
        "audio_errors": audio.exclude(status="success").values("operation", "status", "error_code")
        .annotate(total=Count("id")).order_by("-total", "error_code"),
        "audio_by_model": audio.values("operation", "model", "voice").annotate(
            calls=Count("id"), seconds=Sum("audio_seconds"), tokens_in=Sum("input_tokens"),
            tokens_out=Sum("output_tokens"), spend=Sum("estimated_cost")).order_by("operation", "-calls", "model"),
    }


def model_choices():
    text = UsageEvent.objects.exclude(model="").values_list("model", flat=True).distinct()
    audio = AudioUsageEvent.objects.exclude(model="").values_list("model", flat=True).distinct()
    learning = LearningUsageEvent.objects.exclude(model="").values_list("model", flat=True).distinct()
    return sorted(set(text) | set(audio) | set(learning))


def ordered(queryset, field, descending=True):
    return queryset.order_by(F(field).desc(nulls_last=True) if descending else F(field).asc(), "pk")


def render_report(request, template, title, section, context):
    ai_models = {"text": settings.OPENAI_MODEL, "stt": settings.OPENAI_TRANSCRIBE_MODEL,
                 "live_stt": settings.OPENAI_LIVE_TRANSCRIBE_MODEL, "learning": settings.OPENAI_LEARNING_MODEL,
                 "tts": settings.OPENAI_TTS_MODEL, "voice": settings.OPENAI_TTS_VOICE}
    return render(request, template, {**admin.site.each_context(request), "title": title, "section": section,
                                      "ai_models": ai_models, "gbp_per_usd": settings.ANALYTICS_GBP_PER_USD, **context})


def filter_context(filters, models, **extra):
    return {"filters": filters, "periods": PERIODS, "audiences": AUDIENCES, "types": TYPES, "models": models,
            "base_query": filters.querystring(**extra), **extra}


@staff_member_required
def dashboard(request):
    models = model_choices()
    filters = ReportFilters.from_request(request, models)
    start = filters.start
    events = UsageEvent.objects.filter(filters.events_q())
    totals = events.aggregate(**usage_aggregates())
    audio = audio_breakdowns(AudioUsageEvent.objects.filter(filters.audio_q()))
    # Learning AI follows period, audience and model; the request type applies to text figures only.
    learning = learning_breakdown(LearningUsageEvent.objects.filter(filters.audio_q()))
    recent = UsageEvent.objects.filter(filters.events_q(include_period=False))
    users = User.objects.aggregate(total=Count("id"),
                                   new=Count("id", filter=Q(date_joined__gte=start) if start else None))
    cohort = AnonymousVisitor.objects.filter(first_seen_at__gte=start) if start else AnonymousVisitor.objects.all()
    visitors = cohort.aggregate(total=Count("id"), signed_up=Count("id", filter=Q(converted_via="signup")),
                                signed_in=Count("id", filter=Q(converted_via="login")))
    visitors["all_time"] = AnonymousVisitor.objects.count()
    visitors["active"] = (events.filter(audience="anonymous", visitor__isnull=False)
                          .values("visitor").distinct().count())
    registered, anonymous = replace(filters, audience="registered"), replace(filters, audience="anonymous")
    top_users = [] if filters.audience == "anonymous" else ordered(with_costs(
        User.objects.annotate(**usage_aggregates("usage_events__", registered.events_q("usage_events__")),
                              **audio_columns("user", registered.audio_q()),
                              **learning_columns("user", registered.audio_q()))).filter(requests__gt=0), "requests")[:10]
    top_visitors = [] if filters.audience == "registered" else ordered(with_costs(
        AnonymousVisitor.objects.select_related("converted_user").annotate(
            **usage_aggregates("usage_events__", anonymous.events_q("usage_events__")),
            **audio_columns("visitor", anonymous.audio_q()),
            **learning_columns("visitor", anonymous.audio_q()))).filter(requests__gt=0), "requests")[:10]
    plans = plan_summary(events)
    costs = costs_with_audio(totals["cost"], audio["audio_totals"], learning["learning_totals"]["learning_cost"])
    success_rate = rate(totals["successes"], totals["successes"] + totals["failures"])
    signup_rate = rate(visitors["signed_up"], visitors["total"])
    return render_report(request, "analytics/dashboard.html", "Usage analytics", "dashboard", {
        **filter_context(filters, models), **breakdowns(events), **plans, **audio, **learning,
        "totals": totals, "costs": costs,
        "windows": recent.aggregate(**windows()), "users": users, "visitors": visitors,
        "top_users": top_users, "top_visitors": top_visitors,
        "success_rate": success_rate, "signup_rate": signup_rate,
        "series": activity_series(recent, start),
        # Read off the aggregates above; no query of its own (apps/analytics/insights.py).
        "insights": dashboard_insights(
            totals=totals, costs=costs, audio_totals=audio["audio_totals"],
            learning_totals=learning["learning_totals"], plan_rows=plans["plan_rows"], visitors=visitors,
            signup_rate=signup_rate, success_rate=success_rate, plan_conversions=plans["plan_conversions"],
            spend_today=ai_spend(monitoring_day_start()).cost, daily_alert=settings.AI_COST_ALERT_DAILY_USD)})


@staff_member_required
def users_report(request):
    models = model_choices()
    filters = replace(ReportFilters.from_request(request, models), audience="registered")
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort") if request.GET.get("sort") in USER_SORTS else "requests"
    users = with_costs(User.objects.annotate(**usage_aggregates("usage_events__", filters.events_q("usage_events__")),
                                             **audio_columns("user", filters.audio_q()),
                                             **learning_columns("user", filters.audio_q()),
                                             last_active=Max("usage_events__created_at")))
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query))
    users = ordered(users, USER_SORTS[sort], descending=sort != "username")
    return render_report(request, "analytics/users.html", "Registered users", "users", {
        **filter_context(filters, models, q=query), "sort": sort,
        "page_obj": Paginator(users, PAGE_SIZE).get_page(request.GET.get("page"))})


@staff_member_required
def user_detail(request, pk):
    member = get_object_or_404(User, pk=pk)
    events = UsageEvent.objects.filter(user=member)
    summary = events.aggregate(**usage_aggregates(), **windows(), last_active=Max("created_at"))
    audio = audio_breakdowns(AudioUsageEvent.objects.filter(user=member))
    learning = learning_breakdown(LearningUsageEvent.objects.filter(user=member))
    return render_report(request, "analytics/user_detail.html", f"Usage: {member.get_username()}", "users", {
        **breakdowns(events), **audio, **learning, "member": member, "summary": summary,
        "costs": costs_with_audio(summary["cost"], audio["audio_totals"], learning["learning_totals"]["learning_cost"]),
        "success_rate": rate(summary["successes"], summary["successes"] + summary["failures"]),
        "visitors": member.converted_visitors.order_by("converted_at"),
        "series": activity_series(events, day_start(29))})


@staff_member_required
def visitors_report(request):
    models = model_choices()
    filters = replace(ReportFilters.from_request(request, models), audience="anonymous")
    scope = filters.events_q("usage_events__")
    query = request.GET.get("q", "").strip()
    converted = request.GET.get("converted") if request.GET.get("converted") in ("yes", "no") else ""
    sort = request.GET.get("sort") if request.GET.get("sort") in VISITOR_SORTS else "requests"
    visitors = (with_costs(AnonymousVisitor.objects.select_related("converted_user").annotate(
        **usage_aggregates("usage_events__", scope), **audio_columns("visitor", filters.audio_q()),
        **learning_columns("visitor", filters.audio_q()),
        active_days=Count(TruncDate("usage_events__created_at"), distinct=True, filter=scope)))
        .filter(Q(requests__gt=0) | Q(transcriptions__gt=0) | Q(speech_plays__gt=0) | Q(learning_calls__gt=0)))
    if converted:
        visitors = visitors.filter(converted_user__isnull=converted == "no")
    if query:
        lookup = visitor_lookup(query)
        visitors = visitors.filter(lookup) if lookup is not None else visitors.none()
    visitors = ordered(visitors, VISITOR_SORTS[sort])
    return render_report(request, "analytics/visitors.html", "Anonymous visitors", "visitors", {
        **filter_context(filters, models, q=query, converted=converted), "sort": sort,
        "page_obj": Paginator(visitors, PAGE_SIZE).get_page(request.GET.get("page"))})


@staff_member_required
def visitor_detail(request, pk):
    visitor = get_object_or_404(AnonymousVisitor.objects.select_related("converted_user"), pk=pk)
    events = UsageEvent.objects.filter(visitor=visitor, audience="anonymous")
    before = Q(created_at__lt=visitor.converted_at) if visitor.converted_at else None
    summary = events.aggregate(**usage_aggregates(), active_days=Count(TruncDate("created_at"), distinct=True),
                               first_request=Min("created_at"), last_request=Max("created_at"),
                               before_conversion=Count("id", filter=before))
    audio = audio_breakdowns(AudioUsageEvent.objects.filter(visitor=visitor, audience="anonymous"))
    learning = learning_breakdown(LearningUsageEvent.objects.filter(visitor=visitor, audience="anonymous"))
    after = None
    if visitor.converted_user_id and visitor.converted_at:
        after = UsageEvent.objects.filter(user_id=visitor.converted_user_id, created_at__gte=visitor.converted_at) \
            .aggregate(**usage_aggregates())
    return render_report(request, "analytics/visitor_detail.html", f"Usage: {visitor.short_id}", "visitors", {
        **breakdowns(events), **audio, **learning, "visitor": visitor, "summary": summary, "after": after,
        "costs": costs_with_audio(summary["cost"], audio["audio_totals"], learning["learning_totals"]["learning_cost"]),
        "success_rate": rate(summary["successes"], summary["successes"] + summary["failures"]),
        "series": activity_series(events, day_start(29))})
