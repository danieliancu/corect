"""Progress facts calculated from the database: what improved, what still repeats, what changed. No AI."""
import math
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.assistant.models import AssistantRequest
from apps.learning.models import ExerciseAttempt, MistakeOccurrence, UserMistakePattern
from apps.learning.taxonomy import pattern_label
from apps.learning.templatetags.learning_labels import romanian_count

from . import rules
from .profile import practice_stats

Status = UserMistakePattern.Status
IMPROVED = {Status.IMPROVING, Status.MASTERED}
TO_PRACTISE = {Status.NEW, Status.RECURRING, Status.RESURFACED}


def learning_overview(user, patterns=None, now=None):
    """Summary for the dashboard and Progress. Trends are only claimed when there is enough data."""
    now = now or timezone.now()
    patterns = list(UserMistakePattern.objects.filter(user=user)) if patterns is None else patterns
    corrections = AssistantRequest.objects.filter(user=user, status="success", request_type="correction") \
        .aggregate(total=Count("id"), first=Min("created_at"))
    history_days = (now - corrections["first"]).days if corrections["first"] else 0
    if not corrections["total"]:
        stage = "empty"
    elif corrections["total"] >= rules.INSIGHT_MIN_CORRECTIONS and history_days >= rules.INSIGHT_MIN_HISTORY_DAYS:
        stage = "ready"
    else:
        stage = "early"
    repeated_recent = sum(p.recent_occurrence_count for p in patterns if p.recent_occurrence_count >= 2)
    repeated_previous = sum(p.previous_period_count for p in patterns if p.previous_period_count >= 2)
    change = None
    if stage == "ready" and repeated_previous >= rules.INSIGHT_MIN_PREVIOUS:
        change = round((repeated_recent - repeated_previous) / repeated_previous * 100)
    return {"stage": stage, "corrections": corrections["total"], "patterns": len(patterns),
            "improved": sum(p.status in IMPROVED for p in patterns),
            "to_practise": sum(p.status in TO_PRACTISE for p in patterns),
            "mastered": sum(p.status == Status.MASTERED for p in patterns),
            "repeated_change": change, "repeated_reduction": -change if change is not None and change < 0 else None,
            "exercises_30": ExerciseAttempt.objects.filter(user=user, created_at__gte=now - timedelta(days=rules.RECENT_DAYS)).count()}


def resurfaced_gap(user, pattern_key):
    dates = list(MistakeOccurrence.objects.filter(user=user, pattern_key=pattern_key).order_by("-occurred_at")
                 .values_list("occurred_at", flat=True)[:2])
    return (dates[0] - dates[1]).days if len(dates) == 2 else None


def insight_cards(user, patterns=None, now=None):
    """Up to two cards per kind: improved, still repeating, almost solved, resurfaced."""
    now = now or timezone.now()
    patterns = list(UserMistakePattern.objects.filter(user=user).order_by("-priority_score")) if patterns is None else patterns
    stats = practice_stats(user, [p.pattern_key for p in patterns], now)
    groups = {"improved": [], "persistent": [], "almost": [], "resurfaced": []}
    for pattern in patterns:
        label, stat = pattern_label(pattern.pattern_key), stats.get(pattern.pattern_key)
        recent, previous = pattern.recent_occurrence_count, pattern.previous_period_count
        card = {"label": label, "pattern_key": pattern.pattern_key, "category": pattern.category}
        if pattern.status == Status.RESURFACED:
            gap = resurfaced_gap(user, pattern.pattern_key)
            if gap:
                groups["resurfaced"].append({**card, "kind": "resurfaced", "title": "A revenit",
                    "text": f"Nu apăruse de {romanian_count(gap, 'zi', 'zile')}, dar ai făcut din nou această greșeală."})
        elif previous >= rules.INSIGHT_MIN_PREVIOUS and recent <= previous * rules.INSIGHT_IMPROVED_RATIO:
            percent = round((previous - recent) / previous * 100)
            groups["improved"].append({**card, "kind": "improved", "title": "Te-ai îmbunătățit",
                "text": f"Cu {percent}% mai puține greșeli repetate decât în perioada anterioară."})
        elif (stat and stat["attempts"] >= rules.INSIGHT_ALMOST_ATTEMPTS and stat["accuracy"] >= rules.INSIGHT_ALMOST_ACCURACY
              and pattern.status != Status.MASTERED):
            groups["almost"].append({**card, "kind": "almost", "title": "Aproape rezolvat",
                "text": f"Ai răspuns corect la {stat['recent_successes']} din ultimele {stat['attempts']} exerciții."})
        elif recent >= rules.INSIGHT_PERSISTENT_RECENT:
            groups["persistent"].append({**card, "kind": "persistent", "title": "Încă se repetă",
                "text": f"Ai făcut această greșeală de {romanian_count(recent, 'dată', 'ori')} în ultimele 30 de zile."})
    return [card for kind in ("improved", "persistent", "almost", "resurfaced") for card in groups[kind][:2]]


