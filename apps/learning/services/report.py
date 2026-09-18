"""The end-of-practice report: score, verdict, what changed per pattern, the answers to review and the streak. No AI."""
from datetime import timedelta

from django.utils import timezone

from apps.core.text import romanian_count
from apps.learning.models import PracticeSession, UserMistakePattern
from apps.learning.taxonomy import pattern_label

from .exercises import CHOICE_TYPES

REVIEW_LIMIT = 5
STREAK_LOOKBACK_DAYS = 366
# (minimum accuracy %, stars out of 3, title, text), best first.
VERDICTS = (
    (100, 3, "Perfect!", "Toate răspunsurile sunt corecte. Așa se fixează o tipologie."),
    (60, 2, "Foarte bine!", "Mai ai puțin până stăpânești ce ai exersat azi."),
    (30, 1, "Bun început", "Uită-te la răspunsurile de mai jos și încearcă din nou: așa se învață."),
    (0, 0, "Merită repetat", "E normal la început. Citește explicațiile de mai jos și mai încearcă o dată."),
)


def verdict(correct, answered):
    if not answered:
        return {"percent": None, "stars": 0, "title": "Sesiune încheiată",
                "text": "Nu am putut verifica răspunsurile de data asta, dar exercițiile rămân în recapitulare."}
    percent = round(100 * correct / answered)
    for minimum, stars, title, text in VERDICTS:
        if percent >= minimum:
            return {"percent": percent, "stars": stars, "title": title, "text": text}


def practice_streak(user, now=None):
    """Consecutive local days, ending today, with at least one finished practice session."""
    now = now or timezone.now()
    finished = PracticeSession.objects.filter(user=user, completed_at__gte=now - timedelta(days=STREAK_LOOKBACK_DAYS),
                                              completed_at__lte=now).values_list("completed_at", flat=True)
    days = {timezone.localdate(value) for value in finished}
    streak, day = 0, timezone.localdate(now)
    while day in days:
        streak, day = streak + 1, day - timedelta(days=1)
    return streak


def shown_answer(attempt):
    """What the learner picked: the option's text for choice exercises (stored as its index), otherwise their words."""
    exercise, answer = attempt.exercise, attempt.answer
    if exercise.exercise_type in CHOICE_TYPES and answer.isdigit() and int(answer) < len(exercise.options):
        return exercise.options[int(answer)]
    return answer


def session_report(session, now=None):
    attempts = list(session.attempts.select_related("exercise").order_by("created_at", "pk"))
    graded = [attempt for attempt in attempts if attempt.is_correct is not None]
    correct = sum(1 for attempt in graded if attempt.is_correct)
    wrong = [attempt for attempt in graded if not attempt.is_correct]
    keys = list(dict.fromkeys(attempt.pattern_key for attempt in graded if attempt.pattern_key))
    patterns = {pattern.pattern_key: pattern
                for pattern in UserMistakePattern.objects.filter(user=session.user, pattern_key__in=keys)}
    streak = practice_streak(session.user, now)
    progress = []
    for key in keys:
        rows = [attempt for attempt in graded if attempt.pattern_key == key]
        pattern = patterns.get(key)
        before = session.mastery_at_start.get(key)
        after = pattern.mastery if pattern else None
        progress.append({
            "label": pattern_label(key), "correct": sum(1 for attempt in rows if attempt.is_correct), "total": len(rows),
            "before": before, "after": after,
            "delta": after - before if after is not None and before is not None else None,
            "status": pattern.status if pattern else "", "next_review_at": pattern.next_review_at if pattern else None,
            "mastered": bool(pattern and pattern.status == UserMistakePattern.Status.MASTERED),
        })
    return {
        "correct": correct, "answered": len(graded), "wrong": len(wrong),
        "unverified": len(attempts) - len(graded), "verdict": verdict(correct, len(graded)), "progress": progress,
        "review": [{"question": attempt.exercise.question, "answer": shown_answer(attempt),
                    "correct_answer": attempt.exercise.correct_answer} for attempt in wrong[:REVIEW_LIMIT]],
        "more_to_review": max(0, len(wrong) - REVIEW_LIMIT), "streak": streak,
        "streak_unit": romanian_count(streak, "zi", "zile").split(" ", 1)[1] + " la rând",
    }
