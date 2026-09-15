"""The languages of "Vreau să sune natural!": the only place that knows which source languages exist.

Three separate ideas:
- source language: what the learner wrote or said (English or Romanian today);
- target language: always British English (en-GB);
- explanation language: the language of the interface and of every explanation (Romanian today).

Each source language maps to an internal operation. English is `correction` (genuine errors repaired, plus a natural
British version when it adds something); every other language is `translation` (rewritten directly in natural British
English). Adding a language later means one registry entry, its prompt rules in services/prompts.py and eval cases: the
interface, the request flow, persistence and analytics stay as they are.
"""
from dataclasses import dataclass

CORRECTION = "correction"
TRANSLATION = "translation"
UNCLASSIFIED = "unclassified"  # A request that failed before its language was known.
UNSUPPORTED = ("other", "ambiguous")  # Classifications the model may return that have no operation.


@dataclass(frozen=True)
class SourceLanguage:
    code: str  # ISO 639-1, exactly as the model classifies it.
    name: str  # English name, used in prompts.
    label_ro: str  # Lower-case name shown to learners.
    operation: str


@dataclass(frozen=True)
class TargetLanguage:
    code: str
    legacy_code: str  # Stored in TranslationResult.target_language (history written before en-GB was explicit).
    name: str
    label_ro: str


TARGET = TargetLanguage(code="en-GB", legacy_code="en", name="British English", label_ro="engleză britanică")
EXPLANATION_LANGUAGE = "ro"
EXPLANATION_LANGUAGE_NAME = "Romanian"
SOURCE_LANGUAGES: dict[str, SourceLanguage] = {language.code: language for language in (
    SourceLanguage("en", "English", "engleză", CORRECTION),
    SourceLanguage("ro", "Romanian", "română", TRANSLATION),
)}


def source_codes() -> tuple[str, ...]:
    return tuple(SOURCE_LANGUAGES)


def operation_for(code: str) -> str | None:
    """The internal operation for a classified source language, or None when the language is not supported."""
    language = SOURCE_LANGUAGES.get(code)
    return language.operation if language else None


def translation_codes() -> tuple[str, ...]:
    return tuple(code for code, language in SOURCE_LANGUAGES.items() if language.operation == TRANSLATION)


def source_label(code: str) -> str:
    language = SOURCE_LANGUAGES.get(code)
    return language.label_ro if language else ""


def _joined(names: list[str], conjunction: str) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


def unsupported_message() -> str:
    names = _joined([language.label_ro for language in SOURCE_LANGUAGES.values()], "sau")
    return f"Scrie în {names} ca să primești varianta naturală în engleză britanică."


def transcription_prompt() -> str:
    """The speech-to-text hint: every supported source language, mixed speech allowed."""
    names = _joined([language.name for language in SOURCE_LANGUAGES.values()], "or")
    return f"The speaker may use {names}, or mix them in the same recording."