TREND_WEEKS = 8
CHART_WIDTH, CHART_HEIGHT = 200, 80
RING_RADIUS = 42
LATEST_RESULTS = 5


def smooth_path(points):
    """A soft curve through the points (Catmull-Rom drawn as cubic Béziers), kept inside the chart."""
    top, bottom = 6, CHART_HEIGHT - 10
    clamp = lambda y: min(max(y, top), bottom)
    path = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = points[max(i - 1, 0)], points[i], points[i + 1], points[min(i + 2, len(points) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, clamp(p1[1] + (p2[1] - p0[1]) / 6))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, clamp(p2[1] - (p3[1] - p1[1]) / 6))
        path.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(path)


def weekly_trend(counts):
    """SVG geometry for weekly occurrence counts, oldest week first; the last point is the latest week."""
    peak = max(counts + [1])
    step = (CHART_WIDTH - 16) / (len(counts) - 1)
    points = [(8 + i * step, CHART_HEIGHT - 10 - value / peak * (CHART_HEIGHT - 24)) for i, value in enumerate(counts)]
    line = smooth_path(points)
    return {"counts": counts, "line": line,
            "area": f"{line} L{points[-1][0]:.1f},{CHART_HEIGHT} L{points[0][0]:.1f},{CHART_HEIGHT} Z",
            # Numbers for SVG attributes are formatted here: the Romanian locale would render 192.0 as "192,0".
            "end_x": f"{points[-1][0]:.1f}", "end_y": f"{points[-1][1]:.1f}"}


def change_cards(user, cards, now=None):
    """The insight cards with their small charts drawn from real data: occurrences per week for a pattern, and for an
    almost solved one the practice accuracy (ring) and the latest results (dots). Pairs of cards first; almost solved
    cards, and a card left without a pair, take the full width."""
    now = now or timezone.now()
    keys = [card["pattern_key"] for card in cards]
    weekly = defaultdict(lambda: [0] * TREND_WEEKS)
    for key, when in (MistakeOccurrence.objects.filter(user=user, pattern_key__in=keys,
                                                       occurred_at__gt=now - timedelta(weeks=TREND_WEEKS))
                      .values_list("pattern_key", "occurred_at")):
        weekly[key][TREND_WEEKS - 1 - min((now - when).days // 7, TREND_WEEKS - 1)] += 1
    almost_keys = [card["pattern_key"] for card in cards if card["kind"] == "almost"]
    stats = practice_stats(user, almost_keys, now) if almost_keys else {}
    latest = defaultdict(list)
    for key, correct in (ExerciseAttempt.objects.filter(user=user, pattern_key__in=almost_keys, is_correct__isnull=False)
                         .order_by("pattern_key", "-created_at").values_list("pattern_key", "is_correct")):
        if len(latest[key]) < LATEST_RESULTS:
            latest[key].append(correct)
    circumference = 2 * math.pi * RING_RADIUS
    pairs, wide = [], []
    for card in cards:
        if card["kind"] == "almost":
            percent = round(100 * ((stats.get(card["pattern_key"]) or {}).get("accuracy") or 0))
            dots = latest[card["pattern_key"]][::-1]
            wide.append({**card, "wide": True, "percent": percent, "dots": dots, "dots_correct": sum(dots),
                         "ring_length": f"{circumference * percent / 100:.2f}", "ring_circumference": f"{circumference:.2f}"})
        else:
            pairs.append({**card, "wide": False, "trend": weekly_trend(weekly[card["pattern_key"]])})
    if len(pairs) % 2:
        pairs[-1]["wide"] = True
    return pairs + wide


def activity_days(user, days=7, now=None):
    """Corrections and exercises per local day, zero-filled, for the compact activity chart and its table."""
    now = now or timezone.now()
    start = timezone.localdate(now) - timedelta(days=days - 1)
    corrections = dict(AssistantRequest.objects.filter(user=user, status="success", created_at__date__gte=start)
                       .annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id"))
                       .values_list("day", "total"))
    exercises = dict(ExerciseAttempt.objects.filter(user=user, created_at__date__gte=start)
                     .annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id"))
                     .values_list("day", "total"))
    rows = [{"date": start + timedelta(days=offset), "corrections": corrections.get(start + timedelta(days=offset), 0),
             "exercises": exercises.get(start + timedelta(days=offset), 0)} for offset in range(days)]
    peak = max([row["corrections"] + row["exercises"] for row in rows] + [1])
    for row in rows:
        row["total"] = row["corrections"] + row["exercises"]
        row["percent"] = round(100 * row["total"] / peak)
    return rows
