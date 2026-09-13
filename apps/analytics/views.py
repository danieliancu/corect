"""Staff-only usage analytics. Every figure is a database aggregate over the usage ledger."""
from dataclasses import replace
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Min, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .filters import AUDIENCES, PERIODS, TYPES, ReportFilters, day_start
from .formatting import rate
from .identifiers import visitor_lookup
from .models import AnonymousVisitor, UsageEvent

User = get_user_model()
PAGE_SIZE = 50
USER_SORTS = {"requests": "requests", "tokens": "tokens_total", "cost": "cost", "last_active": "last_active",
              "joined": "date_joined", "username": "username"}
VISITOR_SORTS = {"requests": "requests", "tokens": "tokens_total", "cost": "cost", "last_seen": "last_seen_at",
                 "first_seen": "first_seen_at", "active_days": "active_days"}


def usage_aggregates(prefix="", scope=None):
    """Conditional aggregates over usage events, directly (prefix "") or through a relation ("usage_events__").

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
        "translations": count(request_type="translation"), "successes": count(status="success"),
        "failures": count(status="failed"), "rejections": count(status="rejected"),
        "tokens_in": total("input_tokens"), "tokens_cached": total("cached_input_tokens"),
        "tokens_out": total("output_tokens"), "tokens_total": total("total_tokens"), "cost": total("estimated_cost"),
        "without_tokens": count(total_tokens__isnull=True), "without_cost": count(estimated_cost__isnull=True),
    }


def windows():
    return {"today": Count("id", filter=Q(created_at__gte=day_start(0))),
            "last_7": Count("id", filter=Q(created_at__gte=day_start(6))),
            "last_30": Count("id", filter=Q(created_at__gte=day_start(29)))}


def activity_series(events, start):
    """Requests per local day from `start` (zero-filled), or per month when there is no start."""
    if start is None:
        rows = (events.annotate(bucket=TruncMonth("created_at")).values("bucket")
                .annotate(requests=Count("id"), tokens=Sum("total_tokens")).order_by("bucket"))
        return [{"label": row["bucket"].strftime("%b %Y"), "requests": row["requests"], "tokens": row["tokens"]}
                for row in rows]
    rows = {row["bucket"]: row for row in events.filter(created_at__gte=start).annotate(bucket=TruncDate("created_at"))
            .values("bucket").annotate(requests=Count("id"), tokens=Sum("total_tokens"))}
    first = timezone.localdate(start)
    series = []
    for offset in range((timezone.localdate() - first).days + 1):
        day = first + timedelta(days=offset)
        row = rows.get(day, {})
        series.append({"label": day.strftime("%d %b"), "requests": row.get("requests", 0), "tokens": row.get("tokens")})
    return series


def breakdowns(events):
    return {
        "errors": events.exclude(status="success").values("status", "error_code")
        .annotate(total=Count("id")).order_by("-total", "error_code"),
        "by_model": events.values("model").annotate(requests=Count("id"), tokens=Sum("total_tokens"),
                                                    cost=Sum("estimated_cost")).order_by("-requests", "model"),
    }


def model_choices():
    return list(UsageEvent.objects.exclude(model="").order_by("model").values_list("model", flat=True).distinct())


def ordered(queryset, field, descending=True):
    return queryset.order_by(F(field).desc(nulls_last=True) if descending else F(field).asc(), "pk")


def render_report(request, template, title, section, context):
    return render(request, template, {**admin.site.each_context(request), "title": title, "section": section, **context})


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
    recent = UsageEvent.objects.filter(filters.events_q(include_period=False))
    users = User.objects.aggregate(total=Count("id"),
                                   new=Count("id", filter=Q(date_joined__gte=start) if start else None))
    cohort = AnonymousVisitor.objects.filter(first_seen_at__gte=start) if start else AnonymousVisitor.objects.all()
    visitors = cohort.aggregate(total=Count("id"), signed_up=Count("id", filter=Q(converted_via="signup")),
                                signed_in=Count("id", filter=Q(converted_via="login")))
    visitors["all_time"] = AnonymousVisitor.objects.count()
    visitors["active"] = (events.filter(audience="anonymous", visitor__isnull=False)
                          .values("visitor").distinct().count())
    top_users = [] if filters.audience == "anonymous" else ordered(
        User.objects.annotate(**usage_aggregates("usage_events__", replace(filters, audience="registered")
                                                 .events_q("usage_events__"))).filter(requests__gt=0), "requests")[:10]
    top_visitors = [] if filters.audience == "registered" else ordered(
        AnonymousVisitor.objects.select_related("converted_user").annotate(**usage_aggregates(
            "usage_events__", replace(filters, audience="anonymous").events_q("usage_events__")))
        .filter(requests__gt=0), "requests")[:10]
    return render_report(request, "analytics/dashboard.html", "Usage analytics", "dashboard", {
        **filter_context(filters, models), **breakdowns(events), "totals": totals, "windows": recent.aggregate(**windows()),
        "users": users, "visitors": visitors, "top_users": top_users, "top_visitors": top_visitors,
        "success_rate": rate(totals["successes"], totals["successes"] + totals["failures"]),
        "signup_rate": rate(visitors["signed_up"], visitors["total"]),
        "series": activity_series(recent, start)})


@staff_member_required
def users_report(request):
    models = model_choices()
    filters = replace(ReportFilters.from_request(request, models), audience="registered")
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort") if request.GET.get("sort") in USER_SORTS else "requests"
    users = User.objects.annotate(**usage_aggregates("usage_events__", filters.events_q("usage_events__")),
                                  last_active=Max("usage_events__created_at"))
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
    return render_report(request, "analytics/user_detail.html", f"Usage: {member.get_username()}", "users", {
        **breakdowns(events), "member": member, "summary": summary,
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
    visitors = (AnonymousVisitor.objects.select_related("converted_user")
                .annotate(**usage_aggregates("usage_events__", scope),
                          active_days=Count(TruncDate("usage_events__created_at"), distinct=True, filter=scope))
                .filter(requests__gt=0))
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
    after = None
    if visitor.converted_user_id and visitor.converted_at:
        after = UsageEvent.objects.filter(user_id=visitor.converted_user_id, created_at__gte=visitor.converted_at) \
            .aggregate(**usage_aggregates())
    return render_report(request, "analytics/visitor_detail.html", f"Usage: {visitor.short_id}", "visitors", {
        **breakdowns(events), "visitor": visitor, "summary": summary, "after": after,
        "success_rate": rate(summary["successes"], summary["successes"] + summary["failures"]),
        "series": activity_series(events, day_start(29))})
