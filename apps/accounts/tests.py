from importlib import import_module
from io import StringIO
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.apps import apps as registry
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import AnonymousVisitor
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.models import AssistantRequest
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from . import testing
from .emails import EMAIL_INDEX, duplicate_email_groups
from .testing import confirmation_path
from .models import LegalAcceptance

PASSWORD = "A-unique-pass-9431"
SIGNUP = {"username": "new-learner", "email": "learner@example.com", "password1": PASSWORD, "password2": PASSWORD}
VERIFICATION_SENT = "/accounts/confirm-email/"


def verified_user(username, email, password=PASSWORD):
    return testing.verified_user(username, email, password)


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
        self.assertRedirects(response, VERIFICATION_SENT)
        acceptance = LegalAcceptance.objects.get()
        self.assertEqual((acceptance.user.username, acceptance.terms_version, acceptance.privacy_version, acceptance.source),
                         ("new-learner", settings.TERMS_VERSION, settings.PRIVACY_VERSION, "signup"))
        self.assertLessEqual(acceptance.accepted_at, timezone.now())
        self.assertEqual(response.cookies[CONSENT_COOKIE].value, consent_cookie_value(True))  # Analytics on by default.
        self.assertNotContains(self.client.get("/"), 'class="consent-bar"')

    def test_signup_keeps_the_analytics_choice(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        response = self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on"})
        self.assertEqual(response.cookies[CONSENT_COOKIE].value, consent_cookie_value(False))


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", password=PASSWORD)
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


def drop_email_index():
    """Duplicates can only exist in data created before migration 0003: remove the index to recreate that state."""
    with connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX {EMAIL_INDEX}")


class AccountEmailTests(TestCase):
    """Every account has one valid email address, stored lower-case and unique ignoring case."""

    def setUp(self):
        cache.clear()  # allauth's per-address mail limits live in the cache; each test starts clean

    def signup(self, **data):
        return self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on", **data})

    def test_signup_requires_an_email(self):
        for email in ("", "   "):
            with self.subTest(email=email):
                response = self.signup(email=email)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors["email"])
        self.assertEqual(self.signup(email="not-an-email").status_code, 200)
        self.assertFalse(User.objects.exists())
        self.assertContains(self.client.get("/accounts/signup/"), 'type="email" name="email"')

    def test_signup_stores_the_address_trimmed_and_lower_case(self):
        self.assertRedirects(self.signup(email="  Daniel@Example.COM "), VERIFICATION_SENT)
        self.assertEqual(User.objects.get().email, "daniel@example.com")
        self.assertEqual(EmailAddress.objects.get().email, "daniel@example.com")

    def test_duplicate_addresses_are_refused_whatever_their_case(self):
        # No second account, and the page reveals nothing: the address's owner is told by email instead, once (the
        # rate limit keeps repeated attempts from flooding the inbox).
        verified_user("daniel", "daniel@example.com")
        for number, email in enumerate(("daniel@example.com", "Daniel@Example.com", " DANIEL@EXAMPLE.COM")):
            with self.subTest(email=email):
                self.assertRedirects(self.signup(email=email, username=f"other{number}"), VERIFICATION_SENT)
        self.assertEqual([(message.subject, message.to) for message in mail.outbox],
                         [("Corect.uk – Ai deja un cont Corect.uk", ["daniel@example.com"])])
        self.assertEqual(User.objects.count(), 1)

    def test_the_database_refuses_duplicates_even_without_the_form(self):
        User.objects.create_user("first", "same@example.com", PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("second", "SAME@example.com", PASSWORD)
        # Blank addresses (accounts created before the rule) do not collide with each other.
        User.objects.create_user("legacy-one", "", PASSWORD)
        User.objects.create_user("legacy-two", "", PASSWORD)

    def test_a_concurrent_signup_with_the_same_address_is_treated_as_an_existing_one(self):
        # The form check passed for both requests; the database index refuses the second row, and the second signup
        # ends like any signup with a used address: no account, no acceptance, the neutral page.
        conflict = IntegrityError(f'duplicate key value violates unique constraint "{EMAIL_INDEX}"')
        with patch("allauth.account.forms.BaseSignupForm.save", side_effect=conflict):
            response = self.signup()
        self.assertRedirects(response, VERIFICATION_SENT)
        self.assertFalse(User.objects.exists())
        self.assertFalse(LegalAcceptance.objects.exists())
        with patch("allauth.account.forms.BaseSignupForm.save", side_effect=IntegrityError("other")), \
                self.assertRaises(IntegrityError):
            self.signup()

    def test_login_with_email_ignores_case_and_username_still_works(self):
        verified_user("ana", "ana@example.com")
        for identifier in ("ana", "ana@example.com", "  ANA@Example.com "):
            with self.subTest(identifier=identifier):
                response = self.client.post("/accounts/login/", {"login": identifier, "password": PASSWORD})
                self.assertRedirects(response, "/", fetch_redirect_response=False)
                self.client.post("/accounts/logout/")
        response = self.client.post("/accounts/login/", {"login": "ana@example.com", "password": "wrong"})
        self.assertContains(response, "nume de utilizator sau email")


    def test_admin_add_form_requires_a_unique_address(self):
        User.objects.create_user("taken", "taken@example.com", PASSWORD)
        self.client.force_login(User.objects.create_superuser("root", "root@example.com", PASSWORD))
        data = {"username": "staff-made", "usable_password": "true", "password1": PASSWORD, "password2": PASSWORD}
        self.assertContains(self.client.post("/admin/auth/user/add/", {**data, "email": ""}),
                            "Every new account needs an email address.")
        self.assertContains(self.client.post("/admin/auth/user/add/", {**data, "email": "Taken@example.com"}),
                            "Există deja un cont")
        self.client.post("/admin/auth/user/add/", {**data, "email": "New@Example.com"})
        self.assertEqual(User.objects.get(username="staff-made").email, "new@example.com")


class LegacyAccountWithoutEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("legacy", "", PASSWORD)
        self.client.force_login(self.user)

    def test_pages_lead_to_the_profile_until_an_address_is_added_and_confirmed(self):
        for path in ("/", "/history/", "/learn/", "/about/"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/accounts/profile/?email_required=1",
                                     fetch_redirect_response=False)
        self.assertContains(self.client.get("/accounts/profile/?email_required=1"),
                            "Contul tău nu are încă o adresă de email.")
        # Adding the address sends its link; until it is opened the account stays on the account pages.
        self.client.post("/accounts/email/", {"email": "legacy@example.com", "action_add": ""})
        self.assertEqual(mail.outbox[-1].to, ["legacy@example.com"])
        self.assertRedirects(self.client.get("/history/"), "/accounts/profile/?email_required=1",
                             fetch_redirect_response=False)
        self.assertContains(self.client.get("/accounts/profile/"), "Confirmă adresa de email ca să poți continua")
        self.client.post(confirmation_path(mail.outbox[-1]))
        self.assertEqual(self.client.get("/history/").status_code, 200)
        self.assertNotContains(self.client.get("/accounts/profile/"), "nu are încă o adresă de email")

    def test_a_legacy_account_without_an_address_can_still_sign_in_to_add_one(self):
        self.client.logout()
        response = self.client.post("/accounts/login/", {"login": "legacy", "password": PASSWORD})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/"), "/accounts/profile/?email_required=1", fetch_redirect_response=False)

    def test_legal_pages_logout_and_health_stay_open(self):
        for path in ("/confidentialitate/", "/termeni/", "/cookie-uri/", "/healthz", "/readyz"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertRedirects(self.client.post("/accounts/logout/"), "/")

    def test_ai_requests_are_refused_without_calling_the_provider(self):
        with patch("apps.assistant.views.NaturalizeService.naturalize") as naturalize:
            response = self.client.post("/naturalize/", {"text": "hello"}, HTTP_HX_REQUEST="true")
            self.assertEqual((response.status_code, response["HX-Redirect"]),
                             (204, "/accounts/profile/?email_required=1"))
            voice = self.client.post("/assistant/speech/", {"text": "hello"})
            self.assertEqual((voice.status_code, voice.json()["code"]), (403, "email_required"))
        naturalize.assert_not_called()

    def test_anonymous_visitors_and_accounts_with_an_address_are_unaffected(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.client.force_login(verified_user("modern", "modern@example.com"))
        self.assertEqual(self.client.get("/history/").status_code, 200)


class EmailIntegrityReportTests(TestCase):
    def run_report(self):
        out = StringIO()
        try:
            call_command("report_email_integrity", stdout=out)
            return 0, out.getvalue()
        except SystemExit as exit_:
            return exit_.code, out.getvalue()

    def test_clean_database(self):
        User.objects.create_user("ana", "ana@example.com", PASSWORD)
        code, output = self.run_report()
        self.assertEqual(code, 0)
        self.assertIn("Every account has a unique email address.", output)

    def test_shared_and_missing_addresses_are_reported_without_revealing_them(self):
        User.objects.create_user("blank", "", PASSWORD)
        drop_email_index()
        first = User.objects.create_user("one", "Shared@example.com", PASSWORD)
        second = User.objects.create_user("two", "shared@example.com ", PASSWORD)
        self.assertEqual(duplicate_email_groups(), [[first.pk, second.pk]])
        code, output = self.run_report()
        self.assertEqual(code, 1)
        self.assertIn(f"Shared address s***@example.com: account IDs {first.pk}, {second.pk}", output)
        self.assertIn("Accounts without an address: 1", output)
        self.assertNotIn("shared@example.com", output.lower())


class EmailMigrationPreflightTests(TestCase):
    def test_preflight_stops_on_shared_addresses_without_changing_data(self):
        migration = import_module("apps.accounts.migrations.0003_email_normalise_and_unique")
        drop_email_index()
        User.objects.create_user("one", " Mixed@Example.com", PASSWORD)
        migration.preflight(registry, None)  # A single address is fine.
        migration.normalise(registry, None)
        self.assertEqual(User.objects.get(username="one").email, "mixed@example.com")
        User.objects.create_user("two", "MIXED@example.com", PASSWORD)
        with self.assertRaisesMessage(RuntimeError, "1 address(es) are shared by more than one account"):
            migration.preflight(registry, None)
        self.assertEqual(User.objects.get(username="two").email, "MIXED@example.com")
