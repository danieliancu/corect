import re

from django.conf import settings

from apps.assistant.schemas import CorrectionResult
from .openai_client import AssistantError, parse_response
from .prompts import CORRECTION_PROMPT


def same_text(first: str, second: str) -> bool:
    # The model may alter spacing when it echoes text back; only the words must match.
    return first.split() == second.split()


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


class CorrectionService:
    def correct(self, text: str) -> CorrectionResult:
        validate_text(text)
        result = parse_response(CORRECTION_PROMPT, text, CorrectionResult).output
        if not same_text(result.original_text, text):
            raise AssistantError("invalid_original")
        result.original_text = text
        if result.detected_language == "ro":
            # The view translates Romanian sent to Correct instead of showing this message.
            raise AssistantError("romanian_input", "Textul pare să fie în română. Apasă Translate pentru traducerea în engleză.")
        if result.detected_language != "en":
            raise AssistantError("language", "Scrie în engleză pentru a primi corecturi.")
        if any(item.original not in text for item in result.corrections):
            raise AssistantError("invalid_snippet")
        genuine = [item for item in result.corrections if not same_words(item.original, item.replacement)]
        if len(genuine) < len(result.corrections) and not genuine:
            result.overall_explanation = ""
        result.corrections = genuine
        result.has_errors = any(not item.is_british_english_preference for item in genuine)
        if not result.has_errors:
            # Keep silent capitalisation/punctuation fixes, but never a rewrite of correct English.
            if not same_words(result.corrected_text, text):
                result.corrected_text = text
        elif not result.corrected_text.strip() or same_words(result.corrected_text, text):
            raise AssistantError("invalid_correction")
        if not result.native_text.strip() or same_words(result.native_text, result.corrected_text):
            result.native_text = result.native_explanation = ""
        return result
