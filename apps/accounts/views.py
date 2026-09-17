from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.analytics.models import AnonymousVisitor
from apps.analytics.services.visitors import cookie_visitor_id, delete_visitor_cookie, link_visitor
from apps.assistant.services import quota
from apps.assistant.services.limits import actor_key
from apps.core.consent import analytics_chosen, record_acceptance, set_consent_cookie
from apps.core.plans import tier_for
from .emails import EMAIL_TAKEN, is_email_conflict
from .forms import DeleteAccountForm, ProfileForm, SignupForm
from .models import LegalAcceptance


@never_cache
def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    user = None
    if request.method == "POST" and form.is_valid():
        user = save_or_report_email_conflict(form, lambda: record_acceptance(form.instance, LegalAcceptance.Source.SIGNUP))
    if user is not None:
        link_visitor(request, user, via="signup")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        # The browser has now accepted the current versions too; the analytics choice is kept as it was.
        return set_consent_cookie(redirect("home"), analytics_chosen(request))
    return render(request, "accounts/form.html", {"form": form, "title": "Creează-ți contul",
        "intro": "Păstrează-ți istoricul. Învață din greșeli. Vezi cât de departe ajungi.", "button": "Creează cont",
        "signup": True})


def save_or_report_email_conflict(form, after_save=None):
    """Saves the form; when a concurrent request took the same address first, the database index refuses the row and
    the form shows the usual "already used" error instead of a server error."""
    try:
        with transaction.atomic():
            user = form.save()
            if after_save is not None:
                after_save()
            return user
    except IntegrityError as exc:
        if not is_email_conflict(exc):
            raise
        form.add_error("email", EMAIL_TAKEN)
        return None


@login_required
@never_cache
def profile(request):
    # Independent forms share the page; the submit button's form says which one was sent.
    action = request.POST.get("action") if request.method == "POST" else None
    username = request.user.username  # captured before an invalid form can change the instance in memory
    email_required = not request.user.email  # an account created before emails were required
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST if action == "password" else None)
    password_form.fields["old_password"].widget.attrs.pop("autofocus", None)  # don't jump to the second form
    delete_form = DeleteAccountForm(request.user, request.POST if action == "delete" else None, prefix="delete")
    if action == "profile" and profile_form.is_valid() and save_or_report_email_conflict(profile_form):
        messages.success(request, "Profilul tău a fost actualizat.")
        return redirect("profile")
    if action == "password" and password_form.is_valid():
        update_session_auth_hash(request, password_form.save())
        messages.success(request, "Parola ta a fost schimbată.")
        return redirect("profile")
    if action == "delete" and delete_form.is_valid():
        user = request.user
        with transaction.atomic():
            # Visitor IDs tied to this account or this browser go too, so the usage statistics that remain (no text,
            # audio or IP) are linked to neither the account nor a visitor ID.
            AnonymousVisitor.objects.filter(Q(converted_user=user) | Q(pk=cookie_visitor_id(request))).delete()
            user.delete()  # History, corrections and acceptance records are deleted with the account.
        logout(request)
        messages.success(request, "Contul tău și istoricul au fost șterse.")
        return delete_visitor_cookie(redirect("home"))
    return render(request, "accounts/profile.html", {"profile_form": profile_form, "password_form": password_form,
        "delete_form": delete_form, "username": username, "email_required": email_required,
        "quota": quota.status(actor_key(request), tier_for(request.user))})
