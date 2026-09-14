from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import AnonymousVisitor
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.models import AssistantRequest
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from .models import LegalAcceptance

PASSWORD = "A-unique-pass-9431"
SIGNUP = {"username": "new-learner", "email": "learner@example.com", "password1": PASSWORD, "password2": PASSWORD}


class SignupAcceptanceTests(TestCase):
    def test_signup_form_has_an_unticked_legal_checkbox_with_working_links(self):
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, 'Confirm că am cel puțin 16 ani și accept <a href="/termeni/">Termenii</a> și '
                                      '<a href="/confidentialitate/">Politica de confidențialitate</a>.')
        self.assertContains(response, '<input type="checkbox" name="accept_legal" required id="id_accept_legal">')
        for path in ("/termeni/", "/confidentialitate/"):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_signup_fails_cleanly_without_acceptance(self):
        response = self.client.post("/accounts/signup/", SIGNUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ca să creezi contul, confirmă că ai cel puțin 16 ani")
        self.assertFalse(User.objects.exists())
        self.assertFalse(LegalAcceptance.objects.exists())

    def test_signup_records_the_accepted_versions(self):
        response = self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on"})
        self.assertRedirects(response, "/")
        acceptance = LegalAcceptance.objects.get()
        self.assertEqual((acceptance.user.username, acceptance.terms_version, acceptance.privacy_version, acceptance.source),
                         ("new-learner", "2026-09-14", "2026-09-14", "signup"))
        self.assertLessEqual(acceptance.accepted_at, timezone.now())
        self.assertEqual(response.cookies[CONSENT_COOKIE].value, consent_cookie_value(True))  # Analytics on by default.
        self.assertNotContains(self.client.get("/"), 'class="consent-bar"')

    def test_signup_keeps_the_analytics_choice(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        response = self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on"})
        self.assertEqual(response.cookies[CONSENT_COOKIE].value, consent_cookie_value(False))


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", password=PASSWORD)
        self.client.force_login(self.user)

    def delete(self, password):
        return self.client.post("/accounts/profile/", {"action": "delete", "delete-password": password})

    def test_profile_offers_account_deletion(self):
        self.assertContains(self.client.get("/accounts/profile/"), "Șterge contul definitiv")

    def test_wrong_password_keeps_the_account(self):
        response = self.delete("wrong-password")
        self.assertContains(response, "Parola nu este corectă.")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_deletion_removes_the_account_history_and_visitor_ids_and_signs_out(self):
        AssistantRequest.objects.create(user=self.user, request_type="correction", original_text="text",
                                        result_text="result", model_used="test", prompt_version="test", status="success")
        LegalAcceptance.objects.create(user=self.user, terms_version="2026-09-14", privacy_version="2026-09-14",
                                       source="signup")
        now = timezone.now()
        converted = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now, converted_user=self.user)
        browser = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
        other = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
        self.client.cookies[VISITOR_COOKIE] = str(browser.pk)
        response = self.delete(PASSWORD)
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(response.cookies[VISITOR_COOKIE]["max-age"], 0)
        self.assertContains(self.client.get("/"), "Contul tău și istoricul au fost șterse.")
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(AssistantRequest.objects.exists())
        self.assertFalse(LegalAcceptance.objects.exists())
        self.assertEqual(list(AnonymousVisitor.objects.all()), [other])
        self.assertFalse(AnonymousVisitor.objects.filter(pk__in=[converted.pk, browser.pk]).exists())
        self.assertRedirects(self.client.get("/accounts/profile/"), "/accounts/login/?next=/accounts/profile/")
