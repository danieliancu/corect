from allauth.account.internal.flows.email_verification import send_verification_email_to_address
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.analytics.models import AnonymousVisitor
from apps.analytics.services.visitors import cookie_visitor_id, delete_visitor_cookie
from apps.assistant.services import quota
from apps.assistant.services.limits import actor_key
from apps.billing.services import BillingUnavailable, cancel_for_deletion, grants_pro, subscription_for
from apps.core.events import GOODBYE, UPDATED, announce
from apps.core.plans import PAID, forget_plan, pro_source, tier_for
from .forms import DeleteAccountForm, ProfileForm, ResendVerificationForm

GOODBYE_TEXT = "Contul tău și istoricul au fost șterse. Îți mulțumim că ai folosit Corect.uk."
DELETION_BLOCKED = ("Nu am putut anula abonamentul Pro, așa că nu am șters contul. Încearcă din nou în câteva minute; "
                    "nimic nu a fost modificat.")


RESEND_DONE = ("Dacă adresa aparține unui cont care așteaptă confirmarea, ți-am trimis din nou linkul. Verifică și "
               "dosarul Spam; un link nou poate fi cerut o dată la câteva minute.")


@require_POST
def resend_verification(request):
    """The confirmation link again, for someone who cannot sign in yet. The same answer whatever the address, so it
    reveals nothing about accounts, and allauth's per-address cooldown stops it being used to flood an inbox."""
    form = ResendVerificationForm(request.POST)
    if form.is_valid():
        address = EmailAddress.objects.filter(email__iexact=form.cleaned_data["email"], verified=False).first()
        if address is not None:
            send_verification_email_to_address(request, address)
    messages.info(request, RESEND_DONE)
    return redirect("account_email_verification_sent")


def password_form_for(user, data):
    # An account created with Google has no password yet: it can set one instead of changing it.
    if user.has_usable_password():
        form = PasswordChangeForm(user, data)
        form.fields["old_password"].widget.attrs.pop("autofocus", None)  # don't jump to the second form
    else:
        form = SetPasswordForm(user, data)
    return form


@login_required
@never_cache
def profile(request):
    # Independent forms share the page; the submit button's form says which one was sent.
    action = request.POST.get("action") if request.method == "POST" else None
    user = request.user
    username = user.username  # captured before an invalid form can change the instance in memory
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=user)
    password_form = password_form_for(user, request.POST if action == "password" else None)
    delete_form = DeleteAccountForm(user, request.POST if action == "delete" else None, prefix="delete")
    if action == "profile" and profile_form.is_valid():
        profile_form.save()
        announce(request, UPDATED, "Profilul tău a fost actualizat.")
        return redirect("profile")
    if action == "password" and password_form.is_valid():
        update_session_auth_hash(request, password_form.save())
        announce(request, UPDATED, "Parola ta a fost salvată.")
        return redirect("profile")
    if action == "delete" and delete_form.is_valid():
        try:
            # A paid subscription is cancelled in Stripe first; if that fails, the account stays as it is.
            cancel_for_deletion(user)
        except BillingUnavailable:
            messages.error(request, DELETION_BLOCKED)
            return redirect("profile")
        with transaction.atomic():
            # Visitor IDs tied to this account or this browser go too, so the usage statistics that remain (no text,
            # audio or IP) are linked to neither the account nor a visitor ID.
            AnonymousVisitor.objects.filter(Q(converted_user=user) | Q(pk=cookie_visitor_id(request))).delete()
            user.delete()  # History, corrections, acceptance records and the subscription row go with the account.
        forget_plan(user)
        logout(request)
        announce(request, GOODBYE, GOODBYE_TEXT)
        return delete_visitor_cookie(redirect("home"))
    addresses = EmailAddress.objects.filter(user=user).order_by("-primary", "pk")
    primary = addresses.first()  # the primary address, or the one just added by an account that had none
    pending = addresses.filter(verified=False).exclude(pk=getattr(primary, "pk", None)).first()
    subscription = subscription_for(user)
    return render(request, "accounts/profile.html", {
        "profile_form": profile_form, "password_form": password_form, "delete_form": delete_form,
        "username": username, "email_required": not (primary and primary.verified),
        "primary_email": primary, "pending_email": pending,
        "quota": quota.status(actor_key(request), tier_for(user)),
        "pro_source": pro_source(user), "paid": pro_source(user) == PAID, "subscription": subscription,
        "subscription_live": bool(subscription and grants_pro(subscription.status)),
        "billing_enabled": settings.BILLING_ENABLED,
        "google_enabled": settings.GOOGLE_LOGIN_ENABLED,
        "google_connected": SocialAccount.objects.filter(user=user, provider="google").exists()})
