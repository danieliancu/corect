from django.conf import settings
from django.db import transaction

from apps.assistant.models import AssistantRequest, GrammarCorrection
from .prompts import PROMPT_VERSION


@transaction.atomic
def save_result(user, kind, text, result):
    if not user.is_authenticated:
        return
    correction = kind == "correction"
    entry = AssistantRequest.objects.create(user=user, request_type=kind, original_text=text,
        result_text=result.corrected_text if correction else result.translated_text,
        detected_language=result.detected_language if correction else result.source_language,
        model_used=settings.OPENAI_MODEL, prompt_version=PROMPT_VERSION, status="success",
        result_data=result.model_dump())
    if correction:
        GrammarCorrection.objects.bulk_create([GrammarCorrection(request=entry, original=item.original,
            replacement=item.replacement, category=item.category, severity=item.severity,
            explanation_ro=item.explanation_ro, is_british_preference=item.is_british_english_preference)
            for item in result.corrections])


def save_failure(user, kind, code):
    if user.is_authenticated:
        AssistantRequest.objects.create(user=user, request_type=kind, status="failed", error_code=code,
            model_used=settings.OPENAI_MODEL, prompt_version=PROMPT_VERSION)
