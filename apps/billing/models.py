"""Local copy of each account's Stripe subscription, kept current by Stripe's webhooks (apps/billing/services.py).

Only identifiers and states are stored: no card or payment details ever reach Corect.
"""
from django.conf import settings
from django.db import models


class AccountSubscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription")
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    stripe_subscription_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    # Stripe's own value (active, trialing, past_due, unpaid, canceled, incomplete, incomplete_expired, paused);
    # blank until Stripe reports a subscription. services.grants_pro() is the one rule that reads it.
    status = models.CharField(max_length=32, blank=True, db_index=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    # The Checkout Session opened last: a new one expires it first, so only one can ever be paid.
    checkout_session_id = models.CharField(max_length=255, blank=True)
    # Unix time of the newest Stripe event applied; older, late-arriving events are not applied over it.
    last_event_created = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "account subscription"

    def __str__(self):
        return f"{self.user} · {self.status or 'no subscription'}"


class StripeEvent(models.Model):
    """Every Stripe event handled once: a replayed or duplicated delivery finds its row and changes nothing."""

    event_id = models.CharField(max_length=255, unique=True)
    type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-received_at",)
        verbose_name = "Stripe event"

    def __str__(self):
        return f"{self.type} {self.event_id}"
