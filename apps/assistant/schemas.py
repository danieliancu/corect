from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["spelling", "verb_tense", "verb_form", "article", "preposition", "word_order",
    "subject_verb_agreement", "conditional", "collocation", "punctuation", "vocabulary",
    "british_english", "romanian_transfer", "other"]
Language = Literal["en", "ro", "other", "ambiguous"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Correction(StrictModel):
    original: str = Field(min_length=1)
    replacement: str
    category: Category
    severity: Literal["minor", "major", "suggestion"]
    explanation_ro: str = Field(min_length=1, max_length=1500)
    is_british_english_preference: bool

    @model_validator(mode="after")
    def consistent_preference(self):
        if self.is_british_english_preference != (self.category == "british_english"):
            raise ValueError("British preferences must use the british_english category")
        if self.is_british_english_preference and self.severity != "suggestion":
            raise ValueError("British preferences must be suggestions")
        return self


class CorrectionResult(StrictModel):
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
    source_language: Language
    target_language: Language
    original_text: str
    translated_text: str
