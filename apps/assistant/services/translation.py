from apps.assistant.schemas import TranslationResult
from .correction import same_text, validate_text
from .openai_client import AssistantError, parse_response
from .prompts import TRANSLATION_PROMPT


class TranslationService:
    def translate(self, text: str) -> TranslationResult:
        validate_text(text)
        result = parse_response(TRANSLATION_PROMPT, text, TranslationResult).output
        if result.source_language not in ("en", "ro"):
            raise AssistantError("language", "Scrie un text în română sau engleză pentru traducere.")
        if ((result.source_language, result.target_language) not in (("en", "ro"), ("ro", "en"))
                or not same_text(result.original_text, text) or not result.translated_text.strip()):
            raise AssistantError("invalid_translation")
        result.original_text = text
        return result
