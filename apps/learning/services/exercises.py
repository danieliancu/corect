"""Stored exercises: reuse first, generate a batch with AI only when too few are waiting, grade in Python whenever the
answer is deterministic, and fall back to the editorial bank when AI is unavailable."""
import uuid
from datetime import timedelta
from typing import NamedTuple

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.assistant.models import SubmissionClaim
from apps.learning.models import Exercise, ExerciseAttempt, MistakeOccurrence
from apps.learning.practice import QUESTIONS
from apps.learning.schemas import ExerciseBatch, OpenAnswerEvaluation
from apps.learning.taxonomy import PATTERNS, fallback_pattern, pattern_category, pattern_hint

from . import rules
from .ai import Feature, LearningAIUnavailable, learning_call
from .prompts import (OPEN_ANSWER_PROMPT, OPEN_ANSWER_PROMPT_VERSION, PERSONALISED_PRACTICE_PROMPT,
                      PERSONALISED_PRACTICE_PROMPT_VERSION)

Type = Exercise.Type
GradedBy = ExerciseAttempt.GradedBy
CHOICE_TYPES = {Type.MULTIPLE_CHOICE, Type.CHOOSE_PHRASE}
# What sessions use: options or one short blank, never a whole sentence to write (rewrite, short_correction).
PRACTICE_TYPES = CHOICE_TYPES | {Type.FILL_BLANK}
FILL_BLANK_MAX_WORDS = 5
EDITORIAL_PATTERNS = {"verb_form": "base_form_after_did", "verb_tense": "present_perfect_duration", "article": "a_vs_an",
                      "preposition": "dependent_preposition", "conditional": "second_conditional_form",
                      "collocation": "take_vs_make", "romanian_transfer": "age_with_be",
                      "subject_verb_agreement": "third_person_s", "word_order": "question_word_order",
                      "vocabulary": "borrow_vs_lend", "british_english": "american_spelling_preference"}
QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def seed_editorial():
    """The editorial question bank as shared exercises (no user), created once."""
    existing = set(Exercise.objects.filter(source=Exercise.Source.EDITORIAL).values_list("question", flat=True))
    Exercise.objects.bulk_create([
        Exercise(source=Exercise.Source.EDITORIAL, pattern_key=EDITORIAL_PATTERNS.get(category, fallback_pattern(category)),
                 exercise_type=Type.MULTIPLE_CHOICE, question=question, options=options, correct_answer=options[answer],
                 explanation_ro=explanation, difficulty=1)
        for category, question, options, answer, explanation in QUESTIONS if question not in existing])


def rested_ids(user, now):
    return ExerciseAttempt.objects.filter(user=user, created_at__gte=now - timedelta(days=rules.EXERCISE_REST_DAYS)) \
        .values("exercise_id")


def suitable_pool(user, pattern_key, now=None):
    """The learner's stored exercises for a pattern that are not retired or answered in the last days."""
    now = now or timezone.now()
    return (Exercise.objects.filter(user=user, pattern_key=pattern_key, retired_at__isnull=True,
                                    exercise_type__in=PRACTICE_TYPES)
            .exclude(pk__in=rested_ids(user, now)).order_by("times_shown", "generated_at", "pk"))


def editorial_pool(user, pattern_key, now=None):
    """Editorial exercises for the same pattern first, then the same category."""
    now = now or timezone.now()
    seed_editorial()
    category = pattern_category(pattern_key)
    keys = [key for key in PATTERNS if pattern_category(key) == category]
    items = Exercise.objects.filter(source=Exercise.Source.EDITORIAL, pattern_key__in=keys,
                                    exercise_type__in=PRACTICE_TYPES).exclude(pk__in=rested_ids(user, now))
    return sorted(items, key=lambda item: (item.pattern_key != pattern_key, item.pk))


def ensure_exercises(user, pattern_key, needed, now=None):
    """Up to `needed` exercises and an error code ("" when fine).

    AI generates a batch only when fewer than `needed` suitable exercises are stored and fewer than
    LEARNING_REUSE_THRESHOLD are waiting. When AI is unavailable, editorial exercises fill the gap.
    """
    now = now or timezone.now()
    Exercise.objects.filter(user=user, source=Exercise.Source.GENERATED, retired_at__isnull=True,
                            generated_at__lt=now - timedelta(days=rules.EXERCISE_MAX_AGE_DAYS)).update(retired_at=now)
    available = suitable_pool(user, pattern_key, now).count()
    error = ""
    if available < needed and available < settings.LEARNING_REUSE_THRESHOLD:
        try:
            generate_batch(user, pattern_key)
        except LearningAIUnavailable as exc:
            error = exc.code
    chosen = list(suitable_pool(user, pattern_key, now)[:needed])
    if len(chosen) < needed:
        chosen += editorial_pool(user, pattern_key, now)[:needed - len(chosen)]
    return chosen, error


def generation_payload(user, pattern_key):
    """The minimum context: the pattern and at most three recent examples (truncated). Never history."""
    examples = (MistakeOccurrence.objects.filter(user=user, pattern_key=pattern_key).select_related("correction")
                .order_by("-occurred_at")[:3])
    return {"count": settings.LEARNING_BATCH_SIZE, "pattern": pattern_key,
            "pattern_hint": pattern_hint(pattern_key), "category": pattern_category(pattern_key),
            "recent_mistakes": [{"wrong": item.correction.original[:120], "correct": item.correction.replacement[:120]}
                                for item in examples]}


