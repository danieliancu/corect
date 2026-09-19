from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.testing import make_pro
from apps.analytics.models import LearningUsageEvent
from apps.assistant.models import RateBucket, SubmissionClaim
from apps.core.plans import PRO_GROUP
from apps.learning.models import ExerciseAttempt, PracticeSession
from apps.learning.schemas import OpenAnswerEvaluation
from apps.learning.services.ai import LearningAIUnavailable, learning_call
from apps.learning.services.exercises import ensure_exercises, grade
from apps.learning.services.guardrails import (BUDGET_EXHAUSTED, QUOTA_EXHAUSTED, RATE_LIMIT, LearningLimitReached,
                                               reserve_learning_call)
from config.env_settings import signed_in_limits
from .helpers import batch, make_correction, make_exercise, patch_ai

LIMITS = {"LEARNING_AI_RATE_LIMIT_MINUTE": 100, "LEARNING_AI_DAY_LIMITS": {"anonymous": 0, "free": 3, "pro": 5},
          "LEARNING_AI_GLOBAL_DAY_CALLS": 1000, "LEARNING_AI_GLOBAL_DAY_COST_USD": Decimal("10")}


def call(user):
    return learning_call(user=user, feature="practice", prompt="p", prompt_version="v", payload={}, schema=None)


@override_settings(**LIMITS)
class LearningGuardrailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", "pass")  # Free: its own daily AI limit
        self.other = User.objects.create_user("bob", "bob@example.com", "pass")

    def refused(self, user, code):
        with self.assertRaises(LearningLimitReached) as caught:
            reserve_learning_call(user)
        self.assertEqual(caught.exception.code, code)

    def test_per_account_daily_limit_under_at_and_over(self):
        for _ in range(3):  # Under the limit, then exactly at it.
            reserve_learning_call(self.user)
        self.refused(self.user, QUOTA_EXHAUSTED)
        reserve_learning_call(self.other)  # Another account is unaffected.

    def test_pro_accounts_get_their_own_ceiling(self):
        Group.objects.get_or_create(name=PRO_GROUP)[0].user_set.add(self.user)
        for _ in range(5):
            reserve_learning_call(self.user)
        self.refused(self.user, QUOTA_EXHAUSTED)

    @override_settings(LEARNING_AI_RATE_LIMIT_MINUTE=2)
    def test_per_minute_limit(self):
        reserve_learning_call(self.user)
        reserve_learning_call(self.user)
        self.refused(self.user, RATE_LIMIT)

    @override_settings(LEARNING_AI_GLOBAL_DAY_CALLS=2)
    def test_service_wide_call_cap_covers_every_account(self):
        reserve_learning_call(self.user)
        reserve_learning_call(self.other)
        self.refused(self.user, BUDGET_EXHAUSTED)

    def test_a_refusal_in_a_later_window_undoes_the_earlier_ones(self):
        with override_settings(LEARNING_AI_GLOBAL_DAY_CALLS=1):
            reserve_learning_call(self.other)
            self.refused(self.user, BUDGET_EXHAUSTED)
        # The user's minute and day counters were rolled back with the refusal.
        self.assertFalse(RateBucket.objects.filter(key__startswith=f"learn-ai:user:{self.user.pk}:").exclude(count=0)
                         .exists())

    def test_service_wide_spend_cap(self):
        LearningUsageEvent.objects.create(audience="registered", user=self.other, feature="practice", status="success",
                                          provider_calls=1, estimated_cost=Decimal("9.99"))
        reserve_learning_call(self.user)
        LearningUsageEvent.objects.create(audience="registered", user=self.other, feature="practice", status="success",
                                          provider_calls=1, estimated_cost=Decimal("0.01"))
        self.refused(self.user, BUDGET_EXHAUSTED)

    def test_yesterdays_spend_does_not_count(self):
        LearningUsageEvent.objects.create(audience="registered", user=self.other, feature="practice", status="success",
                                          provider_calls=1, estimated_cost=Decimal("50"),
                                          created_at=timezone.now() - timedelta(days=2))
        reserve_learning_call(self.user)

    def test_refused_calls_never_reach_the_provider_and_are_recorded(self):
        with patch_ai(batch()) as ai:
            for _ in range(3):
                call(self.user)
            with self.assertRaises(LearningAIUnavailable) as caught:
                call(self.user)
        self.assertEqual(caught.exception.code, QUOTA_EXHAUSTED)
        self.assertEqual(ai.call_count, 3)
        refused = LearningUsageEvent.objects.get(status="rejected")
        self.assertEqual((refused.error_code, refused.total_tokens, refused.estimated_cost, refused.provider_calls),
                         (QUOTA_EXHAUSTED, 0, Decimal(0), 0))

    def test_failed_provider_calls_still_count(self):
        with override_settings(LEARNING_AI_DAY_LIMITS={"anonymous": 0, "free": 2, "pro": 2}), patch_ai(error="timeout"):
            for _ in range(2):
                with self.assertRaises(LearningAIUnavailable):
                    call(self.user)
            with self.assertRaises(LearningAIUnavailable) as caught:
                call(self.user)
        self.assertEqual(caught.exception.code, QUOTA_EXHAUSTED)

    def test_counters_unavailable_means_no_provider_call(self):
        with patch("apps.learning.services.ai.reserve_learning_call", side_effect=DatabaseError), patch_ai(batch()) as ai:
            with self.assertRaises(LearningAIUnavailable) as caught:
                call(self.user)
        self.assertEqual(caught.exception.code, "database_unavailable")
        ai.assert_not_called()

    def test_concurrent_requests_cannot_pass_the_limit(self):
        # The counter is one conditional UPDATE per window: whatever the interleaving, the row never exceeds the limit.
        with override_settings(LEARNING_AI_DAY_LIMITS={"anonymous": 0, "free": 1, "pro": 1}):
            reserve_learning_call(self.user)
            bucket = RateBucket.objects.get(key__contains=":day:", key__startswith=f"learn-ai:user:{self.user.pk}")
            self.refused(self.user, QUOTA_EXHAUSTED)
            bucket.refresh_from_db()
            self.assertEqual(bucket.count, 1)

    def test_limit_settings_are_validated(self):
        self.assertEqual(signed_in_limits("X", (40, 200), {}), {"anonymous": 0, "free": 40, "pro": 200})
        for raw in ("0,10", "10", "20,10", "a,b"):
            with self.subTest(raw=raw), self.assertRaises(ImproperlyConfigured):
                signed_in_limits("X", (40, 200), {"X": raw})


