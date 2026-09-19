"""Pro purchase, billing management and Stripe's webhook. The browser can start a checkout or open the portal for its own
account, but never says who is Pro: only the verified webhook changes a subscription (services.apply_event)."""
import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.analytics.models import FunnelEvent
from apps.analytics.services.funnel import record_funnel_event
from apps.core.events import PRO, announce
from apps.core.plans import is_pro, pro_source, PAID
from . import services
from .models import StripeEvent

logger = logging.getLogger("apps.billing")

UNAVAILABLE = "Abonamentele nu pot fi pornite acum. Încearcă din nou în câteva minute."
ALREADY = "Ai deja un abonament Pro. Îl poți gestiona din contul tău."
PRO_WELCOME = "Ai cereri nelimitate (Fair Use), progresul, greșelile pe categorii, exercițiile și tot istoricul."
PRO_WELCOMED = "corect_pro_welcomed"  # session: the subscription already congratulated, so a reload does not repeat it
PORTAL_UNAVAILABLE = "Nu am putut deschide pagina abonamentului. Încearcă din nou în câteva minute."


def plans_url():
    return reverse("home") + "#plans"


@require_POST
@login_required
def checkout(request):
    if not settings.BILLING_ENABLED:
        messages.info(request, UNAVAILABLE)
        return redirect(plans_url())
    try:
        url = services.start_checkout(request)
    except services.AlreadySubscribed:
        messages.info(request, ALREADY)
        return redirect("profile")
    except services.BillingUnavailable:
        messages.error(request, UNAVAILABLE)
        return redirect(plans_url())
    record_funnel_event(request, FunnelEvent.Name.CHECKOUT_STARTED, "pricing_section")
    return redirect(url)


@require_GET
@login_required
@never_cache
def success(request):
    """Stripe sends the buyer here. The page only reports what the webhook has recorded; it never grants Pro."""
    active = is_pro(request.user) and pro_source(request.user) == PAID
    if active:
        record_funnel_event(request, FunnelEvent.Name.SUBSCRIPTION_STARTED, "checkout")
        subscription = services.subscription_for(request.user)
        if request.session.get(PRO_WELCOMED) != subscription.pk:
            request.session[PRO_WELCOMED] = subscription.pk
            announce(request, PRO, PRO_WELCOME)
    return render(request, "billing/success.html", {"active": active})


@require_POST
@login_required
def portal(request):
    try:
        return redirect(services.open_portal(request))
    except services.BillingUnavailable:
        messages.error(request, PORTAL_UNAVAILABLE)
        return redirect("profile")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not settings.BILLING_ENABLED:
        return HttpResponse(status=503)  # Stripe retries: nothing is lost while billing is being configured
    try:
        event = stripe.Webhook.construct_event(request.body, request.headers.get("Stripe-Signature", ""),
                                               settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning("stripe_webhook_rejected reason=signature")
        return HttpResponse(status=400)
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    StripeEvent.objects.create(event_id=event["id"], type=event["type"][:100])
            except IntegrityError:
                logger.info("stripe_event_duplicate type=%s", event["type"])
                return HttpResponse(status=200)
            outcome = services.apply_event(event)
    except (stripe.StripeError, services.BillingUnavailable) as exc:
        # Rolled back, including the event row, so Stripe's retry is handled in full.
        logger.warning("stripe_event_failed type=%s error=%s", event["type"], type(exc).__name__)
        return HttpResponse(status=500)
    logger.info("stripe_event type=%s outcome=%s", event["type"], outcome)
    return HttpResponse(status=200)
