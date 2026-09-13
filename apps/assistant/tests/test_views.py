from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from apps.assistant.models import AssistantRequest, GrammarCorrection, RateBucket, SubmissionClaim
from apps.assistant.services.openai_client import AssistantError
from .examples import correction_result, translation_result


class EndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ana", password="test-password")
        self.other = User.objects.create_user(username="other", password="test-password")
        self.correction_patch = patch("apps.assistant.views.CorrectionService.correct", return_value=correction_result())
        self.translation_patch = patch("apps.assistant.views.TranslationService.translate", return_value=translation_result())
        self.correct = self.correction_patch.start()
        self.translate = self.translation_patch.start()
        self.addCleanup(self.correction_patch.stop)
        self.addCleanup(self.translation_patch.stop)

    def post(self, path="/assistant/correct/", text=None, token=None, **kwargs):
        return self.client.post(path, {"text": text if text is not None else correction_result().original_text,
                                     "submission_token": token or uuid4()}, **kwargs)

    def test_homepage_anonymous_no_calls_or_fake_results(self):
        response = self.client.get("/")
        self.assertContains(response, 'maxlength="2000"')
        self.assertContains(response, "Scrie ceva, apoi alege")
        self.assertNotContains(response, "Aici începe claritatea.")
        self.assertNotContains(response, "didn&#x27;t go")
        self.correct.assert_not_called()
        self.translate.assert_not_called()

    def test_anonymous_correction_has_no_saved_content(self):
        response = self.post()
        self.assertContains(response, "După did/didn")
        self.assertFalse(AssistantRequest.objects.exists())
        self.assertFalse(GrammarCorrection.objects.exists())
        self.assertEqual(SubmissionClaim.objects.count(), 1)

    def test_authenticated_correction_saved_with_owner(self):
        self.client.force_login(self.user)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        entry = AssistantRequest.objects.get()
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.model_used, "test-model")
        self.assertEqual(entry.result_data, correction_result().model_dump())
        self.assertEqual(entry.corrections.count(), 1)

    def test_translation_both_anonymous_and_authenticated(self):
        for authenticated in (False, True):
            if authenticated:
                self.client.force_login(self.user)
            response = self.post("/assistant/translate/", translation_result().original_text)
            self.assertContains(response, "Română → engleză britanică")
        self.assertEqual(AssistantRequest.objects.count(), 1)
        self.assertEqual(AssistantRequest.objects.get().request_type, "translation")
        self.assertFalse(GrammarCorrection.objects.exists())

    def test_romanian_sent_to_correct_is_translated(self):
        self.client.force_login(self.user)
        self.correct.side_effect = AssistantError("romanian_input", "Textul pare să fie în română.")
        text = translation_result().original_text
        response = self.post(text=text, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Română → engleză britanică")
        self.assertContains(response, "l-am tradus")
        self.translate.assert_called_once_with(text)
        self.assertEqual(AssistantRequest.objects.get().request_type, "translation")

    def test_htmx_partial_refreshes_submission_token(self):
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, 'id="result-actions" class="result-actions" hx-swap-oob="true"')
        self.assertContains(response, "Autentificare / Creează cont")
        self.assertNotContains(response, "<!doctype")

    def test_invalid_input_never_calls_services(self):
        for text in ("", "  \n ", "x" * 2001):
            self.assertEqual(self.post(text=text).status_code, 400)
        self.correct.assert_not_called()
        self.assertFalse(RateBucket.objects.exists())

    def test_browser_line_breaks_are_normalised_before_services(self):
        self.post(text="First line.\r\nSecond line.\rThird line.\r\n")
        self.post("/assistant/translate/", "  Prima linie.\r\nA doua linie.   ")
        self.correct.assert_called_once_with("First line.\nSecond line.\nThird line.")
        self.translate.assert_called_once_with("Prima linie.\nA doua linie.")

    def test_invalid_or_missing_token(self):
        self.assertEqual(self.post(token="invalid").status_code, 400)
        self.assertEqual(self.client.post("/assistant/correct/", {"text": "Hello"}).status_code, 400)
        self.correct.assert_not_called()

    def test_get_never_triggers_ai(self):
        for path in ("/assistant/correct/", "/assistant/translate/"):
            self.assertEqual(self.client.get(path).status_code, 405)
        self.correct.assert_not_called()
        self.translate.assert_not_called()

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/assistant/correct/", {"text": "Hi", "submission_token": uuid4()}).status_code, 403)
        self.correct.assert_not_called()

    def test_duplicate_is_rejected_before_provider(self):
        token = uuid4()
        self.assertEqual(self.post(token=token).status_code, 200)
        self.assertEqual(self.post(token=token).status_code, 409)
        self.correct.assert_called_once()
        self.assertEqual(list(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    @override_settings(RATE_LIMIT_MINUTE=1)
    def test_rate_limit(self):
        self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.correct.assert_called_once()

    @override_settings(RATE_LIMIT_DAY=1)
    def test_daily_rate_limit(self):
        self.post()
        self.assertEqual(self.post().status_code, 429)
        self.correct.assert_called_once()

    def test_failure_is_graceful_and_saved_without_text(self):
        self.client.force_login(self.user)
        self.correct.side_effect = AssistantError("timeout")
        response = self.post(HTTP_HX_REQUEST="true")
        self.assertContains(response, "Ceva nu a mers", status_code=503)
        entry = AssistantRequest.objects.get()
        self.assertEqual(entry.status, "failed")
        self.assertEqual(entry.original_text, "")
        self.assertEqual(entry.result_data, {})
        self.assertEqual(entry.error_code, "timeout")

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

    def test_output_and_original_are_escaped(self):
        malicious = "<script>alert('x')</script>"
        result = correction_result()
        result.corrected_text = malicious
        self.correct.return_value = result
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
        self.assertEqual(len(response.context["trend"]), 30)
        self.assertEqual(sum(day["total"] for day in response.context["trend"]), 2)
        self.assertEqual(sum(day["total"] for day in response.context["trend_data"]), 2)
        self.assertContains(response, 'id="trend-data"')
        self.assertContains(response, "vendor/chart.umd.min.js")
        response = self.client.get("/mistakes/")
        self.assertContains(response, "de 2 ori")
        self.assertEqual(self.client.get("/practice/").status_code, 200)
        response = self.client.post("/practice/", {"position": 0, "answer": "1"})
        self.assertContains(response, "Corect!", html=False)

    def test_account_signup_login_profile_logout(self):
        response = self.client.post("/accounts/signup/", {"username": "new-learner", "email": "learner@example.com",
            "password1": "A-unique-pass-9431", "password2": "A-unique-pass-9431"})
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
            self.assertContains(self.client.get(path), '<span class="category-name">Forma verbului<span class="category-badge">1</span></span>'
                                '<span class="category-arrow" aria-hidden="true">→</span>', html=False)
        response = self.client.get("/mistakes/verb_form/")
        self.assertContains(response, "1 greșeală înregistrată în această categorie")
        self.assertContains(response, "didn&#x27;t go")
        self.assertContains(response, f'href="/history/{entry.pk}/"')
        self.assertContains(response, 'href="/practice/?category=verb_form"')
        for path in ("/mistakes/british_english/", "/mistakes/nonsense/"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertContains(self.client.get("/practice/?category=verb_form"), "I didn&#x27;t ___ him yesterday.")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get("/mistakes/verb_form/"), "didn&#x27;t go")

    def test_empty_text_asks_to_write_or_speak(self):
        messages = {"/assistant/correct/": "Ca să facem corectura, scrie ceva în casetă sau apasă microfonul și vorbește.",
                    "/assistant/translate/": "Ca să facem traducerea, scrie ceva în casetă sau apasă microfonul și vorbește."}
        for path, message in messages.items():
            for text in ("", "   \n "):
                with self.subTest(path=path, text=text):
                    response = self.post(path, text=text, HTTP_HX_REQUEST="true")
                    self.assertContains(response, message, status_code=400)
                    self.assertNotContains(response, "Acest câmp este obligatoriu", status_code=400)
                    self.assertNotContains(response, "Textul tău e tot aici", status_code=400)
        too_long = self.post(text="a" * 2001)
        self.assertContains(too_long, "Textul tău e tot aici", status_code=400)
        self.correct.assert_not_called()
        self.translate.assert_not_called()

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

    def test_header_user_icon_follows_sign_in_state(self):
        response = self.client.get("/settings/")
        self.assertContains(response, 'class="user-link" href="/accounts/login/" aria-label="Autentificare"')
        self.assertContains(response, '<span class="nav-icon" aria-hidden="true">', count=8)
        self.client.force_login(self.user)
        response = self.client.get("/settings/")
        self.assertContains(response, 'class="user-link is-signed-in" href="/accounts/profile/" aria-label="Profil: ana"')
        self.assertContains(response, '<span class="user-initial" aria-hidden="true">A</span>')

    def test_settings_accessible_without_account(self):
        self.assertContains(self.client.get("/settings/"), "Nu se salvează cât nu ești autentificat")
