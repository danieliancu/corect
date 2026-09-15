"""Vreau să sune natural!: English or Romanian in, natural British English out, in exactly one generative call.

The model classifies the source language and applies that language's rules in the same structured response; no
separate detection call is ever made. The service then turns the raw output into the persisted result for the effective
internal operation (apps/assistant/languages.py):
- correction (English): only genuine errors are listed, correct English is never rewritten, and the natural British
  version is kept only when it differs from the corrected text;
- translation (Romanian): the British English text; no grammar corrections are ever created.
"""
import re
from dataclasses import dataclass

from django.conf import settings
from pydantic import ValidationError

from apps.assistant.languages import CORRECTION, TARGET, TRANSLATION, operation_for, unsupported_message
from apps.assistant.schemas import Correction, CorrectionResult, NaturalizeResult, TranslationResult
from apps.learning.taxonomy import valid_pattern
from .openai_client import AssistantError, guarded_parse
from .prompts import NATURALIZE_PROMPT, PROMPT_VERSION

# One static key for everyone (never per user): the prompt prefix is identical for every request.
PROMPT_CACHE_KEY = f"corect:naturalize:{PROMPT_VERSION}"
# max_output_tokens also has to cover the model's reasoning tokens. Measured with the language-quality eval
# (apps/assistant/evals, 2026-09-15): short texts used at most 24 % of the floor, but long English full of mistakes
# used up to 3806 tokens (reasoning, the corrected text, the natural version and one explanation per mistake), so
# the budget grows by two tokens per character to keep the heaviest response near 60 %. It still caps runaway output.
OUTPUT_TOKENS_RESERVE, OUTPUT_TOKENS_PER_CHARACTER, OUTPUT_TOKENS_CEILING = 3000, 2, 8000


def same_words(first: str, second: str) -> bool:
    # Capitalisation and punctuation are fixed silently, so they never count as a real change.
    def words(text):
        return re.findall(r"[\w']+", text.replace("’", "'").lower())
    return words(first) == words(second)


def validate_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise AssistantError("empty", "Scrie mai întâi puțin text.")
    if len(text) > settings.ASSISTANT_MAX_CHARACTERS:
        raise AssistantError("too_long", f"Folosește cel mult {settings.ASSISTANT_MAX_CHARACTERS} de caractere.")


def output_token_budget(text: str) -> int:
    """A fixed reserve for reasoning and explanations, plus room for the text itself appearing more than once (the
    corrected text, the natural version and the corrected snippets), capped: 7000 at 2000 characters."""
    return min(OUTPUT_TOKENS_CEILING, OUTPUT_TOKENS_RESERVE + OUTPUT_TOKENS_PER_CHARACTER * len(text))


def reasoning_option() -> dict:
    effort = settings.OPENAI_REASONING_EFFORT
    return {"reasoning": {"effort": effort}} if effort else {}


def finalise_correction(text: str, raw: NaturalizeResult) -> CorrectionResult:
    if any(item.original not in text for item in raw.corrections):
        raise AssistantError("invalid_snippet")  # Never show a "mistake" the learner did not write.
    corrections = []
    for draft in raw.corrections:
        preference = draft.category == "british_english"
        corrections.append(Correction(
            original=draft.original, replacement=draft.replacement, category=draft.category,
            # A pattern from another category would put the mistake in the wrong learning group.
            pattern=valid_pattern(draft.category, draft.pattern),
            severity="suggestion" if preference else draft.severity, explanation_ro=draft.explanation_ro,
            is_british_english_preference=preference))
    genuine = [item for item in corrections if not same_words(item.original, item.replacement)]
    overall_explanation = "" if len(genuine) < len(corrections) and not genuine else raw.overall_explanation
    has_errors = any(not item.is_british_english_preference for item in genuine)
    corrected = raw.corrected_text
    if not has_errors:
        # Keep silent capitalisation/punctuation fixes, but never a rewrite of correct English.
        if not same_words(corrected, text):
            corrected = text
    elif not corrected.strip() or same_words(corrected, text):
        raise AssistantError("invalid_correction")
    natural, natural_explanation = raw.natural_text, raw.natural_explanation
    if not natural.strip() or same_words(natural, corrected):
        natural = natural_explanation = ""  # Already natural: no alternative is manufactured.
    try:
        return CorrectionResult(detected_language=raw.source_language, original_text=text, corrected_text=corrected,
                                has_errors=has_errors, corrections=genuine, overall_explanation=overall_explanation,
                                native_text=natural, native_explanation=natural_explanation)
    except ValidationError:
        raise AssistantError("invalid_correction") from None


def finalise_translation(text: str, raw: NaturalizeResult) -> TranslationResult:
    translated = raw.natural_text.strip()
    if not translated or same_words(translated, text):
        raise AssistantError("invalid_translation")
    # Any corrections or explanations the model returned for this language are ignored: no fake grammar mistakes.
    return TranslationResult(source_language=raw.source_language, target_language=TARGET.legacy_code,
                             original_text=text, translated_text=translated)


FINALISERS = {CORRECTION: finalise_correction, TRANSLATION: finalise_translation}


@dataclass(frozen=True)
class Naturalized:
    operation: str  # correction | translation
    source_language: str
    result: CorrectionResult | TranslationResult


class NaturalizeService:
    def naturalize(self, text: str) -> Naturalized:
        validate_text(text)
        raw = guarded_parse(NATURALIZE_PROMPT, text, NaturalizeResult, max_output_tokens=output_token_budget(text),
                            prompt_cache_key=PROMPT_CACHE_KEY, **reasoning_option()).output
        operation = operation_for(raw.source_language)
        if operation is None:
            error = AssistantError("language", unsupported_message())
            error.source_language = raw.source_language
            raise error
        try:
            result = FINALISERS[operation](text, raw)
        except AssistantError as exc:
            exc.source_language = raw.source_language  # The failure is still counted under its operation.
            raise
        return Naturalized(operation, raw.source_language, result)
