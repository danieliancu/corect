from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

from apps.assistant.models import AssistantRequest, GrammarCorrection
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.usage import ParsedResponse, ProviderUsage, report_usage
from apps.learning.models import Exercise, ExerciseAttempt
from apps.learning.schemas import ExerciseBatch, OpenAnswerEvaluation

USAGE = ProviderUsage(model="test-model", response_model="test-model-2026", input_tokens=900, cached_input_tokens=0,
                      output_tokens=600, reasoning_tokens=0, total_tokens=1500)


def make_correction(user, category="preposition", original="since five years", replacement="for five years",
                    pattern="since_vs_for", days_ago=0, preference=False):
    """A saved correction request with one correction, created `days_ago` days ago."""
    entry = AssistantRequest.objects.create(
        user=user, request_type="correction", original_text=original, result_text=replacement, model_used="test-model",
        prompt_version="test", status="success",
        result_data={"corrections": [{"original": original, "replacement": replacement, "category": category,
                                      "pattern": pattern, "is_british_english_preference": preference}]})
    correction = GrammarCorrection.objects.create(request=entry, original=original, replacement=replacement,
                                                  category=category, severity="minor", explanation_ro="Explicație.",
                                                  is_british_preference=preference)
    when = timezone.now() - timedelta(days=days_ago)
    GrammarCorrection.objects.filter(pk=correction.pk).update(created_at=when)
    AssistantRequest.objects.filter(pk=entry.pk).update(created_at=when)
    entry.refresh_from_db()
    return entry


def make_exercise(user, pattern_key="since_vs_for", count=1, exercise_type="multiple_choice", **fields):
    values = {"question": "I've lived here ___ 2019.", "options": ["since", "for"], "correct_answer": "since",
              "explanation_ro": "Since cu un moment de început.", **fields}
    return [Exercise.objects.create(user=user, pattern_key=pattern_key, exercise_type=exercise_type,
                                    source="generated", **{**values, "question": f"{values['question']} #{i}"})
            for i in range(count)]


def attempt(user, exercise, correct, days_ago=0, pattern_key=None):
    item = ExerciseAttempt.objects.create(user=user, exercise=exercise, pattern_key=pattern_key or exercise.pattern_key,
                                          answer="0", is_correct=correct, graded_by="deterministic")
    ExerciseAttempt.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return item


def batch(pattern="since_vs_for", count=8):
    items = []
    for i in range(count):
        kind = ("multiple_choice", "fill_blank", "choose_phrase")[i % 3]
        items.append({"exercise_type": kind,
                      "question": f"I've worked here ___ March, number {i}." if kind == "fill_blank" else f"Question {i} about since and for.",
                      "options": [] if kind == "fill_blank" else ["since", "for"],
                      "correct_answer": "since",
                      "accepted_answers": [], "explanation_ro": "Since arată începutul.", "difficulty": 1,
                      "uk_context": "at work"})
    return ExerciseBatch.model_validate({"pattern": pattern, "exercises": items})


def replying(output):
    """A parse_response stand-in that reports provider usage like the real client."""
    def reply(*args, **kwargs):
        report_usage(USAGE)
        return ParsedResponse(output, USAGE)
    return reply


def failing(code="timeout"):
    def reply(*args, **kwargs):
        raise AssistantError(code)
    return reply


@contextmanager
def patch_ai(output=None, error=None):
    """Replaces both model entry points (plain and guarded) with one mock, so call assertions cover either."""
    model = MagicMock(side_effect=failing(error) if error else replying(output))
    with patch("apps.learning.services.ai.parse_response", model), patch("apps.learning.services.ai.guarded_parse", model):
        yield model


__all__ = ["make_correction", "make_exercise", "attempt", "batch", "patch_ai", "USAGE", "OpenAnswerEvaluation"]
