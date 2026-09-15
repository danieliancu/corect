from django.conf import settings
from django.db import transaction

from apps.assistant.models import AssistantRequest, GrammarCorrection
from apps.assistant.schemas import CorrectionResult
from .prompts import POLITE_PROMPT_VERSION, PROMPT_VERSION


def prompt_version(polite):
    return POLITE_PROMPT_VERSION if polite else PROMPT_VERSION


@transaction.atomic
def save_result(user, outcome):
    """Saves a signed-in learner's result under its effective operation (never "naturalize"). Grammar corrections are
    stored only for corrected English, so text written in another language never creates mistakes."""
    if not user.is_authenticated:
        return None
    result = outcome.result
    correction = isinstance(result, CorrectionResult)
    entry = AssistantRequest.objects.create(user=user, request_type=outcome.operation, original_text=result.original_text,
        result_text=result.corrected_text if correction else result.translated_text,
        detected_language=outcome.source_language, model_used=settings.OPENAI_MODEL,
        prompt_version=prompt_version(getattr(outcome, "polite", False)),
        status="success", result_data=result.model_dump())
    if correction:
        GrammarCorrection.objects.bulk_create([GrammarCorrection(request=entry, original=item.original,
            replacement=item.replacement, category=item.category, severity=item.severity,
            explanation_ro=item.explanation_ro, is_british_preference=item.is_british_english_preference)
            for item in result.corrections])
    return entry


def save_failure(user, kind, code, source_language="", polite=False):
    if user.is_authenticated:
        return AssistantRequest.objects.create(user=user, request_type=kind, status="failed", error_code=code,
            detected_language=source_language[:20], model_used=settings.OPENAI_MODEL, prompt_version=prompt_version(polite))
