"""The personal mistake profile: occurrences from saved corrections, recurrence, status and mastery. No AI."""
from collections import Counter, defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from apps.learning.models import ExerciseAttempt, MistakeOccurrence, UserMistakePattern
from apps.learning.taxonomy import derive_pattern, pattern_label, valid_pattern

from . import rules
from .priorities import score_pattern, top_pattern_keys

Status = UserMistakePattern.Status


def correction_pattern_key(correction, stored=None):
    """The AI's pattern when it fits the category, otherwise the deterministic one."""
    if stored:
        return valid_pattern(correction.category, stored)
    return derive_pattern(correction.category, correction.original, correction.replacement)


def occurrences_for(entry):
    """Unsaved occurrences for a saved correction request: genuine mistakes only, never British preferences."""
    stored = (entry.result_data or {}).get("corrections") or []
    occurrences = []
    for index, correction in enumerate(entry.corrections.order_by("pk")):
        if correction.is_british_preference:
            continue
        pattern = stored[index].get("pattern") if index < len(stored) and isinstance(stored[index], dict) else None
        occurrences.append(MistakeOccurrence(user_id=entry.user_id, correction=correction, category=correction.category,
                                             pattern_key=correction_pattern_key(correction, pattern),
                                             occurred_at=correction.created_at))
    return occurrences


def record_correction_occurrences(user, entry):
    """Links a signed-in learner's new correction to their profile and returns hints for the result card:
    {pattern_key: {"previous_count", "is_top", "label"}}."""
    if entry is None or not user.is_authenticated or entry.request_type != "correction":
        return {}
    occurrences = occurrences_for(entry)
    if not occurrences:
        return {}
    keys = {item.pattern_key for item in occurrences}
    with transaction.atomic():
        mastered = set(UserMistakePattern.objects.filter(user=user, pattern_key__in=keys, status=Status.MASTERED)
                       .values_list("pattern_key", flat=True))
        MistakeOccurrence.objects.bulk_create(occurrences, ignore_conflicts=True)
        refresh_patterns(user, keys, resurfaced=mastered)
    counts = Counter(item.pattern_key for item in occurrences)
    totals = dict(UserMistakePattern.objects.filter(user=user, pattern_key__in=keys)
                  .values_list("pattern_key", "occurrence_count"))
    top = set(top_pattern_keys(user))
    return {key: {"previous_count": max(totals.get(key, 0) - counts[key], 0), "label": pattern_label(key),
                  "is_top": key in top and totals.get(key, 0) > 1} for key in keys}


def practice_stats(user, keys=None, now=None):
    """Per pattern: accuracy over the latest attempts, and success/failure totals."""
    now = now or timezone.now()
    attempts = ExerciseAttempt.objects.filter(user=user, is_correct__isnull=False,
                                              created_at__gte=now - timedelta(days=rules.ATTEMPT_WINDOW_DAYS))
    totals = ExerciseAttempt.objects.filter(user=user, is_correct__isnull=False)
    if keys is not None:
        attempts, totals = attempts.filter(pattern_key__in=keys), totals.filter(pattern_key__in=keys)
    latest = defaultdict(list)
    for key, correct in attempts.order_by("pattern_key", "-created_at").values_list("pattern_key", "is_correct"):
        if len(latest[key]) < rules.RECENT_ATTEMPTS:
            latest[key].append(correct)
    summary = {row["pattern_key"]: row for row in totals.values("pattern_key").annotate(
        successes=Count("id", filter=Q(is_correct=True)), failures=Count("id", filter=Q(is_correct=False)))}
    stats = {}
    for key in set(latest) | set(summary):
        recent = latest.get(key, [])
        stats[key] = {"attempts": len(recent), "accuracy": sum(recent) / len(recent) if recent else None,
                      "recent_successes": sum(recent), "recent_failures": len(recent) - sum(recent),
                      "successes": summary.get(key, {}).get("successes", 0),
                      "failures": summary.get(key, {}).get("failures", 0)}
    return stats


def decide_status(pattern, *, recent, previous, total, stats, quiet_days, resurfaced_now):
    attempts, accuracy = stats["attempts"], stats["accuracy"] or 0
    if resurfaced_now:
        return Status.RESURFACED
    if (attempts >= rules.MASTERED_MIN_ATTEMPTS and accuracy >= rules.MASTERED_ACCURACY
            and quiet_days >= rules.MASTERED_QUIET_DAYS):
        return Status.MASTERED
    if pattern.status == Status.MASTERED:
        return Status.MASTERED  # Only a new occurrence brings a mastered pattern back.
    if (attempts >= rules.IMPROVING_MIN_ATTEMPTS and accuracy >= rules.IMPROVING_ACCURACY
            and (recent < previous or quiet_days >= rules.IMPROVING_QUIET_DAYS)):
        return Status.IMPROVING
    if pattern.status == Status.RESURFACED:
        return Status.RESURFACED
    if recent >= rules.RECURRING_RECENT or total >= rules.RECURRING_TOTAL:
        return Status.RECURRING
    return Status.NEW


