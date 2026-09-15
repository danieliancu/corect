"""Structured outputs for the learning AI functions, validated before anything is stored."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExerciseType = Literal["multiple_choice", "fill_blank", "choose_phrase", "rewrite", "short_correction"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedExercise(StrictModel):
    exercise_type: ExerciseType
    question: str = Field(min_length=3, max_length=400)
    options: list[str] = Field(max_length=4)
    correct_answer: str = Field(min_length=1, max_length=300)
    accepted_answers: list[str] = Field(max_length=6)
    explanation_ro: str = Field(min_length=3, max_length=600)
    difficulty: Literal[1, 2, 3]
    uk_context: str = Field(max_length=120)


class ExerciseBatch(StrictModel):
    pattern: str
    exercises: list[GeneratedExercise] = Field(min_length=1, max_length=12)


class OpenAnswerEvaluation(StrictModel):
    is_correct: bool
    feedback_ro: str = Field(min_length=1, max_length=400)
    better_answer: str = Field(max_length=400)
