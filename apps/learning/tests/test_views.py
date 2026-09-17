from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.analytics.models import LearningUsageEvent, UsageEvent
from apps.assistant.tests.examples import correction_result
from apps.learning.models import ExerciseAttempt, PracticeSession, UserMistakePattern
from apps.learning.services.profile import record_correction_occurrences
from .helpers import batch, make_correction, make_exercise, patch_ai


class LearningPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")
        self.client.force_login(self.user)

    def test_new_user_dashboard_is_an_onboarding_state(self):
        response = self.client.get("/learn/")
        self.assertContains(response, "<h1>Bun venit, ana</h1>", html=False)
        self.assertContains(response, "Începe să scrii sau să vorbești în engleză.")
        self.assertNotContains(response, "Zone de exersat")
        self.assertContains(response, "<h1", count=1)

    def test_dashboard_shows_today_priorities_and_stats_without_ai(self):
        for days_ago in (3, 2, 1):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago))
        with patch("apps.learning.services.ai.parse_response") as ai:
            response = self.client.get("/learn/")
        ai.assert_not_called()
        for text in ("Pentru tine azi", "5 exerciții alese din greșelile tale recente", "Since / for",
                     '<span class="learn-topic-count" aria-hidden="true">5</span>',  # The pill states its share.
                     'value="today"', "Începe cele 5 exerciții",  # The button that starts the day's session.
                     "Ce trebuie exersat", "Se repetă", "Zone de exersat", '<h3 id="activity-title">Progres</h3>','<span class="learn-plan-pill">Free</span>', "1 tipologie urmărită"):
            self.assertContains(response, text)
        for text in ("Situați", "Obiectivele tale", "Următoarele", "Continuăm de unde ai rămas", "learn-rail",
                     'aria-label="Exersează: Since / for"', 'href="/practice/"'):
            self.assertNotContains(response, text)  # No per-row buttons, no practice tab.
        self.assertContains(response, '<h2 id="now-title">Ce trebuie exersat</h2>')
        self.assertContains(response, '<a class="learn-heading-link" href="/mistakes/">Vezi toate')
        self.assertContains(self.client.get("/practice/"), 'aria-label="Exersează: Since / for"')

    def test_personalised_practice_flow_grades_in_python_and_schedules_review(self):
        record_correction_occurrences(self.user, make_correction(self.user))
        make_exercise(self.user, count=5)
        with patch_ai(batch()) as ai:
            response = self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "since_vs_for"})
            session = PracticeSession.objects.get()
            self.assertRedirects(response, f"/learn/practice/{session.pk}/")
            page = self.client.get(f"/learn/practice/{session.pk}/")
            self.assertContains(page, "Exercițiul 1 din 5")
            for _ in range(5):
                self.assertContains(self.client.post(f"/learn/practice/{session.pk}/", {"action": "answer", "answer": "0"}),
                                    "Corect!")
                self.client.post(f"/learn/practice/{session.pk}/", {"action": "next"})
        ai.assert_not_called()
        finished = self.client.get(f"/learn/practice/{session.pk}/")
        self.assertContains(finished, "Ai răspuns corect la 5 din 5 exerciții.")
        self.assertEqual(ExerciseAttempt.objects.filter(is_correct=True).count(), 5)
        self.assertEqual(UserMistakePattern.objects.get().review_step, 1)

    def test_ai_unavailable_keeps_practice_working_with_stored_exercises(self):
        record_correction_occurrences(self.user, make_correction(self.user, category="verb_form", original="didn't went",
                                                                 replacement="didn't go", pattern="base_form_after_did"))
        with patch_ai(error="timeout"):
            response = self.client.post("/learn/practice/start/", {"kind": "today"}, follow=True)
        self.assertContains(response, "am folosit exerciții pregătite deja")
        self.assertContains(response, "I didn&#x27;t ___ him yesterday.")
        self.assertEqual(self.client.get("/learn/").status_code, 200)
        self.assertEqual(self.client.get("/progress/").status_code, 200)

    def test_learners_cannot_see_each_others_sessions(self):
        record_correction_occurrences(self.user, make_correction(self.user))
        make_exercise(self.user, count=5)
        self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "since_vs_for"})
        session = PracticeSession.objects.get()
        other = User.objects.create_user("bob", "bob@example.com", password="pass")
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/learn/practice/{session.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/learn/"), "Since / for")
        self.assertNotContains(self.client.get("/progress/"), "Since / for")
        self.client.logout()
        self.assertEqual(self.client.get("/learn/").status_code, 302)

    def test_invalid_practice_requests_and_removed_pages_are_refused(self):
        self.assertEqual(self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "nope"}).status_code, 404)
        self.assertEqual(self.client.post("/learn/practice/start/", {"kind": "scenario", "scenario": "gp"}).status_code, 404)
        self.assertEqual(self.client.get("/learn/scenarios/").status_code, 404)
        self.assertEqual(self.client.post("/learn/goals/", {"goals": ["medical"]}).status_code, 404)

    def test_progress_leads_with_insights_and_keeps_the_chart(self):
        record_correction_occurrences(self.user, make_correction(self.user))
        response = self.client.get("/progress/")
        self.assertContains(response, "În ultimele 30 de zile")
        self.assertContains(response, "Începem să vedem câteva tipologii.")
        self.assertContains(response, 'id="trend-data"')
        self.assertNotContains(response, "Pe scurt")
        self.assertLess(response.content.decode().index("În ultimele 30 de zile"), response.content.decode().index("trend-chart"))
        self.assertEqual(self.client.post("/learn/coach/").status_code, 404)

    def test_what_changed_cards_show_real_trends_and_link_to_the_category(self):
        for days_ago in (3, 2, 1):
            record_correction_occurrences(self.user, make_correction(self.user, days_ago=days_ago))
        response = self.client.get("/progress/")
        for text in ("Ce s-a schimbat", "O privire rapidă asupra progresului tău din ultima perioadă.",
                     'class="learn-change is-persistent is-wide"', "Încă se repetă",
                     "Ai făcut această greșeală de 3 ori în ultimele 30 de zile.",
                     'aria-label="Apariții pe săptămână în ultimele 8 săptămâni: 0, 0, 0, 0, 0, 0, 0, 3"',
                     'href="/mistakes/preposition/" aria-label="Vezi greșelile din categoria Prepoziție"',
                     '<circle cx="192.0" cy="14.0" r="5"'):
            self.assertContains(response, text)
        self.assertContains(response, "<h1", count=1)


