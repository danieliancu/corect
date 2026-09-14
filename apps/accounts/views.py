from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.analytics.models import AnonymousVisitor
from apps.analytics.services.visitors import cookie_visitor_id, delete_visitor_cookie, link_visitor
from apps.core.consent import analytics_chosen, record_acceptance, set_consent_cookie
from .forms import DeleteAccountForm, ProfileForm, SignupForm
from .models import LegalAcceptance


@never_cache
def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            record_acceptance(user, LegalAcceptance.Source.SIGNUP)
        link_visitor(request, user, via="signup")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        # The browser has now accepted the current versions too; the analytics choice is kept as it was.
        return set_consent_cookie(redirect("home"), analytics_chosen(request))
    return render(request, "accounts/form.html", {"form": form, "title": "Creează-ți contul",
        "intro": "Păstrează-ți istoricul. Învață din greșeli. Vezi cât de departe ajungi.", "button": "Creează cont",
        "signup": True})


@login_required
@never_cache
def profile(request):
    # Independent forms share the page; the submit button's form says which one was sent.
    action = request.POST.get("action") if request.method == "POST" else None
    username = request.user.username  # captured before an invalid form can change the instance in memory
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST if action == "password" else None)
    password_form.fields["old_password"].widget.attrs.pop("autofocus", None)  # don't jump to the second form
    delete_form = DeleteAccountForm(request.user, request.POST if action == "delete" else None, prefix="delete")
    if action == "profile" and profile_form.is_valid():
        profile_form.save()
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
        "delete_form": delete_form, "username": username})
