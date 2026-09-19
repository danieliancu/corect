"""Free/Pro from Stripe subscriptions: checkout, webhooks, the portal, deletion and the plan the rest of the app sees.
Stripe itself is replaced by a mock; webhook payloads are signed exactly as Stripe signs them."""
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import stripe
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.testing import make_pro, verified_user
from apps.analytics.models import UsageEvent
from apps.assistant.models import AssistantRequest
from apps.assistant.services import quota
from apps.assistant.tests.examples import naturalized_english
from apps.billing.models import AccountSubscription, StripeEvent
from apps.core.checks import billing_configured, email_delivery_configured, google_login_configured
from apps.core.plans import MANUAL, PAID, is_pro, pro_source, tier_for

PASSWORD = "Billing-test-pass-4410"


def stripe_object(values):
    return stripe.StripeObject.construct_from(values, "unit-test-key")


def subscription(status="active", sub_id="sub_1", customer="cus_1", cancel=False, price="price_unit_pro"):
    return stripe_object({"id": sub_id, "object": "subscription", "customer": customer, "status": status,
                          "cancel_at_period_end": cancel, "metadata": {},
                          "items": {"data": [{"price": {"id": price}, "current_period_end": int(time.time()) + 86400 * 30}]}})


def fake_stripe(existing=(), retrieved=None):
    """A Stripe client whose answers the test controls."""
    client = MagicMock()
    client.v1.customers.create.return_value = stripe_object({"id": "cus_1"})
    client.v1.subscriptions.list.return_value = stripe_object({"data": list(existing)})
    client.v1.subscriptions.retrieve.return_value = retrieved or subscription()
    client.v1.checkout.sessions.create.return_value = stripe_object({"id": "cs_new", "url": "https://checkout.stripe.test/cs_new"})
    client.v1.checkout.sessions.retrieve.return_value = stripe_object({"id": "cs_old", "status": "open", "payment_status": "unpaid"})
    client.v1.billing_portal.sessions.create.return_value = stripe_object({"url": "https://billing.stripe.test/p"})
    return client


def signed(payload, secret=None):
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signature = hmac.new((secret or settings.STRIPE_WEBHOOK_SECRET).encode(), f"{timestamp}.".encode() + body,
                         hashlib.sha256).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def event(kind, obj, event_id=None, created=None):
    if isinstance(obj, stripe.StripeObject):
        obj = json.loads(str(obj))  # the plain JSON Stripe sends
    return {"id": event_id or f"evt_{uuid4().hex}", "object": "event", "type": kind, "created": created or int(time.time()),
            "data": {"object": obj}}


