"""Signing up and in with one-time email verification, the Google flows and the migration of existing accounts."""
from importlib import import_module

from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.apps import apps as registry
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from .models import GrandfatheredEmail, LegalAcceptance
from .tests import PASSWORD, SIGNUP, VERIFICATION_SENT, confirmation_path, verified_user


class FreshRateLimits:
    """allauth keeps its rate limits (one link per address every few minutes) in the cache; each test starts clean."""

    def setUp(self):
        super().setUp()
        cache.clear()


class EmailVerificationTests(FreshRateLimits, TestCase):
    def sign_up(self, **data):
        return self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on", **data})

    def test_signup_sends_one_verification_email_and_does_not_sign_in(self):
        self.assertRedirects(self.sign_up(), VERIFICATION_SENT)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual((message.to, message.subject),
                         (["learner@example.com"], "Corect.uk – Confirmă adresa de email pentru contul tău"))
        self.assertIn("O confirmi o singură dată", message.body)
        self.assertNotIn("_auth_user_id", self.client.session)  # not signed in before confirming
        page = self.client.get(VERIFICATION_SENT)
        self.assertContains(page, "Verifică-ți emailul")
        self.assertContains(page, "Nu ai primit emailul?")

    def test_an_unverified_account_cannot_sign_in_or_use_the_product(self):
        self.sign_up()
        mail.outbox.clear()
        response = self.client.post("/accounts/login/", {"login": "new-learner", "password": PASSWORD})
        self.assertRedirects(response, VERIFICATION_SENT)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 0)  # the first link is still valid; no new mail inside the cooldown
        self.assertRedirects(self.client.get("/history/"), "/accounts/login/?next=/history/")

    def test_the_link_confirms_once_and_later_sign_ins_need_nothing_more(self):
        self.sign_up()
        path = confirmation_path(mail.outbox[0])
        self.assertContains(self.client.get(path), "Confirmă")  # a page with a button: link scanners cannot confirm it
        self.assertFalse(EmailAddress.objects.get().verified)
        self.assertRedirects(self.client.post(path), "/", fetch_redirect_response=False)
        self.assertTrue(EmailAddress.objects.get().verified)
        self.assertEqual(self.client.get("/history/").status_code, 200)  # signed in by the confirmation
        self.client.post("/accounts/logout/")
        mail.outbox.clear()
        response = self.client.post("/accounts/login/", {"login": "learner@example.com", "password": PASSWORD})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_is_neutral_and_rate_limited(self):
        self.sign_up()
        mail.outbox.clear()
        for email in ("learner@example.com", "nobody@example.com"):
            with self.subTest(email=email):
                response = self.client.post("/accounts/verification/resend/", {"email": email}, follow=True)
                self.assertContains(response, "Dacă adresa aparține unui cont care așteaptă confirmarea")
        self.assertEqual(len(mail.outbox), 0)  # still inside the cooldown of the signup mail
        self.client.post("/accounts/verification/resend/", {"email": "learner@example.com"})
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get("/accounts/verification/resend/").status_code, 405)

    def test_resend_sends_a_fresh_link_after_the_cooldown(self):
        user = User.objects.create_user("late", "late@example.com", PASSWORD)
        EmailAddress.objects.create(user=user, email="late@example.com", verified=False, primary=True)
        self.client.post("/accounts/verification/resend/", {"email": "Late@Example.com"})
        self.assertEqual([message.to for message in mail.outbox], [["late@example.com"]])

    def test_a_new_address_is_verified_before_it_replaces_the_old_one(self):
        user = verified_user("ana", "ana@example.com")
        self.client.force_login(user)
        self.client.post("/accounts/email/", {"email": "ana.new@example.com", "action_add": ""})
        self.assertEqual(mail.outbox[-1].to, ["ana.new@example.com"])
        user.refresh_from_db()
        self.assertEqual(user.email, "ana@example.com")  # the confirmed address stays in use meanwhile
        profile = self.client.get("/accounts/profile/")
        self.assertContains(profile, "ana.new@example.com")
        self.assertContains(profile, "așteaptă confirmarea")
        self.assertEqual(self.client.get("/history/").status_code, 200)  # the account keeps working
        self.client.post(confirmation_path(mail.outbox[-1]))
        user.refresh_from_db()
        self.assertEqual(user.email, "ana.new@example.com")
        self.assertEqual(list(EmailAddress.objects.filter(user=user).values_list("email", "verified", "primary")),
                         [("ana.new@example.com", True, True)])


