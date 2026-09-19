import uuid
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core import mail, serializers
from django.core.cache import cache
from django.db import models
from django.test import TestCase, override_settings

from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.models import AssistantRequest, GrammarCorrection, RateBucket, SubmissionClaim
from apps.assistant.services.naturalize import Naturalized
from apps.assistant.tests.examples import correction_result
from apps.accounts.testing import confirmation_path, verified_user
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from .test_recording import provider_usage, replying

SUBMITTED_TEXT = "My secret sentance about Bucharest."
RESPONSE_TEXT = "RESPONSE-ONLY corrected wording."
CLIENT_IP = "203.0.113.77"
PASSWORD = "A-unique-pass-9431"


class VisitorPrivacyTests(TestCase):
    def setUp(self):
        cache.clear()  # allauth's per-address mail limits live in the cache; each test starts clean
        result = correction_result()
        result.original_text = SUBMITTED_TEXT
        result.corrected_text = RESPONSE_TEXT
        patch("apps.assistant.views.NaturalizeService.naturalize",
              side_effect=replying(Naturalized("correction", "en", result), provider_usage())).start()
        self.addCleanup(patch.stopall)
        # These tests cover a visitor who allowed anonymous analytics; without that choice no visitor exists at all.
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)

    def post(self):
        return self.client.post("/naturalize/", {"text": SUBMITTED_TEXT, "submission_token": uuid4()},
                                REMOTE_ADDR=CLIENT_IP)

    def test_anonymous_text_response_and_ip_are_never_persisted(self):
        self.assertContains(self.post(), RESPONSE_TEXT)
        stored = serializers.serialize("json", [
            *UsageEvent.objects.all(), *AnonymousVisitor.objects.all(), *AssistantRequest.objects.all(),
            *GrammarCorrection.objects.all(), *RateBucket.objects.all(), *SubmissionClaim.objects.all()])
        for secret in (SUBMITTED_TEXT, "Bucharest", RESPONSE_TEXT, CLIENT_IP):
            self.assertNotIn(secret, stored)
        self.assertEqual(UsageEvent.objects.count(), 1)
        self.assertFalse(AssistantRequest.objects.exists())
        for model in (UsageEvent, AnonymousVisitor):
            text_fields = [f.name for f in model._meta.get_fields() if isinstance(f, (models.TextField, models.JSONField))]
            self.assertEqual(text_fields, [], model.__name__)

    def test_analytics_is_on_by_default_and_off_when_switched_off(self):
        del self.client.cookies[CONSENT_COOKIE]
        self.assertIn(VISITOR_COOKIE, self.post().cookies)  # No choice made yet: on by default.
        self.client = self.client_class()
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        response = self.post()
        self.assertContains(response, RESPONSE_TEXT)
        self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertEqual(AnonymousVisitor.objects.count(), 1)
        self.assertEqual(UsageEvent.objects.filter(visitor__isnull=True).count(), 1)

    def test_first_request_assigns_a_random_visitor_cookie_that_later_requests_reuse(self):
        cookie = self.post().cookies[VISITOR_COOKIE]
        visitor = AnonymousVisitor.objects.get()
        self.assertEqual(cookie.value, str(visitor.pk))
        self.assertEqual(uuid.UUID(cookie.value).version, 4)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["max-age"], 365 * 24 * 60 * 60)
        self.post()
        self.assertEqual(AnonymousVisitor.objects.count(), 1)
        self.assertEqual(set(UsageEvent.objects.values_list("visitor_id", flat=True)), {visitor.pk})
        self.assertGreaterEqual(AnonymousVisitor.objects.get().last_seen_at, visitor.last_seen_at)

    def test_unknown_or_malformed_cookie_is_never_adopted(self):
        for value in (str(uuid4()), "not-a-uuid"):
            with self.subTest(value=value):
                self.client.cookies[VISITOR_COOKIE] = value
                self.assertNotEqual(self.post().cookies[VISITOR_COOKIE].value, value)
        self.assertEqual(AnonymousVisitor.objects.count(), 2)

    def test_signup_converts_the_visitor_without_copying_or_duplicating_usage(self):
        self.post()
        self.post()
        visitor = AnonymousVisitor.objects.get()
        self.client.post("/accounts/signup/", {"first_name": "Ana", "email": "learner@example.com",
                                                "password1": PASSWORD, "password2": PASSWORD, "accept_legal": "on"})
        user = User.objects.get(email="learner@example.com")
        self.client.post(confirmation_path(mail.outbox[-1]))  # confirming the address signs the account in
        visitor.refresh_from_db()
        self.assertEqual((visitor.converted_user, visitor.converted_via), (user, "signup"))
        self.assertIsNotNone(visitor.converted_at)
        self.assertEqual(UsageEvent.objects.count(), 2)
        self.assertFalse(UsageEvent.objects.filter(user=user).exists())
        self.assertFalse(AssistantRequest.objects.exists())
        self.post()
        latest = UsageEvent.objects.latest("pk")
        self.assertEqual((latest.audience, latest.user, latest.visitor_id), ("registered", user, visitor.pk))
        self.assertEqual(AnonymousVisitor.objects.count(), 1)
        self.client.post("/accounts/logout/")
        self.client.post("/accounts/login/", {"login": "learner@example.com", "password": PASSWORD})
        visitor.refresh_from_db()
        self.assertEqual((visitor.converted_via, AnonymousVisitor.objects.count()), ("signup", 1))

    def test_login_converts_the_visitor_and_the_first_conversion_wins(self):
        first = verified_user("existing", "existing@example.com", PASSWORD)
        verified_user("second", "second@example.com", PASSWORD)
        self.post()
        self.client.post("/accounts/login/", {"login": "existing@example.com", "password": PASSWORD})
        self.client.post("/accounts/logout/")
        self.client.post("/accounts/login/", {"login": "second@example.com", "password": PASSWORD})
        visitor = AnonymousVisitor.objects.get()
        self.assertEqual((visitor.converted_user, visitor.converted_via), (first, "login"))
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_withdrawn_analytics_stops_conversion(self):
        verified_user("existing", "existing@example.com", PASSWORD)
        self.post()
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        self.client.post("/accounts/login/", {"login": "existing@example.com", "password": PASSWORD})
        self.assertIsNone(AnonymousVisitor.objects.get().converted_user)

    @override_settings(ANALYTICS_VISITOR_COOKIE=False)
    def test_visitor_tracking_can_be_switched_off(self):
        response = self.post()
        self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertFalse(AnonymousVisitor.objects.exists())
        self.assertIsNone(UsageEvent.objects.get().visitor)
        self.assertContains(self.client.get("/confidentialitate/"), "Cookie-ul de statistici pentru vizitatori este dezactivat.")

    def test_privacy_page_discloses_the_visitor_cookie(self):
        self.assertContains(self.client.get("/confidentialitate/"),
                            "păstrat în cookie-ul <code>corect_visitor_id</code>")