@override_settings(**{**LIMITS, "LEARNING_AI_DAY_LIMITS": {"anonymous": 0, "free": 0, "pro": 0}})
class LearningFallbackTests(TestCase):
    """With every learning AI call refused, learners still practise with stored material."""

    def setUp(self):
        self.user = make_pro(User.objects.create_user("ana", "ana@example.com", "pass"))
        self.client.force_login(self.user)

    def test_generation_falls_back_to_editorial_exercises(self):
        with patch_ai(batch()) as ai:
            chosen, error = ensure_exercises(self.user, "since_vs_for", 3)
        ai.assert_not_called()
        self.assertEqual(error, QUOTA_EXHAUSTED)
        self.assertTrue(chosen)
        self.assertEqual({item.source for item in chosen}, {"editorial"})

    def test_practice_start_explains_the_limit(self):
        make_correction(self.user)
        with patch_ai(batch()):
            response = self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "since_vs_for"},
                                        follow=True)
        self.assertContains(response, "Nu mai putem genera exerciții noi azi, așa că am folosit exerciții pregătite deja.")

    def test_open_answers_stay_unverified_with_a_reason(self):
        rewrite = make_exercise(self.user, exercise_type="rewrite", options=[], open_ended=True,
                                correct_answer="I've lived here for two years.")[0]
        with patch_ai(OpenAnswerEvaluation(is_correct=True, feedback_ro="Bine.", better_answer="")) as ai:
            result = grade(self.user, rewrite, "Something else entirely.")
        ai.assert_not_called()
        self.assertEqual((result.is_correct, result.graded_by, result.unavailable_code),
                         (None, "unverified", QUOTA_EXHAUSTED))
        session = PracticeSession.objects.create(user=self.user, kind="pattern", pattern_key="since_vs_for",
                                                 exercise_ids=[rewrite.pk], exercise_patterns=["since_vs_for"],
                                                 started_at=timezone.now())
        with patch_ai(OpenAnswerEvaluation(is_correct=True, feedback_ro="Bine.", better_answer="")):
            response = self.client.post(f"/learn/practice/{session.pk}/", {"action": "answer",
                                                                            "answer": "Something else entirely."})
        self.assertContains(response, "Verificarea automată a răspunsurilor libere este oprită pentru azi.")


@override_settings(**LIMITS)
class DuplicateAnswerTests(TestCase):
    def setUp(self):
        self.user = make_pro(User.objects.create_user("ana", "ana@example.com", "pass"))
        self.client.force_login(self.user)
        self.exercise = make_exercise(self.user, exercise_type="rewrite", options=[], open_ended=True,
                                      correct_answer="I've lived here for two years.")[0]
        self.session = PracticeSession.objects.create(user=self.user, kind="pattern", pattern_key="since_vs_for",
                                                      exercise_ids=[self.exercise.pk],
                                                      exercise_patterns=["since_vs_for"], started_at=timezone.now())

    def answer(self, text="Another good answer."):
        return self.client.post(f"/learn/practice/{self.session.pk}/", {"action": "answer", "answer": text})

    def test_a_repeated_answer_is_graded_and_counted_once(self):
        with patch_ai(OpenAnswerEvaluation(is_correct=True, feedback_ro="Bine.", better_answer="")) as ai:
            self.answer()
            # A second tab or a double click that arrives before the page reloads.
            ExerciseAttempt.objects.all().delete()
            response = self.answer()
        self.assertEqual(ai.call_count, 1)
        self.assertContains(response, "Răspunsul tău a fost deja trimis.")
        self.session.refresh_from_db()
        self.assertEqual(self.session.correct_count, 1)

    def test_an_invalid_choice_can_be_answered_again(self):
        choice = make_exercise(self.user)[0]
        self.session.exercise_ids = [choice.pk]
        self.session.save()
        self.assertContains(self.answer("7"), "Alege unul dintre răspunsuri.")
        self.assertFalse(SubmissionClaim.objects.exists())
        self.assertContains(self.answer("0"), "Corect!")
