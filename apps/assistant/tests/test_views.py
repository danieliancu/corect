import re
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import formats, timezone

from apps.analytics.models import UsageEvent
from apps.assistant.languages import unsupported_message
from apps.assistant.models import AssistantRequest, GrammarCorrection, RateBucket, SubmissionClaim
from apps.assistant.services.openai_client import AssistantError
from .examples import (CORRECTION_CASES, ROMANIAN_CASES, correction_result, english_raw, naturalized_english,
                       naturalized_romanian, romanian_raw, translation_result)
from .provider import USAGE, ProviderMock, moderation

SERVER_TIMING = re.compile(r"^[a-z_]+;dur=\d+(, [a-z_]+;dur=\d+)*$")


class EndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ana", password="test-password")
        self.other = User.objects.create_user(username="other", password="test-password")
        self.naturalize = patch("apps.assistant.views.NaturalizeService.naturalize",
                                return_value=naturalized_english()).start()
        self.addCleanup(patch.stopall)

    def post(self, text=None, token=None, path="/naturalize/", **kwargs):
        return self.client.post(path, {"text": text if text is not None else correction_result().original_text,
                                       "submission_token": token or uuid4()}, **kwargs)

    def test_homepage_has_one_natural_english_action_and_no_fake_results(self):
        response = self.client.get("/")
        html = response.content.decode()
        form = html[html.index('<form id="assistant-form"'):html.index("</form>")]
        self.assertIn('action="/naturalize/#result"', form)
        self.assertEqual(form.count('type="submit"'), 1)
        self.assertIn('<span class="button-label">Vreau să sune natural!</span>', form)
        self.assertIn('placeholder="Scrie în română sau engleză…"', form)
        self.assertIn("Scrie sau vorbește. Corect.uk îl transformă în engleză britanică naturală.", form)
        self.assertIn('data-action-label="Vreau să sune natural!"', form)
        self.assertIn('data-trailing-ms="', form)
        self.assertContains(response, 'maxlength="2000"')
        self.assertContains(response, "greșelile reparate și explicate")
        for stale in ("Corectare", "Traducere", "correct-button", "translate-icon", "assistant/correct",
                      "assistant/translate", "formaction"):
            self.assertNotIn(stale, html)
        self.assertNotContains(response, "didn&#x27;t go")
        self.naturalize.assert_not_called()

    def test_anonymous_english_has_no_saved_content(self):
        response = self.post()
        self.assertContains(response, "După did/didn")
        self.assertContains(response, "Engleza ta, corectată")
        self.assertFalse(AssistantRequest.objects.exists())
        self.assertFalse(GrammarCorrection.objects.exists())
        self.assertEqual(SubmissionClaim.objects.count(), 1)

    def test_english_is_saved_as_a_correction_with_its_mistakes(self):
        self.client.force_login(self.user)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        entry = AssistantRequest.objects.get()
        self.assertEqual((entry.user, entry.request_type, entry.detected_language, entry.model_used),
                         (self.user, "correction", "en", "test-model"))
        self.assertEqual(entry.result_data, correction_result().model_dump())
        self.assertEqual(entry.corrections.count(), 1)

    def test_romanian_is_saved_as_a_translation_without_grammar_mistakes(self):
        self.naturalize.return_value = naturalized_romanian()
        for authenticated in (False, True):
            if authenticated:
                self.client.force_login(self.user)
            response = self.post(translation_result().original_text, HTTP_HX_REQUEST="true")
            self.assertContains(response, "<h2>În engleză britanică</h2>")
            self.assertContains(response, "I&#x27;m sorry I couldn&#x27;t get here earlier.")
            self.assertNotContains(response, "Greșit")
            self.assertNotContains(response, "tradus")
        entry = AssistantRequest.objects.get()
        self.assertEqual((entry.request_type, entry.detected_language, entry.result_text),
                         ("translation", "ro", translation_result().translated_text))
        self.assertFalse(GrammarCorrection.objects.exists())

    def test_previous_two_action_endpoints_are_gone(self):
        for path in ("/assistant/correct/", "/assistant/translate/"):
            self.assertEqual(self.post(path=path).status_code, 404)
        self.naturalize.assert_not_called()

    def test_htmx_partial_refreshes_submission_token(self):
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, 'id="result-actions" class="result-actions" hx-swap-oob="true"')
        self.assertContains(response, "Autentificare / Creează cont")
        self.assertContains(response, "data-new-text>Text nou</a>")
        self.assertNotContains(response, "<!doctype")

    def test_invalid_input_never_calls_services(self):
        for text in ("", "  \n ", "x" * 2001):
            self.assertEqual(self.post(text=text).status_code, 400)
        self.naturalize.assert_not_called()
        self.assertFalse(RateBucket.objects.exists())

    def test_browser_line_breaks_are_normalised_before_services(self):
        self.post(text="First line.\r\nSecond line.\rThird line.\r\n")
        self.naturalize.assert_called_once_with("First line.\nSecond line.\nThird line.")

    def test_only_text_and_token_are_read_and_no_operation_can_be_chosen(self):
        self.post(kind="translation", operation="translation", language="ro")
        self.naturalize.assert_called_once_with(correction_result().original_text)

    def test_invalid_or_missing_token(self):
        self.assertEqual(self.post(token="invalid").status_code, 400)
        self.assertEqual(self.client.post("/naturalize/", {"text": "Hello"}).status_code, 400)
        self.naturalize.assert_not_called()

    def test_get_never_triggers_ai(self):
        self.assertEqual(self.client.get("/naturalize/").status_code, 405)
        self.naturalize.assert_not_called()

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/naturalize/", {"text": "Hi", "submission_token": uuid4()}).status_code, 403)
        self.naturalize.assert_not_called()

    def test_duplicate_is_rejected_before_provider(self):
        token = uuid4()
        self.assertEqual(self.post(token=token).status_code, 200)
        self.assertEqual(self.post(token=token).status_code, 409)
        self.naturalize.assert_called_once()
        self.assertEqual(list(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    @override_settings(RATE_LIMIT_MINUTE=1)
    def test_rate_limit(self):
        self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.naturalize.assert_called_once()

    @override_settings(RATE_LIMIT_DAY=1)
    def test_daily_rate_limit(self):
        self.post()
        self.assertEqual(self.post().status_code, 429)
        self.naturalize.assert_called_once()

    def test_failure_before_the_language_is_known_is_saved_unclassified_without_text(self):
        self.client.force_login(self.user)
        self.naturalize.side_effect = AssistantError("timeout")
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertContains(response, "Ceva nu a mers", status_code=503)
        entry = AssistantRequest.objects.get()
        self.assertEqual((entry.status, entry.request_type, entry.original_text, entry.result_data, entry.error_code),
                         ("failed", "unclassified", "", {}, "timeout"))

    def test_failure_after_classification_keeps_its_operation(self):
        self.client.force_login(self.user)
        error = AssistantError("invalid_snippet")
        error.source_language = "en"
        self.naturalize.side_effect = error
        self.assertEqual(self.post().status_code, 503)
        entry = AssistantRequest.objects.get()
        self.assertEqual((entry.status, entry.request_type, entry.detected_language), ("failed", "correction", "en"))
        event = UsageEvent.objects.get()
        self.assertEqual((event.request_type, event.source_language, event.status), ("correction", "en", "failed"))

    def test_an_unsupported_language_is_explained(self):
        error = AssistantError("language", unsupported_message())
        error.source_language = "other"
        self.naturalize.side_effect = error
        response = self.post(text="Bonjour, je voudrais un café.", HTTP_HX_REQUEST="true")
        self.assertContains(response, "Scrie în engleză sau română", status_code=422)
        event = UsageEvent.objects.get()
        self.assertEqual((event.request_type, event.source_language), ("unclassified", "other"))

    def test_private_pages_and_history_ownership(self):
        for path in ("/history/", "/mistakes/", "/progress/", "/practice/", "/accounts/profile/"):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.client.force_login(self.user)
        self.post()
        entry = AssistantRequest.objects.get()
        self.assertEqual(self.client.get(f"/history/{entry.pk}/").status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/history/{entry.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/history/"), "details-pill")

    def test_history_is_grouped_by_collapsible_days(self):
        self.client.force_login(self.user)
        now = timezone.now()

        def entry(days_ago, kind="correction", language="en"):
            item = AssistantRequest.objects.create(user=self.user, request_type=kind, original_text=f"text {days_ago}",
                result_text="result", detected_language=language, model_used="test", prompt_version="test",
                status="success")
            AssistantRequest.objects.filter(pk=item.pk).update(created_at=now - timedelta(days=days_ago))

        entry(0)
        entry(0, "translation", "ro")
        entry(1)
        entry(3)
        html = self.client.get("/history/").content.decode()
        self.assertEqual(html.count('<details class="history-day"'), 3)
        self.assertEqual(html.count('<details class="history-day" open>'), 1)  # Only the most recent day starts open.
        self.assertLess(html.index('<details class="history-day" open>'), html.index(">Azi</time>"))
        self.assertLess(html.index(">Azi</time>"), html.index(">Ieri</time>"))
        self.assertIn("1 text în engleză · 1 text din română", html)
        self.assertIn('<span class="pill">Engleză</span>', html)
        self.assertIn('<span class="pill">Română → engleză</span>', html)
        self.assertIn(formats.date_format(timezone.localdate() - timedelta(days=3), "j F Y"), html)
        for days_ago in range(4, 12):
            entry(days_ago)
        response = self.client.get("/history/")  # 11 days: 7 on the first page, 4 on the second, never split.
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual([len(day["entries"]) for day in response.context["days"]], [2, 1, 1, 1, 1, 1, 1])
        self.assertEqual(len(self.client.get("/history/?page=2").context["days"]), 4)

    def test_output_and_original_are_escaped(self):
        malicious = "<script>alert('x')</script>"
        result = correction_result()
        result.corrected_text = malicious
        self.naturalize.return_value = naturalized_english()._replace(result=result) if hasattr(
            naturalized_english(), "_replace") else type(naturalized_english())("correction", "en", result)
        response = self.post(text=malicious)
        self.assertNotContains(response, malicious)
        self.assertContains(response, "&lt;script&gt;")

    def test_learning_pages_and_preferences_excluded(self):
        self.client.force_login(self.user)
        self.post()
        self.post()
        entry = AssistantRequest.objects.first()
        GrammarCorrection.objects.create(request=entry, original="color", replacement="colour", category="british_english",
            severity="suggestion", explanation_ro="Preferință britanică", is_british_preference=True)
        response = self.client.get("/progress/")
        self.assertEqual(response.context["mistake_count"], 2)
        self.assertEqual((response.context["english_count"], response.context["into_english_count"]), (2, 0))
        self.assertContains(response, "Texte în engleză")
        self.assertEqual(len(response.context["trend"]), 30)
        self.assertEqual(sum(day["total"] for day in response.context["trend"]), 2)
        self.assertEqual(sum(day["total"] for day in response.context["trend_data"]), 2)
        self.assertContains(response, 'id="trend-data"')
        self.assertContains(response, "vendor/chart.umd.min.js")
        response = self.client.get("/mistakes/")
        self.assertContains(response, "de 2 ori")
        response = self.client.get("/practice/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Întrebare rapidă")

    def test_account_signup_login_profile_logout(self):
        response = self.client.post("/accounts/signup/", {"username": "new-learner", "email": "learner@example.com",
            "password1": "A-unique-pass-9431", "password2": "A-unique-pass-9431", "accept_legal": "on"})
        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/accounts/profile/").status_code, 200)
        self.client.post("/accounts/profile/", {"action": "profile", "username": "new-learner", "email": "updated@example.com"})
        self.assertEqual(User.objects.get(username="new-learner").email, "updated@example.com")
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)
        self.assertRedirects(self.client.post("/accounts/logout/"), "/")
        self.assertRedirects(self.client.post("/accounts/login/", {"username": "new-learner", "password": "A-unique-pass-9431"}), "/")

    def test_profile_changes_username_and_password(self):
        self.client.force_login(self.user)
        profile = lambda **data: self.client.post("/accounts/profile/", {"action": "profile", "email": "", **data})
        password = lambda old: self.client.post("/accounts/profile/", {"action": "password", "old_password": old,
            "new_password1": "A-new-pass-5823", "new_password2": "A-new-pass-5823"})
        self.assertContains(profile(username="other"), "există deja")
        self.assertRedirects(profile(username="ana-maria"), "/accounts/profile/")
        self.assertEqual(User.objects.get(pk=self.user.pk).username, "ana-maria")
        self.assertEqual(password("wrong-password").status_code, 200)
        self.assertRedirects(password("test-password"), "/accounts/profile/")
        self.assertContains(self.client.get("/accounts/profile/"), "Autentificat ca ana-maria")
        self.client.logout()
        self.assertTrue(self.client.login(username="ana-maria", password="A-new-pass-5823"))

    def test_login_with_username_or_email(self):
        self.user.email = "ana@example.com"
        self.user.save()
        for identifier in ("ana", "ANA@example.com"):
            with self.subTest(identifier=identifier):
                self.assertRedirects(self.client.post("/accounts/login/", {"username": identifier, "password": "test-password"}), "/")
                self.client.logout()
        self.assertContains(self.client.post("/accounts/login/", {"username": "ana@example.com", "password": "wrong"}), "nume de utilizator sau email")
        self.other.email = "ana@example.com"
        self.other.save()
        response = self.client.post("/accounts/login/", {"username": "ana@example.com", "password": "test-password"})
        self.assertContains(response, "nume de utilizator sau email")
        self.assertContains(self.client.get("/accounts/login/"), "Nume de utilizator sau email")

    def test_mistake_category_page_lists_only_own_mistakes(self):
        self.assertEqual(self.client.get("/mistakes/verb_form/").status_code, 302)
        self.client.force_login(self.user)
        self.post()
        entry = AssistantRequest.objects.get()
        GrammarCorrection.objects.create(request=entry, original="color", replacement="colour", category="british_english",
            severity="suggestion", explanation_ro="Preferință britanică", is_british_preference=True)
        self.assertContains(self.client.get("/mistakes/"), 'href="/mistakes/verb_form/"')
        self.assertContains(self.client.get("/progress/"), 'href="/mistakes/verb_form/"')
        for path in ("/mistakes/", "/progress/"):  # The count sits in a red badge beside the name; only the arrow is on the right.
            self.assertContains(self.client.get(path), '<span class="category-name"><span class="category-badge">1</span>Forma verbului</span>'
                                '<span class="category-arrow" aria-hidden="true">→</span>', html=False)
        response = self.client.get("/mistakes/verb_form/")
        self.assertContains(response, "1 greșeală înregistrată în această categorie")
        self.assertContains(response, "didn&#x27;t go")
        self.assertContains(response, f'href="/history/{entry.pk}/"')
        self.assertNotContains(response, 'href="/practice/"')  # No long "Exersează" button under the list.
        for path in ("/mistakes/british_english/", "/mistakes/nonsense/"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get("/mistakes/verb_form/"), "didn&#x27;t go")

    def test_repeated_mistakes_open_their_examples_and_pages_use_short_titles(self):
        self.client.force_login(self.user)
        response = self.post()
        self.assertContains(response, "Engleza ta, corectată")
        self.assertNotContains(response, "Greșeli reparate")
        entry = AssistantRequest.objects.get()
        for original, replacement in (("Didn't went", "didn't go"), ("goed", "went")):
            GrammarCorrection.objects.create(request=entry, original=original, replacement=replacement, category="verb_form",
                                             severity="major", explanation_ro="Forma de bază după did.")
        page = self.client.get("/mistakes/")
        self.assertContains(page, "<h1>Greșeli</h1>", html=False)
        self.assertContains(page, '<h2 id="exercises-title">Exerciții</h2>', html=False)
        self.assertContains(page, 'href="/mistakes/verb_form/?original=didn%27t+went&amp;replacement=didn%27t+go"')
        examples = self.client.get("/mistakes/verb_form/", {"original": "didn't went", "replacement": "didn't go"})
        self.assertContains(examples, "2 greșeli înregistrate de tipul")
        self.assertContains(examples, "Toate greșelile din categorie")
        self.assertNotContains(examples, "goed")
        self.assertContains(self.client.get("/mistakes/verb_form/"), "goed")
        for path, title in (("/progress/", "<h1>Progres</h1>"), ("/history/", "<h1>Istoric</h1>")):
            self.assertContains(self.client.get(path), title, html=False)
        self.assertNotContains(self.client.get("/accounts/profile/"), "URMĂTORUL TĂU PAS")

    def test_empty_text_asks_to_write_or_speak(self):
        for text in ("", "   \n "):
            with self.subTest(text=text):
                response = self.post(text=text, HTTP_HX_REQUEST="true")
                self.assertContains(response, "Ca să continuăm, scrie ceva în casetă sau apasă microfonul și vorbește.",
                                    status_code=400)
                self.assertNotContains(response, "Acest câmp este obligatoriu", status_code=400)
                self.assertNotContains(response, "Textul tău e tot aici", status_code=400)
        too_long = self.post(text="a" * 2001)
        self.assertContains(too_long, "Textul tău e tot aici", status_code=400)
        self.naturalize.assert_not_called()

    def test_recorded_mistakes_phrase_follows_romanian_numerals(self):
        from apps.learning.templatetags.learning_labels import recorded_mistakes
        expected = {0: "0 greșeli înregistrate", 1: "1 greșeală înregistrată", 2: "2 greșeli înregistrate",
                    19: "19 greșeli înregistrate", 20: "20 de greșeli înregistrate", 101: "101 greșeli înregistrate",
                    120: "120 de greșeli înregistrate", 200: "200 de greșeli înregistrate"}
        self.assertEqual({count: recorded_mistakes(count) for count in expected}, expected)

    def test_correction_speaker_takes_the_heading_icon_place(self):
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertContains(response, 'class="speak-button speak-button--heading"', count=1)
        self.assertNotContains(response, 'class="result-check"')
        self.assertNotContains(response, '<div class="spoken-sentence"><p class="result-text corrected-sentence"')

    def test_british_english_from_romanian_has_its_speaker_left_of_the_heading(self):
        self.naturalize.return_value = naturalized_romanian()
        html = self.post(translation_result().original_text, HTTP_HX_REQUEST="true").content.decode()
        speaker = html.index('class="speak-button speak-button--heading"')
        self.assertLess(speaker, html.index("<h2>În engleză britanică</h2>"))
        self.assertEqual(html.count('class="speak-button'), 1)

    def test_server_timing_shows_only_names_and_whole_milliseconds(self):
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertRegex(response["Server-Timing"], SERVER_TIMING)
        self.assertIn("db;dur=", response["Server-Timing"])
        self.assertIn("total;dur=", response["Server-Timing"])
        event = UsageEvent.objects.get()
        self.assertIsInstance(event.duration_ms, int)

    def test_header_user_icon_follows_sign_in_state(self):
        response = self.client.get("/confidentialitate/")
        self.assertContains(response, 'class="user-link" href="/accounts/login/" aria-label="Autentificare"')
        self.assertContains(response, '<span class="nav-icon" aria-hidden="true">', count=8)
        self.client.force_login(self.user)
        response = self.client.get("/confidentialitate/")
        self.assertContains(response, 'class="user-link is-signed-in" href="/accounts/profile/" aria-label="Profil: ana"')
        self.assertContains(response, '<span class="user-initial" aria-hidden="true">A</span>')

    def test_settings_page_is_removed(self):
        self.assertEqual(self.client.get("/settings/").status_code, 404)
        for path in ("/", "/confidentialitate/"):
            response = self.client.get(path)
            self.assertNotContains(response, 'href="/settings/"')
            self.assertNotContains(response, "Setări</a>")  # Only "Setări cookie-uri" remains, in the footer.


@override_settings(CONTENT_MODERATION_ENABLED=True)
class SingleCallRoutingTests(ProviderMock, TestCase):
    """End to end through the real view and service, with only the SDK replaced."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("ana", password="test-password")
        self.client.force_login(self.user)
        self.api.moderations.create.return_value = moderation()

    def post(self, text):
        return self.client.post("/naturalize/", {"text": text, "submission_token": uuid4()}, HTTP_HX_REQUEST="true")

    def test_english_and_romanian_each_make_one_generative_call_recorded_under_their_operation(self):
        for text, output, operation, language, heading in (
                (CORRECTION_CASES[0][0], english_raw(), "correction", "en", "Engleza ta, corectată"),
                (ROMANIAN_CASES[0][0], romanian_raw(), "translation", "ro", "În engleză britanică")):
            with self.subTest(operation=operation):
                self.api.reset_mock()
                self.api.moderations.create.return_value = moderation()
                self.respond(output, usage=USAGE)
                self.assertContains(self.post(text), heading)
                self.assertEqual((self.api.responses.parse.call_count, self.api.moderations.create.call_count), (1, 1))
                event = UsageEvent.objects.latest("pk")
                self.assertEqual((event.request_type, event.source_language, event.provider_calls, event.status),
                                 (operation, language, 1, "success"))
                self.assertIsNotNone(event.provider_duration_ms)
                self.assertIsNotNone(event.moderation_duration_ms)
                self.assertEqual(AssistantRequest.objects.latest("pk").request_type, operation)
        self.assertEqual(GrammarCorrection.objects.count(), 1)  # Only the English text had a mistake.