class BillingTestCase(TestCase):
    def setUp(self):
        self.user = verified_user("ana", "ana@example.com", PASSWORD)
        self.stripe = fake_stripe()
        patcher = patch("apps.billing.services.client", return_value=self.stripe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def subscribe(self, user=None, status="active", **fields):
        return AccountSubscription.objects.create(user=user or self.user, stripe_customer_id=fields.pop("customer", "cus_1"),
                                                  stripe_subscription_id=fields.pop("sub_id", "sub_1"), status=status,
                                                  **fields)

    def webhook(self, payload, secret=None):
        body, header = signed(payload, secret)
        return self.client.post("/billing/webhook/stripe/", body, content_type="application/json",
                                HTTP_STRIPE_SIGNATURE=header)

    def fresh(self, user=None):
        return User.objects.get(pk=(user or self.user).pk)


class PlanRuleTests(BillingTestCase):
    def test_tiers_follow_the_subscription_status(self):
        self.assertEqual(tier_for(AnonymousUser()), "anonymous")
        self.assertEqual(tier_for(self.fresh()), "free")
        row = self.subscribe()
        for status, tier in (("active", "pro"), ("trialing", "pro"), ("past_due", "free"), ("unpaid", "free"),
                             ("incomplete", "free"), ("incomplete_expired", "free"), ("paused", "free"),
                             ("canceled", "free")):
            with self.subTest(status=status):
                row.status = status
                row.save()
                self.assertEqual(tier_for(self.fresh()), tier)
        row.status, row.cancel_at_period_end = "active", True  # cancelled at the period end: Pro until Stripe ends it
        row.save()
        self.assertEqual((tier_for(self.fresh()), pro_source(self.fresh())), ("pro", PAID))

    def test_manual_pro_is_a_separate_override(self):
        make_pro(self.user)
        self.assertEqual(pro_source(self.fresh()), MANUAL)
        self.assertFalse(AccountSubscription.objects.exists())  # manual Pro never creates a subscription
        self.subscribe()
        self.assertEqual(pro_source(self.fresh()), PAID)

    def test_quota_and_usage_ledger_follow_the_plan(self):
        self.assertEqual(quota.daily_limit(tier_for(self.fresh())), 20)
        row = self.subscribe()
        self.assertEqual(quota.daily_limit(tier_for(self.fresh())), 200)
        self.client.force_login(self.user)
        with patch("apps.assistant.views.NaturalizeService.naturalize", return_value=naturalized_english()):
            self.client.post("/naturalize/", {"text": "I has a car.", "submission_token": uuid4()})
            row.status = "canceled"
            row.save()
            self.client.post("/naturalize/", {"text": "I has two car.", "submission_token": uuid4()})
        self.assertEqual(list(UsageEvent.objects.order_by("pk").values_list("plan", flat=True)), ["pro", "free"])
        self.assertEqual(AssistantRequest.objects.count(), 2)


class CheckoutTests(BillingTestCase):
    def test_checkout_needs_a_signed_in_verified_account_and_a_post(self):
        self.assertRedirects(self.client.post("/billing/checkout/"), "/accounts/login/?next=/billing/checkout/",
                             fetch_redirect_response=False)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/billing/checkout/").status_code, 405)
        pending = verified_user("pending", "pending@example.com", PASSWORD)
        EmailAddress.objects.filter(user=pending).update(verified=False)
        self.client.force_login(pending)
        self.assertRedirects(self.client.post("/billing/checkout/"), "/accounts/profile/?email_required=1",
                             fetch_redirect_response=False)
        self.stripe.v1.checkout.sessions.create.assert_not_called()

    def test_a_free_account_goes_to_checkout_for_the_configured_price(self):
        self.client.force_login(self.user)
        response = self.client.post("/billing/checkout/")
        self.assertRedirects(response, "https://checkout.stripe.test/cs_new", fetch_redirect_response=False)
        params = self.stripe.v1.checkout.sessions.create.call_args.args[0]
        self.assertEqual((params["mode"], params["customer"], params["client_reference_id"]),
                         ("subscription", "cus_1", str(self.user.pk)))
        self.assertEqual(params["line_items"], [{"price": "price_unit_pro", "quantity": 1}])
        self.assertEqual((params["success_url"], params["cancel_url"]),
                         ("https://corect.uk/billing/success/", "https://corect.uk/#plans"))
        customer = self.stripe.v1.customers.create.call_args
        self.assertEqual(customer.args[0]["metadata"], {"user_id": str(self.user.pk)})
        self.assertEqual(customer.args[1], {"idempotency_key": f"corect-customer-{self.user.pk}"})
        row = AccountSubscription.objects.get()
        self.assertEqual((row.stripe_customer_id, row.checkout_session_id, row.status), ("cus_1", "cs_new", ""))
        self.assertFalse(is_pro(self.fresh()))  # starting a checkout grants nothing

    def test_no_second_subscription_is_started(self):
        self.client.force_login(self.user)
        AccountSubscription.objects.create(user=self.user, stripe_customer_id="cus_1", checkout_session_id="cs_old")
        self.client.post("/billing/checkout/")
        self.stripe.v1.checkout.sessions.expire.assert_called_once_with("cs_old")  # the older open one cannot be paid
        self.stripe.v1.checkout.sessions.create.reset_mock()
        # Stripe already has a subscription (its webhook may not have arrived yet): no new checkout.
        self.stripe.v1.subscriptions.list.return_value = stripe_object({"data": [subscription("incomplete")]})
        response = self.client.post("/billing/checkout/", follow=True)
        self.assertContains(response, "Ai deja un abonament Pro")
        self.stripe.v1.checkout.sessions.create.assert_not_called()

    def test_pro_accounts_are_never_sent_to_checkout(self):
        self.subscribe()
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post("/billing/checkout/"), "/accounts/profile/", fetch_redirect_response=False)
        make_pro(verified_user("staff-pro", "staff-pro@example.com", PASSWORD))
        self.client.force_login(User.objects.get(username="staff-pro"))
        self.client.post("/billing/checkout/")
        self.stripe.v1.checkout.sessions.create.assert_not_called()

    def test_missing_configuration_or_a_stripe_error_is_a_message_not_a_500(self):
        self.client.force_login(self.user)
        with override_settings(BILLING_ENABLED=False):
            response = self.client.post("/billing/checkout/", follow=True)
        self.assertContains(response, "Abonamentele nu pot fi pornite acum")
        self.stripe.v1.checkout.sessions.create.side_effect = stripe.APIConnectionError("down")
        response = self.client.post("/billing/checkout/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Abonamentele nu pot fi pornite acum")
        self.assertFalse(is_pro(self.fresh()))

    def test_returning_to_the_success_page_does_not_make_anyone_pro(self):
        self.client.force_login(self.user)
        page = self.client.get("/billing/success/")
        self.assertContains(page, "Aproape gata")
        self.assertContains(page, '<meta http-equiv="refresh" content="4">')
        self.assertFalse(is_pro(self.fresh()))
        self.subscribe()  # what the webhook does
        page = self.client.get("/billing/success/")
        self.assertContains(page, "Bine ai venit în Pro!")
        self.assertContains(page, '<dialog class="wait-screen event-screen is-pro" open', count=1)
        self.assertContains(page, "Felicitări, acum ești Pro!")
        # Congratulated once per subscription: a reload or a later visit shows only the page.
        self.assertNotContains(self.client.get("/billing/success/"), "event-screen is-")


class WebhookTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        AccountSubscription.objects.create(user=self.user, stripe_customer_id="cus_1")

    def test_only_signed_posts_are_accepted(self):
        self.assertEqual(self.client.get("/billing/webhook/stripe/").status_code, 405)
        payload = event("customer.subscription.created", subscription())
        self.assertEqual(self.webhook(payload, secret="whsec_wrong").status_code, 400)
        self.assertEqual(self.client.post("/billing/webhook/stripe/", b"{}", content_type="application/json").status_code,
                         400)
        self.assertFalse(StripeEvent.objects.exists())
        self.assertFalse(is_pro(self.fresh()))

    def test_subscription_lifecycle(self):
        self.assertEqual(self.webhook(event("customer.subscription.created", subscription())).status_code, 200)
        row = AccountSubscription.objects.get()
        self.assertEqual((row.status, row.stripe_subscription_id, row.stripe_price_id), ("active", "sub_1", "price_unit_pro"))
        self.assertIsNotNone(row.current_period_end)
        self.assertTrue(is_pro(self.fresh()))
        self.stripe.v1.subscriptions.retrieve.return_value = subscription(cancel=True)
        self.webhook(event("customer.subscription.updated", subscription(cancel=True)))
        self.assertTrue(AccountSubscription.objects.get().cancel_at_period_end)
        self.assertTrue(is_pro(self.fresh()))  # still Pro until the period ends
        self.stripe.v1.subscriptions.retrieve.return_value = subscription("past_due")
        self.webhook(event("invoice.payment_failed", {"object": "invoice", "customer": "cus_1",
                                                      "parent": {"subscription_details": {"subscription": "sub_1"}}}))
        self.assertEqual(AccountSubscription.objects.get().status, "past_due")
        self.assertFalse(is_pro(self.fresh()))
        self.stripe.v1.subscriptions.retrieve.return_value = subscription("canceled")
        self.webhook(event("customer.subscription.deleted", subscription("canceled")))
        self.assertEqual(AccountSubscription.objects.get().status, "canceled")
        self.assertEqual(tier_for(self.fresh()), "free")

    def test_state_always_comes_from_stripe_not_from_the_event_body(self):
        # A late "created" event whose body still says active cannot bring back a subscription Stripe has cancelled.
        self.stripe.v1.subscriptions.retrieve.return_value = subscription("canceled")
        self.webhook(event("customer.subscription.created", subscription("active"), created=int(time.time()) - 3600))
        self.assertEqual(AccountSubscription.objects.get().status, "canceled")
        self.assertFalse(is_pro(self.fresh()))

    def test_a_replayed_event_is_handled_once(self):
        payload = event("customer.subscription.created", subscription(), event_id="evt_same")
        self.assertEqual(self.webhook(payload).status_code, 200)
        self.assertEqual(self.webhook(payload).status_code, 200)
        self.assertEqual(self.stripe.v1.subscriptions.retrieve.call_count, 1)
        self.assertEqual(StripeEvent.objects.filter(event_id="evt_same").count(), 1)

    def test_a_stripe_failure_asks_stripe_to_retry_and_changes_nothing(self):
        self.stripe.v1.subscriptions.retrieve.side_effect = stripe.APIConnectionError("down")
        payload = event("customer.subscription.created", subscription(), event_id="evt_retry")
        self.assertEqual(self.webhook(payload).status_code, 500)
        self.assertFalse(StripeEvent.objects.exists())  # rolled back, so the retry is processed in full
        self.stripe.v1.subscriptions.retrieve.side_effect = None
        self.assertEqual(self.webhook(payload).status_code, 200)
        self.assertTrue(is_pro(self.fresh()))

    def test_checkout_completion_links_the_subscription_and_other_events_are_ignored(self):
        AccountSubscription.objects.all().delete()  # the webhook can arrive before the local row exists
        completed = {"object": "checkout.session", "mode": "subscription", "customer": "cus_9", "subscription": "sub_9",
                     "client_reference_id": str(self.user.pk)}
        self.stripe.v1.subscriptions.retrieve.return_value = subscription(sub_id="sub_9", customer="cus_9")
        self.assertEqual(self.webhook(event("checkout.session.completed", completed)).status_code, 200)
        row = AccountSubscription.objects.get()
        self.assertEqual((row.user, row.stripe_customer_id, row.status), (self.user, "cus_9", "active"))
        self.assertEqual(self.webhook(event("charge.refunded", {"object": "charge"})).status_code, 200)
        self.assertEqual(self.webhook(event("checkout.session.completed", {**completed, "mode": "payment"})).status_code,
                         200)

    def test_an_active_subscription_is_not_replaced_by_a_second_ended_one(self):
        self.webhook(event("customer.subscription.created", subscription()))
        self.stripe.v1.subscriptions.retrieve.return_value = subscription("incomplete_expired", sub_id="sub_2")
        self.webhook(event("customer.subscription.updated", subscription("incomplete_expired", sub_id="sub_2")))
        row = AccountSubscription.objects.get()
        self.assertEqual((row.stripe_subscription_id, row.status), ("sub_1", "active"))