@override_settings(NATURALIZE_RATE_LIMIT_MINUTE=100)
class CorrectionLoopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password="pass")
        self.client.force_login(self.user)
        from apps.assistant.tests.examples import naturalized_english
        self.naturalize = patch("apps.assistant.views.NaturalizeService.naturalize",
                                return_value=naturalized_english()).start()
        self.addCleanup(patch.stopall)

    def correct(self, text=None):
        return self.client.post("/naturalize/", {"text": text or correction_result().original_text,
                                                 "submission_token": uuid4()}, HTTP_HX_REQUEST="true")

    def test_romanian_text_creates_no_mistakes_or_practice(self):
        from apps.assistant.models import GrammarCorrection
        from apps.assistant.tests.examples import naturalized_romanian
        self.naturalize.return_value = naturalized_romanian()
        for index in range(3):
            response = self.correct(f"Text în română {index}.")
            self.assertContains(response, "În engleză britanică")
            self.assertNotContains(response, "Exersează acum")
        self.assertFalse(GrammarCorrection.objects.exists())
        self.assertFalse(UserMistakePattern.objects.exists())

    def test_a_repeated_mistake_offers_practice_from_the_correction(self):
        # Three different texts with the same mistake. Sending one text again is answered from the learner's own
        # saved result (apps/assistant/services/persistence.py), so it would not count the mistake a second time.
        first = self.correct("I didn't went to work on Monday.")
        self.assertNotContains(first, "Ai mai făcut această greșeală")
        second = self.correct("I didn't went to work on Tuesday.")
        self.assertContains(second, "Ai mai făcut această greșeală o dată.")
        self.assertContains(second, 'name="pattern" value="base_form_after_did"')
        self.assertContains(second, "Exersează acum")
        third = self.correct("I didn't went to work on Wednesday.")
        self.assertContains(third, "Ai mai făcut această greșeală de 2 ori.")
        self.assertContains(third, "Pare să fie una dintre greșelile pe care le repeți cel mai des.")
        self.assertEqual(UserMistakePattern.objects.get().occurrence_count, 3)
        self.assertEqual(UsageEvent.objects.count(), 3)

    def test_anonymous_corrections_build_no_profile(self):
        self.client.logout()
        self.assertNotContains(self.correct(), "Exersează acum")
        self.assertFalse(UserMistakePattern.objects.exists())


class LearningAnalyticsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser("admin", "admin@example.com", "pass")
        self.learner = User.objects.create_user("ana", "ana@example.com", password="pass")
        self.client.force_login(self.staff)

    def learning_event(self, feature, cost, user=None):
        return LearningUsageEvent.objects.create(audience="registered", user=user or self.learner, feature=feature,
                                                 model="test-model", status="success", provider_calls=1,
                                                 total_tokens=1000, estimated_cost=Decimal(cost))

    def test_learning_ai_is_its_own_source_in_the_total_without_double_counting(self):
        UsageEvent.objects.create(audience="registered", user=self.learner, request_type="correction", status="success",
                                  provider_calls=1, total_tokens=500, estimated_cost=Decimal("0.001"))
        self.learning_event("practice", "0.002")
        self.learning_event("open_answer", "0.0005")
        response = self.client.get("/admin/analytics/?period=all")
        costs = response.context["costs"]
        self.assertEqual((costs["text"], costs["learning"], costs["total"]), (Decimal("0.001"), Decimal("0.0025"), Decimal("0.0035")))
        self.assertEqual(response.context["totals"]["requests"], 1)  # Learning calls are not text requests.
        features = {row["key"]: row for row in response.context["learning_features"]}
        self.assertEqual((features["practice"]["calls"], features["practice"]["cost"]), (1, Decimal("0.002")))
        self.assertEqual(set(features), {"practice", "open_answer"})
        self.assertEqual(response.context["learning_cost_per_learner"], Decimal("0.0025"))
        for text in ("Learning AI cost", "Learning AI", "Personalised practice",
                     "Open-answer evaluation", "$0.002000", "$0.002500", "£0.0018"):
            self.assertContains(response, text)

    def test_per_user_learning_cost_and_sorting(self):
        UsageEvent.objects.create(audience="registered", user=self.learner, request_type="correction", status="success",
                                  provider_calls=1, estimated_cost=Decimal("0.001"))
        self.learning_event("practice", "0.004")
        response = self.client.get("/admin/analytics/users/?period=all&sort=learning_cost")
        member = response.context["page_obj"][0]
        self.assertEqual((member.get_username(), member.learning_cost, member.total_cost), ("ana", Decimal("0.004"), Decimal("0.005")))
        detail = self.client.get(f"/admin/analytics/users/{self.learner.pk}/")
        self.assertEqual(detail.context["costs"]["learning"], Decimal("0.004"))
        self.assertContains(detail, "Learning AI total")

    def test_the_learning_ledger_holds_no_content(self):
        from django.db import models
        text_fields = [f.name for f in LearningUsageEvent._meta.get_fields() if isinstance(f, (models.TextField, models.JSONField))]
        self.assertEqual(text_fields, [])
