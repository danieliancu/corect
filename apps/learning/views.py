from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import get_args
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.suspension import SUSPENDED_MESSAGE
from apps.assistant.languages import CORRECTION, TRANSLATION, translation_codes
from apps.assistant.models import AssistantRequest, GrammarCorrection
from apps.assistant.presentation import history_group
from apps.assistant.schemas import Category
from apps.core.entitlements import has_feature
from .models import Exercise, PracticeSession
from .services.daily import complete_session, start_session, today_plan
from .services.exercises import CHOICE_TYPES, grade, record_attempt
from .services.insights import activity_days, change_cards, insight_cards, learning_overview
from .services.priorities import ranked_patterns
from .services.profile import refresh_if_stale
from .taxonomy import PATTERNS, pattern_label

HISTORY_DAYS_PER_PAGE = 7
Kind = PracticeSession.Kind
INSTRUCTIONS = {"multiple_choice": "Alege varianta corectă.", "choose_phrase": "Alege expresia potrivită.",
                "fill_blank": "Completează spațiul liber.", "rewrite": "Rescrie propoziția.",
                "short_correction": "Corectează propoziția."}
GENERATION_FAILED = "Nu am putut pregăti exerciții noi acum. Încearcă din nou în câteva minute."
NO_EXERCISES = "Încă nu avem exerciții pentru tine. Scrie câteva texte în engleză și revino."


def genuine_mistakes(user):
    return GrammarCorrection.objects.filter(request__user=user, request__status="success", is_british_preference=False)


def categories(user):
    return genuine_mistakes(user).values("category").annotate(total=Count("id")).order_by("-total", "category")


def open_session(user, now):
    return PracticeSession.objects.filter(user=user, completed_at__isnull=True,
                                          started_at__gte=now - timedelta(hours=12)).order_by("-started_at").first()


@login_required
@never_cache
def dashboard(request):
    """"Pentru tine azi" and the learner's profile. Every figure comes from the database; nothing here calls AI."""
    user, now = request.user, timezone.now()
    plan = today_plan(user, now)
    activity = activity_days(user, now=now)
    return render(request, "learning/dashboard.html", {
        "learning_section": "dashboard", "plan": plan, "patterns": list(ranked_patterns(user)[:6]),
        "overview": learning_overview(user, now=now), "activity": activity,
        "activity_total": sum(day["total"] for day in activity),
        "open_session": open_session(user, now)})


@login_required
@require_POST
def practice_start(request):
    kind = request.POST.get("kind", "")
    pattern_key = request.POST.get("pattern", "") if kind == Kind.PATTERN else ""
    if kind not in Kind.values or (kind == Kind.PATTERN and pattern_key not in PATTERNS):
        raise Http404
    if not has_feature(request.user, "personalised_practice"):
        messages.info(request, "Exercițiile personalizate fac parte din planul Pro.")
        return redirect("learn_dashboard")
    session, error = start_session(request.user, kind, pattern_key)
    if session is None:
        messages.error(request, SUSPENDED_MESSAGE if error == "account_suspended" else
                       NO_EXERCISES if error == "no_exercises" else GENERATION_FAILED)
        return redirect("learn_dashboard")
    if error:
        messages.info(request, "Nu am putut pregăti exerciții noi, așa că am folosit exerciții pregătite deja.")
    return redirect("learn_session", pk=session.pk)


def session_title(session):
    if session.kind == Kind.PATTERN:
        return f"Exersează · {pattern_label(session.pattern_key)}"
    return "Pentru tine azi"


@login_required
@never_cache
def practice_session(request, pk):
    session = get_object_or_404(PracticeSession, pk=pk, user=request.user)
    total = len(session.exercise_ids)
    if session.completed_at or session.position >= total:
        if not session.completed_at:
            complete_session(session)
        results = session.attempts.filter(is_correct__isnull=False).values("pattern_key").annotate(
            correct=Count("id", filter=Q(is_correct=True)), total=Count("id")).order_by("pattern_key")
        return render(request, "learning/session.html", {
            "learning_section": "practice", "session": session, "finished": True, "title": session_title(session),
            "answered": sum(row["total"] for row in results),
            "by_pattern": [{**row, "label": pattern_label(row["pattern_key"])} for row in results if row["pattern_key"]]})
    exercise = get_object_or_404(Exercise.objects.filter(Q(user=request.user) | Q(user__isnull=True)),
                                 pk=session.exercise_ids[session.position])
    target = session.exercise_patterns[session.position] if session.position < len(session.exercise_patterns) \
        else exercise.pattern_key
    attempt = session.attempts.filter(exercise=exercise).first()
    context = {"learning_section": "practice", "session": session, "exercise": exercise, "title": session_title(session),
               "number": session.position + 1, "total": total, "percent": round(100 * session.position / total),
               "is_last": session.position + 1 >= total, "is_choice": exercise.exercise_type in CHOICE_TYPES,
               "instruction": INSTRUCTIONS.get(exercise.exercise_type, ""), "pattern_label": pattern_label(target)}
    if request.method == "POST":
        if request.POST.get("action") == "next":
            session.position += 1
            session.save(update_fields=["position"])
            if session.position >= total:
                complete_session(session)
            return redirect("learn_session", pk=session.pk)
        if request.POST.get("action") == "answer" and attempt is None:
            answer = request.POST.get("answer", "").strip()
            if not answer:
                context["error"] = "Alege sau scrie mai întâi un răspuns."
            else:
                try:
                    grading = grade(request.user, exercise, answer)
                except ValueError:
                    context["error"] = "Alege unul dintre răspunsuri."
                else:
                    attempt = record_attempt(request.user, session, exercise, target, answer, grading)
                    if grading[0]:
                        session.correct_count += 1
                        session.save(update_fields=["correct_count"])
                    context["better_answer"] = grading[3]
    if attempt and context["is_choice"] and attempt.answer.isdigit() and int(attempt.answer) < len(exercise.options):
        context["chosen"] = exercise.options[int(attempt.answer)]
    context["attempt"] = attempt
    return render(request, "learning/session.html", context)


