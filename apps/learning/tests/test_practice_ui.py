"""Practice around the exercises: the preparing overlay hooks, short-answer exercise types and the end-of-session report."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.testing import make_pro
from apps.learning.models import PracticeSession, UserMistakePattern
from apps.learning.schemas import ExerciseBatch
from apps.learning.services.exercises import clean_batch, suitable_pool
from apps.learning.services.profile import record_correction_occurrences
from apps.learning.services.report import practice_streak, verdict
from .helpers import batch, make_correction, make_exercise, patch_ai


class PreparingOverlayTests(TestCase):
    def setUp(self):
        self.user = make_pro(User.objects.create_user("ana", "ana@example.com", password="pass"))
        self.client.force_login(self.user)
        record_correction_occurrences(self.user, make_correction(self.user))

    def test_every_form_that_starts_practice_shows_the_preparing_overlay(self):
        page = self.client.get("/mistakes/").content.decode()
        self.assertIn("js/wait-screen.js", page)
        self.assertIn('action="/learn/practice/start/" data-wait="Se pregătește exercițiul…">', page)
        self.assertNotIn('action="/learn/practice/start/">', page)  # No start form without the overlay.


class ShortAnswerExerciseTests(TestCase):
    def setUp(self):
        self.user = make_pro(User.objects.create_user("ana", "ana@example.com", password="pass"))
        self.client.force_login(self.user)

    def test_generation_accepts_only_options_or_one_short_blank(self):
        items = [
            {"exercise_type": "fill_blank", "question": f"I've lived here ___ {year}.", "correct_answer": "since"}
            for year in (2017, 2018, 2019)
        ] + [
            {"exercise_type": "fill_blank", "question": "I've lived here ___ 2019.",
             "correct_answer": "I have lived here since 2019"},  # A whole sentence: not a blank.
            {"exercise_type": "fill_blank", "question": "I ___ here ___ 2019.", "correct_answer": "since"},
            {"exercise_type": "fill_blank", "question": "No gap here.", "correct_answer": "since"},
        ]
        parsed = ExerciseBatch.model_validate({"pattern": "since_vs_for", "exercises": [
            {"options": [], "accepted_answers": [], "explanation_ro": "Since.", "difficulty": 1, "uk_context": "", **item}
            for item in items]})
        kept = [(item.question, answer) for item, _, answer in clean_batch(parsed)]
        self.assertEqual([question for question, _ in kept],
                         ["I've lived here ___ 2017.", "I've lived here ___ 2018.", "I've lived here ___ 2019."])
        with self.assertRaises(ValueError):  # The structured output itself refuses sentence-writing types.
            ExerciseBatch.model_validate({"pattern": "since_vs_for", "exercises": [
                {"exercise_type": "rewrite", "question": "Rewrite it.", "options": [], "correct_answer": "x",
                 "accepted_answers": [], "explanation_ro": "Da.", "difficulty": 1, "uk_context": ""}]})

    def test_stored_sentence_exercises_are_no_longer_served(self):
        make_exercise(self.user, exercise_type="rewrite", options=[], open_ended=True)
        make_exercise(self.user, exercise_type="short_correction", options=[])
        blank = make_exercise(self.user, exercise_type="fill_blank", options=[])[0]
        self.assertEqual(list(suitable_pool(self.user, "since_vs_for")), [blank])

    def test_a_fill_blank_exercise_shows_the_gap_and_a_one_line_answer(self):
        record_correction_occurrences(self.user, make_correction(self.user))
        make_exercise(self.user, exercise_type="fill_blank", options=[], count=5)
        with patch_ai(batch()):
            self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "since_vs_for"})
        session = PracticeSession.objects.get()
        page = self.client.get(f"/learn/practice/{session.pk}/")
        self.assertContains(page, '<span class="learn-blank"><span class="sr-only">(spațiu liber)</span></span>')
        self.assertContains(page, 'class="learn-blank-input" id="answer" name="answer" type="text"')
        self.assertNotContains(page, "<textarea")
        answered = self.client.post(f"/learn/practice/{session.pk}/", {"action": "answer", "answer": "since"})
        self.assertContains(answered, '<span class="learn-blank is-filled is-correct">since</span>')


class SessionReportTests(TestCase):
    def setUp(self):
        self.user = make_pro(User.objects.create_user("ana", "ana@example.com", password="pass"))
        self.client.force_login(self.user)
        record_correction_occurrences(self.user, make_correction(self.user))
        make_exercise(self.user, count=5)

    def play(self, answers):
        with patch_ai(batch()):
            self.client.post("/learn/practice/start/", {"kind": "pattern", "pattern": "since_vs_for"})
        session = PracticeSession.objects.latest("started_at")
        for answer in answers:
            self.client.post(f"/learn/practice/{session.pk}/", {"action": "answer", "answer": answer})
            self.client.post(f"/learn/practice/{session.pk}/", {"action": "next"})
        return session, self.client.get(f"/learn/practice/{session.pk}/")

    def test_a_perfect_session_celebrates_and_offers_what_to_do_next(self):
        session, page = self.play(["0"] * 5)
        self.assertEqual(session.mastery_at_start, {"since_vs_for": 0})  # Snapshot taken when the session started.
        for text in ("Perfect!", 'aria-label="3 din 3 stele"', "Ai răspuns corect la 5 din 5 exerciții.",
                     "Progresul tău", "<strong>1</strong><span>zi la rând</span>", "Exersează din nou", 'href="/mistakes/">Înapoi la Greșeli',
                     "Vezi progresul"):
            self.assertContains(page, text)
        self.assertNotContains(page, "De reținut")
        self.assertContains(page, 'action="/learn/practice/start/" data-wait="Se pregătește exercițiul…">')  # Again, with the overlay.
        mastery = UserMistakePattern.objects.get().mastery
        if mastery:  # Practice raised it: the report shows from where to where.
            self.assertContains(page, f"0% → <strong>{mastery}%</strong> rezolvat")

    def test_wrong_answers_are_listed_with_the_correct_answer(self):
        _, page = self.play(["1", "1", "0", "0", "0"])
        self.assertContains(page, "Foarte bine!")
        self.assertContains(page, "Ai răspuns corect la 3 din 5 exerciții.")
        self.assertContains(page, "De reținut")
        self.assertContains(page, '<s lang="en-GB">for</s>')  # The option they chose, not its index.
        self.assertContains(page, '<strong lang="en-GB">since</strong>')
        self.assertContains(page, "Repetă exercițiile")

    def test_verdicts_and_streak(self):
        self.assertEqual([verdict(correct, 5)["stars"] for correct in (5, 4, 3, 2, 1, 0)], [3, 2, 2, 1, 0, 0])
        self.assertEqual(verdict(0, 0)["title"], "Sesiune încheiată")
        now = timezone.now()
        for days_ago in (0, 1, 2, 4):  # Day 3 was missed, so the streak is three.
            PracticeSession.objects.create(user=self.user, kind="today", completed_at=now - timedelta(days=days_ago))
        PracticeSession.objects.create(user=self.user, kind="today")  # Unfinished sessions do not count.
        self.assertEqual(practice_streak(self.user, now), 3)
        self.assertEqual(practice_streak(self.user, now + timedelta(days=2)), 0)
