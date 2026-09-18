import io
import math
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import LearningUsageEvent
from apps.assistant.models import GrammarCorrection
from apps.learning.models import Exercise, MistakeOccurrence, PracticeSession, UserMistakePattern
from apps.learning.schemas import OpenAnswerEvaluation
from apps.learning.services import rules
from apps.learning.services.daily import allocation, complete_session, start_session, today_plan
from apps.learning.services.exercises import ensure_exercises, generation_payload, grade
from apps.learning.services.insights import change_cards, insight_cards, learning_overview
from apps.learning.services.priorities import top_pattern_keys
from apps.learning.services.profile import record_correction_occurrences, refresh_patterns
from apps.learning.services.review import due_patterns, schedule_after_practice
from apps.learning.taxonomy import derive_pattern, valid_pattern
from .helpers import attempt, batch, make_correction, make_exercise, patch_ai

Status = UserMistakePattern.Status


class TaxonomyTests(TestCase):
    def test_deterministic_patterns_from_changed_words(self):
        cases = [("preposition", "since five years", "for five years", "since_vs_for"),
                 ("article", "She is engineer", "She is an engineer", "missing_article"),
                 ("verb_form", "didn't went", "didn't go", "base_form_after_did"),
                 ("verb_form", "can sings", "can sing", "base_form_after_modal"),
                 ("subject_verb_agreement", "she go", "she goes", "third_person_s"),
                 ("collocation", "made a photo", "took a photo", "take_vs_make"),
                 ("romanian_transfer", "I have 48 years", "I'm 48 years old", "age_with_be"),
                 ("spelling", "tomorow", "tomorrow", "spelling_other"),
                 ("vocabulary", "sensible", "sensitive", "vocabulary_other")]
        for category, original, replacement, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(derive_pattern(category, original, replacement), expected)

    def test_a_pattern_from_another_category_falls_back_to_the_category(self):
        self.assertEqual(valid_pattern("article", "since_vs_for"), "article_other")
        self.assertEqual(valid_pattern("preposition", "since_vs_for"), "since_vs_for")
        self.assertEqual(valid_pattern("other", "nonsense"), "other")


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")

    def pattern(self, key="since_vs_for"):
        return UserMistakePattern.objects.get(user=self.user, pattern_key=key)

    def test_first_mistake_creates_a_new_pattern_due_for_review(self):
        hints = record_correction_occurrences(self.user, make_correction(self.user))
        self.assertEqual(hints["since_vs_for"]["previous_count"], 0)
        pattern = self.pattern()
        self.assertEqual((pattern.status, pattern.occurrence_count, pattern.category), (Status.NEW, 1, "preposition"))
        self.assertLessEqual(pattern.next_review_at, timezone.now())
        self.assertEqual(MistakeOccurrence.objects.get().pattern_key, "since_vs_for")

    def test_repeated_mistakes_accumulate_into_a_recurring_pattern(self):
        for days_ago in (10, 5):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago,
                                                                     original="since two weeks", replacement="for two weeks"))
        hints = record_correction_occurrences(self.user, make_correction(self.user))
        self.assertEqual(hints["since_vs_for"]["previous_count"], 2)
        self.assertTrue(hints["since_vs_for"]["is_top"])
        pattern = self.pattern()
        self.assertEqual((pattern.status, pattern.occurrence_count, pattern.recent_occurrence_count), (Status.RECURRING, 3, 3))
        self.assertEqual(UserMistakePattern.objects.count(), 1)

    def test_british_preferences_never_enter_the_profile_and_invalid_patterns_are_normalised(self):
        record_correction_occurrences(self.user, make_correction(self.user, category="british_english", original="color",
                                                                 replacement="colour", pattern="american_spelling_preference",
                                                                 preference=True))
        self.assertFalse(UserMistakePattern.objects.exists())
        record_correction_occurrences(self.user, make_correction(self.user, category="article", original="is engineer",
                                                                 replacement="is an engineer", pattern="since_vs_for"))
        self.assertEqual(self.pattern("article_other").category, "article")

    def test_improving_when_practice_goes_well_and_the_mistake_is_rarer(self):
        for days_ago in (45, 44, 40):
            make_correction(self.user, days_ago=days_ago)
        record_correction_occurrences(self.user, make_correction(self.user, days_ago=20))
        call_command("rebuild_learning_profiles", stdout=io.StringIO())
        exercises = make_exercise(self.user, count=4)
        for exercise, correct in zip(exercises, (True, True, True, False)):
            attempt(self.user, exercise, correct)
        refresh_patterns(self.user)
        pattern = self.pattern()
        self.assertEqual((pattern.recent_occurrence_count, pattern.previous_period_count), (1, 3))
        self.assertEqual(pattern.status, Status.IMPROVING)

    def test_mastered_then_resurfaced(self):
        record_correction_occurrences(self.user, make_correction(self.user, days_ago=30))
        for exercise in make_exercise(self.user, count=5):
            attempt(self.user, exercise, True)
        refresh_patterns(self.user)
        self.assertEqual(self.pattern().status, Status.MASTERED)
        self.assertIsNotNone(self.pattern().last_mastered_at)
        record_correction_occurrences(self.user, make_correction(self.user))
        pattern = self.pattern()
        self.assertEqual((pattern.status, pattern.review_step), (Status.RESURFACED, 0))
        cards = insight_cards(self.user)
        self.assertIn({"kind": "resurfaced", "title": "A revenit", "label": "Since / for", "pattern_key": "since_vs_for",
                       "category": "preposition", "text": "Nu apăruse de 30 de zile, dar ai făcut din nou această greșeală."},
                      cards)

    def test_priority_puts_recent_repeated_mistakes_first(self):
        for days_ago in (50,):
            record_correction_occurrences(self.user, make_correction(self.user, category="article", original="is engineer",
                                                                     replacement="is an engineer", pattern="missing_article",
                                                                     days_ago=days_ago))
        for days_ago in (3, 2, 1):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago))
        record_correction_occurrences(self.user, make_correction(self.user, category="verb_form", original="didn't went",
                                                                 replacement="didn't go", pattern="base_form_after_did",
                                                                 days_ago=8))
        self.assertEqual(top_pattern_keys(self.user), ["since_vs_for", "base_form_after_did", "missing_article"])
        scores = dict(UserMistakePattern.objects.values_list("pattern_key", "priority_score"))
        self.assertGreater(scores["since_vs_for"], scores["base_form_after_did"])


class ReviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")
        record_correction_occurrences(self.user, make_correction(self.user))
        self.pattern = UserMistakePattern.objects.get()
        self.now = timezone.now()

    def test_success_moves_the_review_later_and_failure_brings_it_closer(self):
        self.pattern.review_step = 1
        schedule_after_practice(self.pattern, 5, 5, self.now)
        self.assertEqual(self.pattern.review_step, 2)
        self.assertEqual(self.pattern.next_review_at, self.now + timedelta(days=7))
        schedule_after_practice(self.pattern, 1, 5, self.now)
        self.assertEqual((self.pattern.review_step, self.pattern.next_review_at), (0, self.now + timedelta(days=1)))
        self.pattern.review_step = 2
        schedule_after_practice(self.pattern, 3, 5, self.now)
        self.assertEqual((self.pattern.review_step, self.pattern.next_review_at), (2, self.now + timedelta(days=7)))

    def test_due_patterns(self):
        self.assertEqual(list(due_patterns(self.user)), [self.pattern])
        UserMistakePattern.objects.update(next_review_at=self.now + timedelta(days=3))
        self.assertEqual(list(due_patterns(self.user)), [])


class ExerciseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")
        for days_ago in (2, 1):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago))

    def test_stored_exercises_are_reused_without_ai(self):
        make_exercise(self.user, count=rules.TODAY_EXERCISES)
        with patch_ai(batch()) as ai:
            exercises, error = ensure_exercises(self.user, "since_vs_for", rules.TODAY_EXERCISES)
        ai.assert_not_called()
        self.assertEqual((len(exercises), error), (5, ""))
        self.assertFalse(LearningUsageEvent.objects.exists())

    def test_a_batch_is_generated_only_when_needed_and_recorded_once(self):
        make_exercise(self.user, count=1)
        with patch_ai(batch()) as ai:
            exercises, error = ensure_exercises(self.user, "since_vs_for", 5)
        ai.assert_called_once()
        self.assertEqual((len(exercises), error), (5, ""))
        generated = Exercise.objects.filter(batch_id__isnull=False)
        self.assertEqual(generated.count(), 8)
        event = LearningUsageEvent.objects.get()
        self.assertEqual((event.feature, event.status, event.model, event.prompt_version, event.total_tokens, event.provider_calls),
                         ("practice", "success", "test-model", "2026-09-learn-practice-v2", 1500, 1))
        self.assertEqual(event.estimated_cost, None)  # test-model has no price
        self.assertTrue(all(item.usage_event == event and item.prompt_version == event.prompt_version for item in generated))
        self.assertEqual(set(generated.values_list("exercise_type", flat=True)),
                         {"multiple_choice", "fill_blank", "choose_phrase"})  # Never a whole sentence to write.
        self.assertFalse(generated.filter(open_ended=True).exists())
        payload = ai.call_args.args[1]
        self.assertIn('"pattern": "since_vs_for"', payload)
        self.assertIn("since five years", payload)
        self.assertNotIn("result_data", payload)

    def test_ai_failure_falls_back_to_editorial_exercises_and_records_the_failure(self):
        with patch_ai(error="timeout"):
            exercises, error = ensure_exercises(self.user, "since_vs_for", 3)
        self.assertEqual(error, "timeout")
        self.assertTrue(exercises)
        self.assertTrue(all(item.source == "editorial" for item in exercises))
        event = LearningUsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code), ("failed", "timeout"))

    def test_an_unusable_batch_is_an_invalid_output(self):
        broken = batch(count=4)
        for item in broken.exercises:
            item.options = ["a", "b"]
            item.correct_answer = "c"
            item.exercise_type = "multiple_choice"
        with patch_ai(broken):
            _, error = ensure_exercises(self.user, "since_vs_for", 3)
        self.assertEqual(error, "invalid_output")
        self.assertEqual(LearningUsageEvent.objects.get().error_code, "invalid_output")
        self.assertFalse(Exercise.objects.filter(source="generated").exists())

    def test_deterministic_grading_never_calls_ai(self):
        choice = make_exercise(self.user)[0]
        blank = make_exercise(self.user, exercise_type="fill_blank", options=[], correct_answer="since",
                              accepted_answers=["Since"])[0]
        with patch("apps.learning.services.exercises.learning_call") as ai:
            self.assertEqual(grade(self.user, choice, "0")[:2], (True, "deterministic"))
            self.assertEqual(grade(self.user, choice, "1")[:2], (False, "deterministic"))
            self.assertEqual(grade(self.user, blank, " SINCE. ")[:2], (True, "deterministic"))
            self.assertEqual(grade(self.user, blank, "for")[:2], (False, "deterministic"))
        ai.assert_not_called()

    def test_open_answers_use_ai_only_when_no_known_answer_matches(self):
        rewrite = make_exercise(self.user, exercise_type="rewrite", options=[], open_ended=True,
                                correct_answer="I've lived here for two years.")[0]
        with patch_ai(OpenAnswerEvaluation(is_correct=True, feedback_ro="Foarte bine.", better_answer="")) as ai:
            self.assertEqual(grade(self.user, rewrite, "I've lived here for two years")[:2], (True, "deterministic"))
            ai.assert_not_called()
            self.assertEqual(grade(self.user, rewrite, "I have been living here for two years.")[:4],
                             (True, "ai", "Foarte bine.", ""))
        self.assertEqual(LearningUsageEvent.objects.get().feature, "open_answer")
        with patch_ai(error="timeout"):
            self.assertEqual(grade(self.user, rewrite, "Another answer.")[:2], (None, "unverified"))
            self.assertEqual(grade(self.user, rewrite, "Another answer.").unavailable_code, "timeout")


class DailyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")

    def test_allocation_and_today_plan_prioritise_recurring_mistakes(self):
        self.assertEqual([allocation(n) for n in range(4)], [[], [5], [3, 2], [2, 2, 1]])
        for days_ago in (3, 2, 1):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago))
        record_correction_occurrences(self.user, make_correction(self.user, category="article", original="is engineer",
                                                                 replacement="is an engineer", pattern="missing_article",
                                                                 days_ago=40))
        plan = today_plan(self.user)
        self.assertEqual([item["pattern"].pattern_key for item in plan["items"]], ["since_vs_for", "missing_article"])
        self.assertEqual((plan["total"], [item["count"] for item in plan["items"]]), (5, [3, 2]))
        self.assertEqual(plan["reason"], "Le-am ales din greșelile pe care le-ai repetat în ultima perioadă.")

    def test_completing_a_session_schedules_the_next_review(self):
        record_correction_occurrences(self.user, make_correction(self.user))
        make_exercise(self.user, count=5)
        with patch_ai(batch()) as ai:
            session, error = start_session(self.user, "pattern", "since_vs_for")
        ai.assert_not_called()
        self.assertEqual((len(session.exercise_ids), error), (5, ""))
        for exercise in Exercise.objects.filter(pk__in=session.exercise_ids):
            attempt(self.user, exercise, True).__class__.objects.filter(exercise=exercise).update(session=session)
        complete_session(session)
        pattern = UserMistakePattern.objects.get()
        self.assertEqual(pattern.review_step, 1)
        self.assertGreater(pattern.next_review_at, timezone.now() + timedelta(days=2))
        self.assertIsNotNone(PracticeSession.objects.get().completed_at)


class InsightTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")

    def test_new_users_see_onboarding_not_trends(self):
        self.assertEqual(learning_overview(self.user)["stage"], "empty")
        record_correction_occurrences(self.user, make_correction(self.user))
        overview = learning_overview(self.user)
        self.assertEqual((overview["stage"], overview["repeated_change"]), ("early", None))

    def test_improved_persistent_and_almost_solved_cards_from_data(self):
        for days_ago in (50, 48, 46, 44):
            make_correction(self.user, days_ago=days_ago)
        make_correction(self.user, days_ago=5)
        for days_ago in (6, 4, 2):
            make_correction(self.user, category="article", original="is engineer", replacement="is an engineer",
                            pattern="missing_article", days_ago=days_ago)
        for days_ago in (70, 68, 66):
            make_correction(self.user, category="verb_form", original="didn't went", replacement="didn't go",
                            pattern="base_form_after_did", days_ago=days_ago)
        call_command("rebuild_learning_profiles", stdout=io.StringIO())
        # 5 of 7 correct is "almost solved" but below the 80 % that would make the pattern mastered.
        for exercise, correct in zip(make_exercise(self.user, pattern_key="base_form_after_did", count=7),
                                     (True, True, True, True, True, False, False)):
            attempt(self.user, exercise, correct)
        refresh_patterns(self.user)
        texts = {card["kind"]: card["text"] for card in insight_cards(self.user)}
        self.assertEqual(texts["improved"], "Cu 75% mai puține greșeli repetate decât în perioada anterioară.")
        self.assertEqual(texts["persistent"], "Ai făcut această greșeală de 3 ori în ultimele 30 de zile.")
        self.assertEqual(texts["almost"], "Ai răspuns corect la 5 din ultimele 7 exerciții.")
        overview = learning_overview(self.user)
        self.assertEqual(overview["stage"], "ready")
        self.assertEqual(overview["exercises_30"], 7)

        # "Ce s-a schimbat" draws its charts from the same data.
        cards = change_cards(self.user, insight_cards(self.user))
        self.assertEqual([(card["kind"], card["wide"]) for card in cards],
                         [("improved", False), ("persistent", False), ("almost", True)])
        self.assertEqual(cards[0]["trend"]["counts"], [1, 3, 0, 0, 0, 0, 0, 1])  # Weeks 7–6 ago, then last week.
        persistent = cards[1]["trend"]
        self.assertEqual(persistent["counts"], [0, 0, 0, 0, 0, 0, 0, 3])
        self.assertTrue(persistent["line"].startswith("M8.0,70.0 C"))
        self.assertEqual((persistent["end_x"], persistent["end_y"]), ("192.0", "14.0"))
        self.assertTrue(persistent["area"].endswith("L192.0,80 L8.0,80 Z"))
        almost = cards[2]
        self.assertEqual((almost["percent"], almost["dots"], almost["dots_correct"]),
                         (71, [True, True, True, False, False], 3))  # 5 of the last 7; the last 5 oldest first.
        self.assertEqual(almost["ring_length"], f"{2 * math.pi * 42 * 0.71:.2f}")


class GenerationContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")
        record_correction_occurrences(self.user, make_correction(self.user))

    def test_the_pattern_and_recent_mistakes_are_the_whole_generation_context(self):
        payload = generation_payload(self.user, "since_vs_for")
        self.assertEqual(set(payload), {"count", "pattern", "pattern_hint", "category", "recent_mistakes"})
        self.assertEqual(payload["pattern"], "since_vs_for")
        self.assertEqual(payload["recent_mistakes"], [{"wrong": "since five years", "correct": "for five years"}])


class RebuildCommandTests(TestCase):
    def test_rebuild_is_idempotent_and_never_changes_corrections(self):
        user = User.objects.create_user("ana", "ana@example.com", password="pass")
        make_correction(user, pattern="")
        make_correction(user, category="article", original="is engineer", replacement="is an engineer", pattern="missing_article")
        before = list(GrammarCorrection.objects.values_list("pk", "original", "replacement", "category"))
        with patch("apps.learning.services.ai.parse_response") as ai:
            out = io.StringIO()
            call_command("rebuild_learning_profiles", stdout=out)
            call_command("rebuild_learning_profiles", stdout=io.StringIO())
        ai.assert_not_called()
        self.assertIn("Created 2 mistake occurrences", out.getvalue())
        self.assertEqual(MistakeOccurrence.objects.count(), 2)
        self.assertEqual(set(UserMistakePattern.objects.values_list("pattern_key", flat=True)),
                         {"since_vs_for", "missing_article"})
        self.assertEqual(list(GrammarCorrection.objects.values_list("pk", "original", "replacement", "category")), before)