@login_required
@never_cache
def history(request):
    entries = AssistantRequest.objects.filter(user=request.user, status="success")
    # Grouped by local day, newest first. Pages hold whole days, so a day is never split across two pages.
    page_obj = Paginator(entries.datetimes("created_at", "day", order="DESC"), HISTORY_DAYS_PER_PAGE).get_page(
        request.GET.get("page"))
    day_starts = list(page_obj.object_list)
    days = []
    if day_starts:
        start = timezone.make_aware(datetime.combine(day_starts[-1].date(), datetime.min.time()))
        end = timezone.make_aware(datetime.combine(day_starts[0].date() + timedelta(days=1), datetime.min.time()))
        grouped = {day.date(): [] for day in day_starts}
        for entry in entries.filter(created_at__gte=start, created_at__lt=end):
            grouped[timezone.localdate(entry.created_at)].append(entry)
        days = [{"date": day, "entries": items, "groups": Counter(history_group(entry) for entry in items)}
                for day, items in grouped.items()]
    return render(request, "learning/history.html", {"learning_section": "history", "page_obj": page_obj, "days": days})


@login_required
@never_cache
def history_detail(request, pk):
    entry = get_object_or_404(AssistantRequest, pk=pk, user=request.user, status="success")
    return render(request, "learning/detail.html", {"learning_section": "history", "entry": entry,
                                                    "result": entry.result_data, "kind": entry.request_type})


@login_required
@never_cache
def mistakes(request):
    counts, where = Counter(), defaultdict(Counter)
    for correction in genuine_mistakes(request.user).iterator():
        key = (" ".join(correction.original.lower().split()), " ".join(correction.replacement.lower().split()))
        counts[key] += 1
        where[key][correction.category] += 1
    # Each repeated mistake links to its examples: its (most common) category page, filtered to that exact change.
    repeated = [{"original": original, "replacement": replacement, "total": total,
                 "category": where[(original, replacement)].most_common(1)[0][0],
                 "query": urlencode({"original": original, "replacement": replacement})}
                for (original, replacement), total in counts.most_common(10) if total > 1]
    refresh_if_stale(request.user)
    return render(request, "learning/mistakes.html", {"learning_section": "mistakes", "categories": categories(request.user),
                                                      "repeated": repeated, "patterns": list(ranked_patterns(request.user))})


@login_required
@never_cache
def mistake_category(request, category):
    if category not in get_args(Category) or category == "british_english":
        raise Http404
    items = genuine_mistakes(request.user).filter(category=category).select_related("request").order_by("-created_at")
    original, replacement = (" ".join(request.GET.get(name, "").split()) for name in ("original", "replacement"))
    pair = None
    if original and replacement:  # From "Tipuri de revăzut": only the examples of that one change.
        items = items.filter(original__iexact=original, replacement__iexact=replacement)
        pair = {"original": original, "replacement": replacement,
                "query": urlencode({"original": original, "replacement": replacement})}
    return render(request, "learning/mistake_category.html", {"learning_section": "mistakes", "category": category,
        "pair": pair, "page_obj": Paginator(items, 20).get_page(request.GET.get("page"))})


@login_required
@never_cache
def progress(request):
    """Insights first ("what is getting better, what still needs work"), then the chart. Facts are calculated, not AI."""
    user = request.user
    entries = AssistantRequest.objects.filter(user=user, status="success")
    today = timezone.localdate()
    start = today - timedelta(days=29)
    totals = dict(genuine_mistakes(user).filter(created_at__date__gte=start)
                  .annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id")).values_list("day", "total"))
    trend = [{"date": start + timedelta(days=i), "total": totals.get(start + timedelta(days=i), 0)} for i in range(30)]
    trend_data = [{"label": date_format(day["date"], "j M"), "long": date_format(day["date"], "l, j F"), "total": day["total"]}
                  for day in trend]
    patterns = list(ranked_patterns(user))
    return render(request, "learning/progress.html", {"learning_section": "progress", "trend_data": trend_data,
        "english_count": entries.filter(request_type=CORRECTION).count(),
        "into_english_count": entries.filter(request_type=TRANSLATION, detected_language__in=translation_codes()).count(),
        "mistake_count": genuine_mistakes(user).count(), "categories": categories(user), "trend": trend,
        "overview": learning_overview(user, patterns), "cards": what_changed(user, patterns)})


def what_changed(user, patterns):
    """"Ce s-a schimbat" cards with their chart data; the arrow leads to the category's mistakes when that page exists."""
    cards = change_cards(user, insight_cards(user, patterns))
    for card in cards:
        valid = card["category"] in get_args(Category) and card["category"] != "british_english"
        card["detail_url"] = reverse("mistake_category", args=[card["category"]]) if valid else ""
    return cards


@login_required
@never_cache
def practice(request):
    """The practice hub: today's personalised plan and the patterns to practise."""
    return render(request, "learning/practice.html", {
        "learning_section": "practice", "plan": today_plan(request.user), "patterns": list(ranked_patterns(request.user)[:6]),
        "open_session": open_session(request.user, timezone.now())})