def clean_batch(batch):
    """Keeps only exercises that can be graded as intended; fewer than three usable is an invalid output."""
    usable = []
    for item in batch.exercises:
        options = [option.strip() for option in item.options if option.strip()]
        answer = item.correct_answer.strip()
        if item.exercise_type not in PRACTICE_TYPES:
            continue
        if item.exercise_type in CHOICE_TYPES:
            if not 2 <= len(options) <= 4 or len({option.lower() for option in options}) != len(options) or answer not in options:
                continue
        else:
            # One short blank: a whole sentence to type is not what practice asks for.
            if item.question.count("___") != 1 or len(answer.split()) > FILL_BLANK_MAX_WORDS:
                continue
            options = []
        usable.append((item, options, answer))
    if len(usable) < rules.MIN_VALID_GENERATED:
        raise ValueError("too few usable exercises")
    return usable


def generate_batch(user, pattern_key):
    version = PERSONALISED_PRACTICE_PROMPT_VERSION
    usable, event = learning_call(
        user=user, feature=Feature.PRACTICE, prompt=PERSONALISED_PRACTICE_PROMPT, prompt_version=version,
        payload=generation_payload(user, pattern_key), schema=ExerciseBatch, validate=clean_batch)
    batch_id = uuid.uuid4()
    return Exercise.objects.bulk_create([
        Exercise(user=user, pattern_key=pattern_key, exercise_type=item.exercise_type,
                 question=item.question.strip(), options=options, correct_answer=answer,
                 accepted_answers=[] if item.exercise_type in CHOICE_TYPES else
                 [value.strip() for value in item.accepted_answers if value.strip()],
                 open_ended=item.exercise_type == Type.REWRITE, explanation_ro=item.explanation_ro.strip(),
                 difficulty=item.difficulty, uk_context=item.uk_context.strip()[:200], source=Exercise.Source.GENERATED,
                 batch_id=batch_id, prompt_version=version, model=settings.OPENAI_LEARNING_MODEL[:100], usage_event=event)
        for item, options, answer in usable])


def normalise(text):
    return " ".join((text or "").translate(QUOTES).lower().split()).strip(" .!?\"'")


class Grading(NamedTuple):
    is_correct: bool | None
    graded_by: str
    feedback_ro: str = ""
    better_answer: str = ""
    unavailable_code: str = ""  # Why AI could not check an open answer (e.g. a learning AI limit), else "".


def grade(user, exercise, answer):
    """A Grading. AI is used only for open-ended rewrites that do not match a known answer; when it is unavailable the
    attempt is left unverified and not counted."""
    if exercise.exercise_type in CHOICE_TYPES:
        try:
            chosen = exercise.options[int(answer)]
        except (TypeError, ValueError, IndexError):
            raise ValueError("invalid choice") from None
        return Grading(normalise(chosen) == normalise(exercise.correct_answer), GradedBy.DETERMINISTIC)
    if normalise(answer) in {normalise(value) for value in [exercise.correct_answer, *exercise.accepted_answers]}:
        return Grading(True, GradedBy.DETERMINISTIC)
    if not exercise.open_ended:
        return Grading(False, GradedBy.DETERMINISTIC)
    try:
        evaluation, _ = learning_call(
            user=user, feature=Feature.OPEN_ANSWER, prompt=OPEN_ANSWER_PROMPT, prompt_version=OPEN_ANSWER_PROMPT_VERSION,
            payload={"question": exercise.question, "model_answer": exercise.correct_answer,
                     "accepted_answers": exercise.accepted_answers, "learner_answer": answer[:400]},
            schema=OpenAnswerEvaluation, learner_text=answer[:400], max_output_tokens=800)
    except LearningAIUnavailable as exc:
        return Grading(None, GradedBy.UNVERIFIED, unavailable_code=exc.code)
    return Grading(evaluation.is_correct, GradedBy.AI, evaluation.feedback_ro, evaluation.better_answer)


ANSWER_CLAIM_NAMESPACE = uuid.UUID("5d0f3c3e-6f0b-4c61-9d7e-1b0c2f5a8e41")


def _answer_claim(user, session, exercise):
    return {"actor": f"learn-answer:{user.pk}",
            "token": uuid.uuid5(ANSWER_CLAIM_NAMESPACE, f"{session.pk}:{exercise.pk}")}


def claim_answer(user, session, exercise):
    """True for the first submission of an answer to this exercise in this session, False for any repeat (a double
    click, a second tab). The unique claim is atomic, so two concurrent requests are never both graded."""
    try:
        with transaction.atomic():
            SubmissionClaim.objects.create(**_answer_claim(user, session, exercise))
    except IntegrityError:
        return False
    return True


def release_answer(user, session, exercise):
    """The submission could not be graded (an invalid choice or an error): the learner may answer again."""
    SubmissionClaim.objects.filter(**_answer_claim(user, session, exercise)).delete()


def record_attempt(user, session, exercise, pattern_key, answer, grading):
    is_correct, graded_by, feedback = grading[:3]
    attempt = ExerciseAttempt.objects.create(user=user, session=session, exercise=exercise, pattern_key=pattern_key,
                                             answer=answer[:1000], is_correct=is_correct, graded_by=graded_by,
                                             feedback_ro=feedback)
    if exercise.source == Exercise.Source.GENERATED:
        if is_correct:
            Exercise.objects.filter(pk=exercise.pk).update(times_correct=F("times_correct") + 1)
            exercise.times_correct += 1
        if exercise.times_correct >= rules.EXERCISE_RETIRE_CORRECT or exercise.times_shown >= rules.EXERCISE_RETIRE_SHOWN:
            Exercise.objects.filter(pk=exercise.pk, retired_at__isnull=True).update(retired_at=timezone.now())
    return attempt
