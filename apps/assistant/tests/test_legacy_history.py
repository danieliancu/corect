"""History saved before "Vreau să sune natural!" stays readable, and its stored JSON is never rewritten."""
import copy

from django.contrib.auth.models import User
from django.test import TestCase

from apps.assistant.models import AssistantRequest
from .examples import TRANSLATION_CASES, correction_result, translation_result

LEGACY_CORRECTION = correction_result().model_dump()
LEGACY_INTO_ENGLISH = translation_result().model_dump()
LEGACY_INTO_ROMANIAN = translation_result(TRANSLATION_CASES[2]).model_dump()


class LegacyHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", password="test-password")
        self.client.force_login(self.user)
        self.entries = {
            "correction": self.saved("correction", "en", LEGACY_CORRECTION, LEGACY_CORRECTION["corrected_text"]),
            "into_english": self.saved("translation", "ro", LEGACY_INTO_ENGLISH, LEGACY_INTO_ENGLISH["translated_text"]),
            "into_romanian": self.saved("translation", "en", LEGACY_INTO_ROMANIAN, LEGACY_INTO_ROMANIAN["translated_text"]),
        }

    def saved(self, kind, language, result, result_text):
        return AssistantRequest.objects.create(
            user=self.user, request_type=kind, original_text=result["original_text"], result_text=result_text,
            detected_language=language, model_used="gpt-5.6-luna", prompt_version="2026-09-v6", status="success",
            result_data=copy.deepcopy(result))

    def test_history_lists_every_legacy_entry_with_its_label(self):
        html = self.client.get("/history/").content.decode()
        for label in ("Engleză", "Română → engleză", "Engleză → română"):
            self.assertIn(f'<span class="pill">{label}</span>', html)
        summary = html[html.index('class="history-day-count"'):]
        summary = summary[:summary.index("</span>")]
        for part in ("1 text în engleză", "1 text din română", "1 text în română"):
            self.assertIn(part, summary)

    def test_each_legacy_result_renders_and_its_json_is_unchanged(self):
        expectations = {
            "correction": ("<h1>Engleză</h1>", '<h2 id="corrected-title">Engleza ta, corectată</h2>', 1),
            "into_english": ("<h1>Română → engleză</h1>", "<h2>În engleză britanică</h2>", 1),
            "into_romanian": ("<h1>Engleză → română</h1>", "<h2>În română</h2>", 0),
        }
        for name, (title, heading, speakers) in expectations.items():
            with self.subTest(name=name):
                item = self.entries[name]
                html = self.client.get(f"/history/{item.pk}/").content.decode()
                self.assertIn(title, html)
                self.assertIn(heading, html)
                self.assertEqual(html.count("data-speech-token="), speakers)
                self.assertEqual(AssistantRequest.objects.get(pk=item.pk).result_data, item.result_data)
