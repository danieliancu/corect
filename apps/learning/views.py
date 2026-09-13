from collections import Counter
from datetime import timedelta
from typing import get_args

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.views.decorators.cache import never_cache

from apps.assistant.models import AssistantRequest, GrammarCorrection
from apps.assistant.schemas import Category
from .practice import QUESTIONS


def genuine_mistakes(user):
    return GrammarCorrection.objects.filter(request__user=user, request__status="success", is_british_preference=False)


def categories(user):
    return genuine_mistakes(user).values("category").annotate(total=Count("id")).order_by("-total", "category")


@login_required
@never_cache
def history(request):
    entries = AssistantRequest.objects.filter(user=request.user, status="success")
    return render(request, "learning/history.html", {"page_obj": Paginator(entries, 15).get_page(request.GET.get("page"))})


@login_required
@never_cache
def history_detail(request, pk):
    entry = get_object_or_404(AssistantRequest, pk=pk, user=request.user, status="success")
    return render(request, "learning/detail.html", {"entry": entry, "result": entry.result_data, "kind": entry.request_type})


@login_required
@never_cache
def mistakes(request):
    counts = Counter((" ".join(c.original.lower().split()), " ".join(c.replacement.lower().split()))
                     for c in genuine_mistakes(request.user).iterator())
    repeated = [{"original": key[0], "replacement": key[1], "total": total}
                for key, total in counts.most_common(10) if total > 1]
    return render(request, "learning/mistakes.html", {"categories": categories(request.user), "repeated": repeated})


@login_required
@never_cache
def mistake_category(request, category):
    if category not in get_args(Category) or category == "british_english":
        raise Http404
    items = genuine_mistakes(request.user).filter(category=category).select_related("request").order_by("-created_at")
    return render(request, "learning/mistake_category.html", {"category": category,
        "page_obj": Paginator(items, 20).get_page(request.GET.get("page")),
        "has_practice": any(question[0] == category for question in QUESTIONS)})


@login_required
@never_cache
def progress(request):
    entries = AssistantRequest.objects.filter(user=request.user, status="success")
    today = timezone.localdate()
    start = today - timedelta(days=29)
    totals = dict(genuine_mistakes(request.user).filter(created_at__date__gte=start)
                  .annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id")).values_list("day", "total"))
    trend = [{"date": start + timedelta(days=i), "total": totals.get(start + timedelta(days=i), 0)} for i in range(30)]
    trend_data = [{"label": date_format(day["date"], "j M"), "long": date_format(day["date"], "l, j F"), "total": day["total"]}
                  for day in trend]
    return render(request, "learning/progress.html", {"trend_data": trend_data,
        "correction_count": entries.filter(request_type="correction").count(),
        "translation_count": entries.filter(request_type="translation").count(),
        "mistake_count": genuine_mistakes(request.user).count(), "categories": categories(request.user), "trend": trend})


@login_required
@never_cache
def practice(request):
    ranked = [row["category"] for row in categories(request.user)]
    ordered = sorted(range(len(QUESTIONS)), key=lambda i: ranked.index(QUESTIONS[i][0]) if QUESTIONS[i][0] in ranked else len(ranked))
    try:
        position = int(request.POST.get("position", request.GET.get("q", 0))) % len(ordered)
    except (TypeError, ValueError):
        position = 0
    if request.method == "GET" and "category" in request.GET:
        # Links from a mistake category open that category's exercise.
        position = next((i for i, index in enumerate(ordered) if QUESTIONS[index][0] == request.GET["category"]), position)
    category, question, options, answer, explanation = QUESTIONS[ordered[position]]
    context = {"category": category.replace("_", " "), "question": question, "options": options,
        "position": position, "next": (position + 1) % len(ordered), "personalised": category in ranked}
    if request.method == "POST":
        choice = request.POST.get("answer")
        if choice not in {str(i) for i in range(len(options))}:
            context["error"] = "Choose an answer first."
        else:
            context.update(checked=True, correct=int(choice) == answer, explanation=explanation, answer=options[answer])
    return render(request, "learning/practice.html", context)
