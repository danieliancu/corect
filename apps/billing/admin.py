"""Support view of subscriptions and handled Stripe events: identifiers and states only, never changed by hand (Stripe's
webhooks own them). Manual Pro is the "Pro" group on the user, not a subscription (apps/core/plans.py)."""
from django.contrib import admin

from .models import AccountSubscription, StripeEvent
from .services import grants_pro


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AccountSubscription)
class AccountSubscriptionAdmin(ReadOnlyAdmin):
    list_display = ("user", "status", "paid_pro", "cancel_at_period_end", "current_period_end", "updated_at")
    list_filter = ("status", "cancel_at_period_end")
    search_fields = ("user__username", "stripe_customer_id", "stripe_subscription_id")
    readonly_fields = ("user", "stripe_customer_id", "stripe_subscription_id", "stripe_price_id", "status",
                       "current_period_end", "cancel_at_period_end", "checkout_session_id", "last_event_created",
                       "created_at", "updated_at")

    @admin.display(boolean=True, description="Paid Pro")
    def paid_pro(self, obj):
        return grants_pro(obj.status)


@admin.register(StripeEvent)
class StripeEventAdmin(ReadOnlyAdmin):
    list_display = ("type", "event_id", "received_at")
    list_filter = ("type",)
    search_fields = ("event_id",)
    readonly_fields = ("event_id", "type", "received_at")
