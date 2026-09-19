"""The only module that talks to Stripe. Pro is sold as one monthly subscription through Stripe Checkout; Stripe's
webhooks, not the browser, decide the local state (AccountSubscription), and plans.is_pro() reads that state.

Every change to a subscription row happens under a row lock and re-reads the subscription from Stripe, so duplicate,
late or out-of-order webhooks cannot leave a stale state behind. Nothing here logs addresses, payloads or secrets.
"""
import logging
from datetime import datetime, timezone as dt_timezone
from functools import lru_cache

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse

from .models import AccountSubscription

logger = logging.getLogger("apps.billing")

# The one rule for which Stripe statuses give Pro. A subscription set to cancel at the period end stays "active" (Pro)
# until Stripe ends it; past_due, unpaid, incomplete, incomplete_expired, paused and canceled are Free.
ENTITLED_STATUSES = frozenset({"active", "trialing"})
# Statuses of a subscription that still exists in Stripe and can charge or become active again.
LIVE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid", "incomplete", "paused"})


class BillingUnavailable(Exception):
    """Stripe is not configured or did not answer: nothing was changed, and the user is told to try again."""


class AlreadySubscribed(Exception):
    """The account already has a subscription (possibly still being confirmed): no second one is started."""


def grants_pro(status):
    return status in ENTITLED_STATUSES


def has_entitled_subscription(user):
    return AccountSubscription.objects.filter(user=user, status__in=ENTITLED_STATUSES).exists()


def subscription_for(user):
    return AccountSubscription.objects.filter(user=user).first() if user.is_authenticated else None


@lru_cache(maxsize=2)
def _client(api_key, timeout):
    return stripe.StripeClient(api_key, max_network_retries=2, http_client=stripe.RequestsClient(timeout=timeout))


def client():
    if not settings.BILLING_ENABLED:
        raise BillingUnavailable("billing_disabled")
    return _client(settings.STRIPE_SECRET_KEY, settings.STRIPE_TIMEOUT)


def absolute_url(request, path):
    """SITE_URL in production (never the request's Host header); the request's own origin locally."""
    base = settings.SITE_URL or request.build_absolute_uri("/").rstrip("/")
    return base + path


def _customer_row(user):
    """The account's row, creating its Stripe customer the first time. The idempotency key makes two concurrent first
    purchases share one customer; the unique user column keeps one row."""
    row = AccountSubscription.objects.filter(user=user).first()
    if row is not None:
        return row
    customer = client().v1.customers.create(
        {"email": user.email, "metadata": {"user_id": str(user.pk)}},
        {"idempotency_key": f"corect-customer-{user.pk}"})
    try:
        with transaction.atomic():
            return AccountSubscription.objects.create(user=user, stripe_customer_id=customer.id)
    except IntegrityError:
        return AccountSubscription.objects.get(user=user)


def start_checkout(request):
    """A Stripe Checkout URL for Pro. Refuses when Stripe already has a live subscription for the account, and expires the
    account's previous still-open Checkout Session first, so two tabs can never pay for two subscriptions."""
    from apps.core.plans import is_pro

    user = request.user
    if is_pro(user):
        raise AlreadySubscribed("already_pro")
    stripe_client = client()
    try:
        row = _customer_row(user)
        with transaction.atomic():
            row = AccountSubscription.objects.select_for_update().get(pk=row.pk)
            live = stripe_client.v1.subscriptions.list(
                {"customer": row.stripe_customer_id, "status": "all", "limit": 20})
            if any(item.status in LIVE_STATUSES for item in live.data):
                raise AlreadySubscribed("subscription_exists")
            if row.checkout_session_id:
                previous = stripe_client.v1.checkout.sessions.retrieve(row.checkout_session_id)
                if previous.status == "complete" and previous.payment_status in ("paid", "no_payment_required"):
                    raise AlreadySubscribed("checkout_completed")  # paid; its webhook is on its way
                if previous.status == "open":
                    stripe_client.v1.checkout.sessions.expire(row.checkout_session_id)
            session = stripe_client.v1.checkout.sessions.create({
                "mode": "subscription",
                "customer": row.stripe_customer_id,
                "client_reference_id": str(user.pk),
                "line_items": [{"price": settings.STRIPE_PRO_PRICE_ID, "quantity": 1}],
                "subscription_data": {"metadata": {"user_id": str(user.pk)}},
                "success_url": absolute_url(request, reverse("billing_success")),
                "cancel_url": absolute_url(request, reverse("home") + "#plans"),
                "locale": "ro",
            })
            row.checkout_session_id = session.id
            row.save(update_fields=["checkout_session_id", "updated_at"])
    except stripe.StripeError as exc:
        logger.warning("stripe_checkout_failed error=%s", type(exc).__name__)
        raise BillingUnavailable("stripe_error") from None
    logger.info("stripe_checkout_started user=%s", user.pk)
    return session.url