class ExistingAccountsMigrationTests(FreshRateLimits, TestCase):
    def test_accounts_with_an_address_are_grandfathered_and_others_left_alone(self):
        migration = import_module("apps.accounts.migrations.0005_grandfather_existing_addresses")
        old = User.objects.create_user("old", "Old@Example.com", PASSWORD)
        blank = User.objects.create_user("blank", "", PASSWORD)
        modern = verified_user("modern", "modern@example.com")
        migration.grandfather(registry, None)
        migration.grandfather(registry, None)  # a second run adds nothing
        address = EmailAddress.objects.get(user=old)
        self.assertEqual((address.email, address.verified, address.primary), ("old@example.com", True, True))
        self.assertTrue(GrandfatheredEmail.objects.filter(email_address=address).exists())
        self.assertFalse(EmailAddress.objects.filter(user=blank).exists())
        self.assertEqual(EmailAddress.objects.filter(user=modern).count(), 1)
        self.assertEqual(GrandfatheredEmail.objects.count(), 1)
        # The existing account signs in exactly as before, with no verification step.
        response = self.client.post("/accounts/login/", {"login": "old", "password": PASSWORD})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
        migration.undo(registry, None)
        self.assertFalse(EmailAddress.objects.filter(user=old).exists())


class GoogleSignInTests(FreshRateLimits, TestCase):
    """A verified Google identity arriving at allauth's completion step (the OAuth exchange itself is allauth's)."""

    def google(self, email, verified=True, uid="google-1"):
        request = RequestFactory().get("/accounts/google/login/callback/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        request.session.save()
        with context.request_context(request):
            provider = get_social_adapter().get_provider(request, "google")
            login = SocialLogin(user=User(email=email), provider=provider,
                                account=SocialAccount(provider="google", uid=uid, extra_data={"email": email}),
                                email_addresses=[EmailAddress(email=email, verified=verified, primary=True)])
            response = complete_social_login(request, login)
        request.session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = request.session.session_key
        return response, request

    def test_a_new_google_user_confirms_age_and_terms_before_the_account_exists(self):
        response, _ = self.google("new.person@gmail.com")
        self.assertEqual(response.url, "/accounts/3rdparty/signup/")
        self.assertFalse(User.objects.exists())
        page = self.client.get("/accounts/3rdparty/signup/")
        self.assertContains(page, "Confirm că am cel puțin 16 ani")
        refused = self.client.post("/accounts/3rdparty/signup/", {"username": "newperson",
                                                                  "email": "new.person@gmail.com"})
        self.assertContains(refused, "Ca să creezi contul, confirmă că ai cel puțin 16 ani")
        self.assertFalse(User.objects.exists())
        done = self.client.post("/accounts/3rdparty/signup/", {"username": "newperson", "email": "new.person@gmail.com",
                                                               "accept_legal": "on"})
        self.assertRedirects(done, "/", fetch_redirect_response=False)
        home = self.client.get("/")  # Google proved the address, so the account opens now: welcome, no banner
        self.assertContains(home, '<dialog class="wait-screen event-screen is-welcome" open', count=1)
        self.assertContains(home, "Bun venit, newperson!")
        self.assertNotContains(home, 'class="messages"')
        user = User.objects.get(username="newperson")
        self.assertEqual(LegalAcceptance.objects.get(user=user).source, "signup")
        self.assertTrue(EmailAddress.objects.get(user=user).verified)  # Google verified it: no Corect email
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.client.get("/history/").status_code, 200)

    def test_a_returning_google_user_signs_straight_in(self):
        user = verified_user("returning", "returning@gmail.com")
        SocialAccount.objects.create(user=user, provider="google", uid="google-7")
        response, request = self.google("returning@gmail.com", uid="google-7")
        self.assertEqual(request.user, user)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(LegalAcceptance.objects.count(), 0)  # no second acceptance just for using Google

    def test_a_verified_google_address_links_to_the_account_that_proved_it(self):
        user = verified_user("ana", "ana@gmail.com")
        response, request = self.google("ana@gmail.com", uid="google-ana")
        self.assertEqual(request.user, user)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(SocialAccount.objects.filter(user=user, uid="google-ana").exists())
        self.assertTrue(user.has_usable_password())

    def test_a_grandfathered_address_is_never_taken_over_through_google(self):
        # An account from before verification: its address was never proven, so whoever owns it on Google must not get
        # in (and the account's creator must not keep access to the Google owner).
        user = verified_user("old", "victim@gmail.com")
        GrandfatheredEmail.objects.create(email_address=EmailAddress.objects.get(user=user))
        response, request = self.google("victim@gmail.com", uid="google-victim")
        self.assertFalse(request.user.is_authenticated)
        self.assertFalse(SocialAccount.objects.exists())
        self.assertEqual(response.url, "/accounts/3rdparty/signup/")
        # No second account either: the address's owner gets the "account already exists" mail with a password reset,
        # which proves the address; they can then sign in and connect Google from their account page.
        refused = self.client.post("/accounts/3rdparty/signup/", {"username": "other", "email": "victim@gmail.com",
                                                                  "accept_legal": "on"})
        self.assertRedirects(refused, VERIFICATION_SENT, fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(SocialAccount.objects.exists())
        self.assertEqual([(message.subject, message.to) for message in mail.outbox],
                         [("Corect.uk – Ai deja un cont Corect.uk", ["victim@gmail.com"])])

    def test_an_unverified_google_address_gets_a_verification_email(self):
        response, _ = self.google("unproven@example.com", verified=False)
        self.client.post("/accounts/3rdparty/signup/", {"username": "unproven", "email": "unproven@example.com",
                                                        "accept_legal": "on"})
        user = User.objects.get(username="unproven")
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertEqual([message.to for message in mail.outbox], [["unproven@example.com"]])
        self.assertRedirects(self.client.get("/history/"), "/accounts/login/?next=/history/")
        self.assertNotContains(self.client.get("/accounts/confirm-email/"), "event-screen is-")  # not open yet
        welcome = self.client.post(confirmation_path(mail.outbox[-1]), follow=True)
        self.assertContains(welcome, '<dialog class="wait-screen event-screen is-welcome" open', count=1)

    def test_an_unverified_google_address_never_signs_into_an_existing_account(self):
        user = verified_user("ana", "ana@example.com")
        response, request = self.google("ana@example.com", verified=False, uid="google-fake")
        self.assertFalse(request.user.is_authenticated)
        self.assertFalse(SocialAccount.objects.filter(user=user).exists())


class AuthPagesTests(FreshRateLimits, TestCase):
    def test_google_button_on_login_and_signup(self):
        for path in ("/accounts/login/", "/accounts/signup/"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertContains(page, 'action="/accounts/google/login/?process=login')
                self.assertContains(page, "Continuă cu Google")
                self.assertContains(page, 'class="auth-divider"')

    def test_google_button_hidden_without_credentials(self):
        with self.settings(GOOGLE_LOGIN_ENABLED=False):
            self.assertNotContains(self.client.get("/accounts/login/"), "Continuă cu Google")

    def test_login_page_keeps_username_or_email_and_offers_password_reset(self):
        page = self.client.get("/accounts/login/")
        self.assertContains(page, "Nume de utilizator sau email")
        self.assertContains(page, 'href="/accounts/password/reset/"')
        reset = self.client.post("/accounts/password/reset/", {"email": "nobody@example.com"})
        self.assertEqual(reset.status_code, 302)