class PortalAndDeletionTests(BillingTestCase):
    def test_the_portal_opens_only_for_the_signed_in_account(self):
        self.assertRedirects(self.client.post("/billing/portal/"), "/accounts/login/?next=/billing/portal/",
                             fetch_redirect_response=False)
        self.client.force_login(self.user)
        response = self.client.post("/billing/portal/", follow=True)
        self.assertContains(response, "Nu am putut deschide pagina abonamentului")  # no customer yet
        self.subscribe()
        other = verified_user("bob", "bob@example.com", PASSWORD)
        self.subscribe(user=other, customer="cus_bob", sub_id="sub_bob")
        response = self.client.post("/billing/portal/", {"customer": "cus_bob"})
        self.assertRedirects(response, "https://billing.stripe.test/p", fetch_redirect_response=False)
        self.assertEqual(self.stripe.v1.billing_portal.sessions.create.call_args.args[0],
                         {"customer": "cus_1", "return_url": "https://corect.uk/accounts/profile/"})

    def delete_account(self):
        return self.client.post("/accounts/profile/", {"action": "delete", "delete-password": PASSWORD})

    def test_deleting_an_account_cancels_its_subscription_first(self):
        self.subscribe()
        self.stripe.v1.subscriptions.list.return_value = stripe_object({"data": [subscription(), subscription(
            "canceled", sub_id="sub_old")]})
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/accounts/profile/"), "va fi anulat imediat")
        self.assertRedirects(self.delete_account(), "/", fetch_redirect_response=False)
        self.stripe.v1.subscriptions.cancel.assert_called_once_with("sub_1")  # only what could still charge
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(AccountSubscription.objects.exists())

    def test_the_account_stays_when_stripe_cannot_cancel(self):
        self.subscribe()
        self.stripe.v1.subscriptions.list.return_value = stripe_object({"data": [subscription()]})
        self.stripe.v1.subscriptions.cancel.side_effect = stripe.APIConnectionError("down")
        self.client.force_login(self.user)
        response = self.delete_account()
        self.assertRedirects(response, "/accounts/profile/", fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertContains(self.client.get("/accounts/profile/"), "nu am șters contul")
        with override_settings(BILLING_ENABLED=False):  # nor when Stripe cannot be asked at all
            self.delete_account()
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_accounts_without_a_subscription_are_deleted_without_asking_stripe(self):
        self.client.force_login(self.user)
        self.delete_account()
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.stripe.v1.subscriptions.list.assert_not_called()


class PlanPagesTests(BillingTestCase):
    def test_profile_shows_the_right_plan_controls(self):
        self.client.force_login(self.user)
        page = self.client.get("/accounts/profile/")
        self.assertContains(page, "Planul tău: Free")
        self.assertContains(page, 'action="/billing/checkout/"')
        self.assertContains(page, "Treci la Pro")
        self.assertNotContains(page, "Gestionează abonamentul")
        self.assertNotContains(page, "cus_")  # no Stripe identifiers on the page
        self.assertContains(page, '<span class="email-state is-verified">confirmat</span>')
        row = self.subscribe(current_period_end=timezone.now() + timedelta(days=12))
        page = self.client.get("/accounts/profile/")
        self.assertContains(page, "Planul tău: Pro")
        self.assertContains(page, "Abonament Pro activ. Se reînnoiește pe")
        self.assertContains(page, 'action="/billing/portal/"')
        self.assertNotContains(page, "Treci la Pro")
        row.cancel_at_period_end = True
        row.save()
        self.assertContains(self.client.get("/accounts/profile/"), "Abonamentul Pro este anulat și se încheie pe")
        row.status = "past_due"
        row.save()
        page = self.client.get("/accounts/profile/")
        self.assertContains(page, "Plata abonamentului Pro nu a reușit")
        self.assertContains(page, "Gestionează abonamentul")

    def test_manual_pro_has_no_billing_controls(self):
        make_pro(self.user)
        self.client.force_login(self.user)
        page = self.client.get("/accounts/profile/")
        self.assertContains(page, "acordat de echipa Corect.uk")
        self.assertNotContains(page, "Gestionează abonamentul")
        self.assertNotContains(page, "Treci la Pro")

    def test_plan_cards_for_free_and_paying_accounts(self):
        self.client.force_login(self.user)
        home = self.client.get("/").content.decode()
        self.assertIn('<form method="post" action="/billing/checkout/" data-wait="Te ducem la plata securizată…">', home)
        self.assertIn('data-funnel-cta="pricing_section">Alege Pro</button>', home)
        self.subscribe()
        home = self.client.get("/").content.decode()
        self.assertIn('<h2 id="plans-title">Planul tău: Pro</h2>', home)
        self.assertIn('action="/billing/portal/"', home)
        self.assertNotIn("/billing/checkout/", home)

    def test_free_accounts_get_the_pro_screen_and_keep_recent_history(self):
        self.client.force_login(self.user)
        for path in ("/learn/", "/progress/", "/mistakes/", "/mistakes/verb_form/", "/practice/"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertContains(page, "Disponibil în Pro")
                self.assertContains(page, 'action="/billing/checkout/"')
        old = AssistantRequest.objects.create(user=self.user, request_type="correction", original_text="Old text.",
                                              result_text="Old text.", model_used="m", prompt_version="v",
                                              status="success")
        AssistantRequest.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=45))
        recent = AssistantRequest.objects.create(user=self.user, request_type="correction", original_text="New text.",
                                                 result_text="New text.", model_used="m", prompt_version="v",
                                                 status="success")
        history = self.client.get("/history/")
        self.assertContains(history, "Planul Free arată ultimele 30 de zile")
        self.assertContains(history, f"/history/{recent.pk}/")
        self.assertNotContains(history, f"/history/{old.pk}/")
        self.assertContains(self.client.get(f"/history/{old.pk}/"), "Disponibil în Pro")
        self.subscribe()
        self.assertEqual(self.client.get("/progress/").status_code, 200)
        self.assertNotContains(self.client.get("/progress/"), "Disponibil în Pro")
        self.assertContains(self.client.get("/history/"), f"/history/{old.pk}/")  # all of it comes back with Pro