def open_portal(request):
    """A Stripe Customer Portal URL for the signed-in account's own customer (no identifier ever comes from the browser)."""
    row = subscription_for(request.user)
    if row is None:
        raise BillingUnavailable("no_customer")
    try:
        session = client().v1.billing_portal.sessions.create(
            {"customer": row.stripe_customer_id, "return_url": absolute_url(request, reverse("profile"))})
    except stripe.StripeError as exc:
        logger.warning("stripe_portal_failed error=%s", type(exc).__name__)
        raise BillingUnavailable("stripe_error") from None
    return session.url


def cancel_for_deletion(user):
    """Before an account is deleted: cancel, immediately, every subscription of its customer that could still charge.
    Raises BillingUnavailable (and the account is kept) when that cannot be done, so a deleted account is never billed."""
    row = subscription_for(user)
    if row is None:
        return
    if not settings.BILLING_ENABLED and row.status not in LIVE_STATUSES:
        return  # nothing recorded can still charge, and Stripe cannot be asked
    try:
        stripe_client = client()
        live = stripe_client.v1.subscriptions.list({"customer": row.stripe_customer_id, "status": "all", "limit": 20})
        for item in live.data:
            if item.status in LIVE_STATUSES:
                stripe_client.v1.subscriptions.cancel(item.id)
                logger.info("stripe_subscription_cancelled_for_deletion user=%s", user.pk)
    except stripe.StripeError as exc:
        logger.warning("stripe_cancel_for_deletion_failed error=%s", type(exc).__name__)
        raise BillingUnavailable("stripe_error") from None


# ----- Webhooks -----

HANDLED_EVENTS = frozenset({
    "checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted", "customer.subscription.paused", "customer.subscription.resumed",
    "invoice.paid", "invoice.payment_failed",
})


def _value(obj, *path):
    """obj["a"]["b"] or None, for Stripe objects and plain dicts alike (fields moved between API versions)."""
    for key in path:
        if obj is None:
            return None
        try:
            obj = obj[key]
        except (KeyError, TypeError, IndexError):
            return None
    return obj


def _event_target(event):
    """(customer id, subscription id, user id from our own metadata) named by an event, or None to ignore it."""
    obj = _value(event, "data", "object")
    kind = event["type"]
    if kind == "checkout.session.completed":
        if _value(obj, "mode") != "subscription":
            return None
        return _value(obj, "customer"), _value(obj, "subscription"), _value(obj, "client_reference_id")
    if kind.startswith("customer.subscription."):
        return _value(obj, "customer"), _value(obj, "id"), _value(obj, "metadata", "user_id")
    if kind.startswith("invoice."):
        subscription_id = (_value(obj, "parent", "subscription_details", "subscription") or _value(obj, "subscription"))
        return _value(obj, "customer"), subscription_id, _value(obj, "parent", "subscription_details", "metadata", "user_id")
    return None


def _row_for(customer_id, user_id):
    """The locked row for a Stripe customer; created from our metadata when the checkout webhook arrives first."""
    row = AccountSubscription.objects.select_for_update().filter(stripe_customer_id=customer_id).first()
    if row is not None or not user_id or not str(user_id).isdigit():
        return row
    user = get_user_model().objects.filter(pk=int(user_id)).first()
    if user is None:
        return None  # the account was deleted; its subscription was cancelled at deletion
    row, _ = AccountSubscription.objects.select_for_update().get_or_create(
        user=user, defaults={"stripe_customer_id": customer_id})
    return row if row.stripe_customer_id == customer_id else None


def _period_end(subscription):
    value = _value(subscription, "items", "data", 0, "current_period_end") or _value(subscription, "current_period_end")
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


def apply_event(event):
    """Brings the local row in line with Stripe for one verified event. Runs inside the webhook's transaction; a Stripe
    error propagates, the transaction (including the event's idempotency row) rolls back and Stripe retries."""
    if event["type"] not in HANDLED_EVENTS:
        return "ignored"
    target = _event_target(event)
    if target is None or not target[0] or not target[1]:
        return "ignored"
    customer_id, subscription_id, user_id = target
    row = _row_for(customer_id, user_id)
    if row is None:
        logger.info("stripe_event_without_account type=%s", event["type"])
        return "no_account"
    # Always the subscription as Stripe has it now: late or reordered events then carry no stale state.
    subscription = client().v1.subscriptions.retrieve(subscription_id)
    status = subscription.status
    if row.stripe_subscription_id and row.stripe_subscription_id != subscription.id:
        # Another subscription of the same customer: it replaces the recorded one only if that one no longer gives Pro.
        if grants_pro(row.status) and not grants_pro(status):
            logger.warning("stripe_other_subscription_ignored user=%s", row.user_id)
            return "ignored"
    row.stripe_subscription_id = subscription.id
    row.stripe_price_id = _value(subscription, "items", "data", 0, "price", "id") or ""
    row.status = status
    row.current_period_end = _period_end(subscription)
    row.cancel_at_period_end = bool(_value(subscription, "cancel_at_period_end"))
    row.last_event_created = max(row.last_event_created, int(event["created"] or 0))
    row.checkout_session_id = ""  # its purchase is now recorded; a later new purchase needs no check against it
    row.save()
    logger.info("stripe_subscription_synced user=%s status=%s", row.user_id, status)
    return "synced"