def refresh_patterns(user, keys=None, resurfaced=(), now=None):
    """Recalculates counts, status, mastery and priority from the database (a handful of grouped queries)."""
    now = now or timezone.now()
    recent_start = now - timedelta(days=rules.RECENT_DAYS)
    previous_start = recent_start - timedelta(days=rules.RECENT_DAYS)
    occurrences = MistakeOccurrence.objects.filter(user=user)
    if keys is not None:
        occurrences = occurrences.filter(pattern_key__in=keys)
    rows = list(occurrences.values("pattern_key").annotate(
        category=Max("category"), total=Count("id"), recent=Count("id", filter=Q(occurred_at__gte=recent_start)),
        previous=Count("id", filter=Q(occurred_at__gte=previous_start, occurred_at__lt=recent_start)),
        first=Min("occurred_at"), last=Max("occurred_at")))
    row_keys = [row["pattern_key"] for row in rows]
    existing = {p.pattern_key: p for p in UserMistakePattern.objects.filter(user=user, pattern_key__in=row_keys)}
    stats = practice_stats(user, row_keys, now)
    empty = {"attempts": 0, "accuracy": None, "recent_successes": 0, "recent_failures": 0, "successes": 0, "failures": 0}
    created, updated = [], []
    for row in rows:
        key = row["pattern_key"]
        pattern = existing.get(key)
        is_new = pattern is None
        if is_new:
            pattern = UserMistakePattern(user=user, pattern_key=key, category=row["category"], first_seen_at=row["first"],
                                         last_seen_at=row["last"], next_review_at=now)
        pattern_stats = stats.get(key, empty)
        quiet_days = (now - row["last"]).days
        resurfaced_now = key in resurfaced
        pattern.category, pattern.first_seen_at, pattern.last_seen_at = row["category"], row["first"], row["last"]
        pattern.occurrence_count, pattern.recent_occurrence_count = row["total"], row["recent"]
        pattern.previous_period_count = row["previous"]
        pattern.successful_practice_count, pattern.failed_practice_count = pattern_stats["successes"], pattern_stats["failures"]
        status = decide_status(pattern, recent=row["recent"], previous=row["previous"], total=row["total"],
                               stats=pattern_stats, quiet_days=quiet_days, resurfaced_now=resurfaced_now)
        if resurfaced_now:
            pattern.review_step, pattern.next_review_at = 0, now
        if status == Status.MASTERED and pattern.status != Status.MASTERED:
            pattern.last_mastered_at = now
        pattern.status = status
        accuracy = pattern_stats["accuracy"] or 0
        pattern.mastery = round(100 * (0.6 * accuracy + 0.4 * min(quiet_days, 30) / 30)) if pattern_stats["attempts"] else \
            round(40 * min(quiet_days, 30) / 30)
        pattern.updated_at = now  # bulk_update does not apply auto_now.
        pattern.recurrence_score = round(row["recent"] + 0.5 * row["previous"] + 0.1 * row["total"], 2)
        pattern.priority_score = score_pattern(pattern, recent_successes=pattern_stats["recent_successes"],
                                               recent_failures=pattern_stats["recent_failures"], now=now)
        (created if is_new else updated).append(pattern)
    UserMistakePattern.objects.bulk_create(created, ignore_conflicts=True)
    if updated:
        UserMistakePattern.objects.bulk_update(updated, [
            "category", "first_seen_at", "last_seen_at", "occurrence_count", "recent_occurrence_count",
            "previous_period_count", "successful_practice_count", "failed_practice_count", "mastery", "recurrence_score",
            "priority_score", "review_step", "next_review_at", "last_mastered_at", "status", "updated_at"])
    return len(created), len(updated)


def refresh_if_stale(user, now=None):
    """Priority depends on time (reviews falling due), so it is recalculated when a learning page opens, at most hourly."""
    now = now or timezone.now()
    stale = UserMistakePattern.objects.filter(
        user=user, updated_at__lt=now - timedelta(minutes=rules.PROFILE_REFRESH_MINUTES)).exists()
    if stale:
        refresh_patterns(user, now=now)
