"""The language-quality dataset: realistic English and Romanian from Romanian speakers living in the UK."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.assistant.languages import CORRECTION, TRANSLATION, UNSUPPORTED, source_codes

DEFAULT_CASES = Path(__file__).with_name("naturalize_cases.jsonl")
GROUPS = ("en_romanian_transfer", "en_contexts", "en_tense_time", "en_punctuation_only", "en_already_natural",
          "en_correct_unnatural", "en_american", "en_mixed_ro_words", "ro_to_en", "unsupported", "long")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(Strict):
    """What a good answer must do. Phrase checks ignore case, punctuation and apostrophe style; every inner list of
    `*_includes_any` is a set of acceptable alternatives, and each set must be satisfied."""

    source_language: Literal[source_codes() + UNSUPPORTED] | None = None
    operation: Literal[CORRECTION, TRANSLATION] | None = None
    error_code: str | None = None
    has_errors: bool | None = None
    natural: Literal["required", "forbidden", "any"] = "any"
    corrected_includes_any: list[list[str]] = Field(default_factory=list)  # English corrected text only.
    output_includes_any: list[list[str]] = Field(default_factory=list)  # Corrected or natural text / British English.
    output_excludes: list[str] = Field(default_factory=list)
    categories_any: list[str] = Field(default_factory=list)
    min_corrections: int | None = None
    max_corrections: int | None = None


class EvalCase(Strict):
    id: str = Field(min_length=3)
    group: Literal[GROUPS]
    context: str = "general"
    input: str = Field(min_length=1)
    expect: Expectation
    notes: str = ""


def load_cases(path: Path = DEFAULT_CASES, groups=None, limit=None) -> list[EvalCase]:
    cases = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                cases.append(EvalCase.model_validate(json.loads(line)))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from None
    if groups:
        cases = [case for case in cases if case.group in groups]
    return cases[:limit] if limit else cases