class ConfigurationCheckTests(TestCase):
    def ids(self, messages):
        return [message.id for message in messages]

    def test_billing_is_all_or_nothing_and_production_is_told(self):
        with override_settings(STRIPE_SECRET_KEY="sk_live_x", STRIPE_WEBHOOK_SECRET="", STRIPE_PRO_PRICE_ID=""):
            self.assertEqual(self.ids(billing_configured(None)), ["core.E006"])
        with override_settings(STRIPE_SECRET_KEY="", STRIPE_WEBHOOK_SECRET="", STRIPE_PRO_PRICE_ID="", DEBUG=False):
            self.assertEqual(self.ids(billing_configured(None)), ["core.W002"])
        with override_settings(STRIPE_SECRET_KEY="sk_test_x", DEBUG=False):
            self.assertEqual(self.ids(billing_configured(None)), ["core.W003"])
        with override_settings(STRIPE_SECRET_KEY="sk_live_x", DEBUG=False):
            self.assertEqual(billing_configured(None), [])

    def test_production_email_and_google_configuration(self):
        with override_settings(DEBUG=False, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
                               DEFAULT_FROM_EMAIL="Corect.uk <no-reply@localhost>"):
            self.assertEqual(self.ids(email_delivery_configured(None)), ["core.E005", "core.E005"])
        with override_settings(DEBUG=False, EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="",
                               DEFAULT_FROM_EMAIL="Corect.uk <no-reply@corect.uk>"):
            self.assertEqual(self.ids(email_delivery_configured(None)), ["core.E005"])
        with override_settings(DEBUG=False, EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                               EMAIL_HOST="smtp.example.com", DEFAULT_FROM_EMAIL="Corect.uk <no-reply@corect.uk>"):
            self.assertEqual(email_delivery_configured(None), [])
        resend = "anymail.backends.resend.EmailBackend"
        with override_settings(DEBUG=False, EMAIL_BACKEND=resend, ANYMAIL={"RESEND_API_KEY": ""},
                               DEFAULT_FROM_EMAIL="Corect.uk <no-reply@corect.uk>"):
            self.assertEqual(self.ids(email_delivery_configured(None)), ["core.E005"])
        with override_settings(DEBUG=False, EMAIL_BACKEND=resend, ANYMAIL={"RESEND_API_KEY": "re_x"}, EMAIL_HOST="",
                               DEFAULT_FROM_EMAIL="Corect.uk <no-reply@corect.uk>"):
            self.assertEqual(email_delivery_configured(None), [])  # no SMTP host needed
        with override_settings(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET=""):
            self.assertEqual(self.ids(google_login_configured(None)), ["core.E007"])
