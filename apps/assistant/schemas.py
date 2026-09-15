from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.assistant.languages import UNSUPPORTED, source_codes
from apps.learning.taxonomy import PATTERN_KEYS

Category = Literal["spelling", "verb_tense", "verb_form", "article", "preposition", "word_order",
    "subject_verb_agreement", "conditional", "collocation", "punctuation", "vocabulary",
    "british_english", "romanian_transfer", "other"]
PatternKey = Literal[PATTERN_KEYS]  # Mistake patterns (apps/learning/taxonomy.py), chosen in the same response.
Language = Literal["en", "ro", "other", "ambiguous"]
SourceLanguageCode = Literal[source_codes() + UNSUPPORTED]  # From the language registry (apps/assistant/languages.py).


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectionDraft(StrictModel):
    """A correction as the model returns it. Cross-field rules are applied afterwards by the service, never here: a
    validator failing inside the provider call would lose the billed usage of that call."""

    original: str = Field(min_length=1)
    replacement: str
    category: Category
    pattern: PatternKey
    severity: Literal["minor", "major", "suggestion"]
    explanation_ro: str = Field(min_length=1, max_length=1500)
    is_british_english_preference: bool


class Correction(CorrectionDraft):
    @model_validator(mode="after")
    def consistent_preference(self):
        if self.is_british_english_preference != (self.category == "british_english"):
            raise ValueError("British preferences must use the british_english category")
        if self.is_british_english_preference and self.severity != "suggestion":
            raise ValueError("British preferences must be suggestions")
        return self


class NaturalizeResult(StrictModel):
    """The single structured output of "Vreau să sune natural!". Field order is generation order: the language first.

    English: corrected_text holds only genuine fixes; natural_text is the natural British version, empty when the text
    already sounds natural. Romanian: natural_text is the British English text; the other fields stay empty.
    The input is never echoed back: the service already has it.
    """

    source_language: SourceLanguageCode
    corrected_text: str
    has_errors: bool
    corrections: list[CorrectionDraft] = Field(max_length=100)
    overall_explanation: str = Field(max_length=2000)
    natural_text: str
    natural_explanation: str = Field(max_length=1000)


class CorrectionResult(StrictModel):
    """Persisted shape of an English result (AssistantRequest.result_data), unchanged since before Naturalise."""

    detected_language: Language
    original_text: str
    corrected_text: str
    has_errors: bool
    corrections: list[Correction] = Field(max_length=100)
    overall_explanation: str = Field(max_length=2000)
    native_text: str
    native_explanation: str = Field(max_length=1000)

    @model_validator(mode="after")
    def consistent_errors(self):
        genuine = any(not item.is_british_english_preference for item in self.corrections)
        if genuine != self.has_errors:
            raise ValueError("Error flag does not match genuine corrections")
        return self


class TranslationResult(StrictModel):
    """Persisted shape of a result written in British English from another language. History also holds older rows
    translated from English into Romanian (target_language "ro"), which stay readable."""

    source_language: str = Field(max_length=12)
    target_language: str = Field(max_length=12)
    original_text: str
    translated_text: str
