from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.analytics.services.visitors import link_visitor
from .forms import ProfileForm, SignupForm


@never_cache
def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        link_visitor(request, user, via="signup")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("home")
    return render(request, "accounts/form.html", {"form": form, "title": "Create your account",
        "intro": "Keep your history. Learn from your mistakes. See how far you go.", "button": "Create account", "signup": True})


@login_required
@never_cache
def profile(request):
    # Two independent forms share the page; the submit button's form says which one was sent.
    action = request.POST.get("action") if request.method == "POST" else None
    username = request.user.username  # captured before an invalid form can change the instance in memory
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST if action == "password" else None)
    password_form.fields["old_password"].widget.attrs.pop("autofocus", None)  # don't jump to the second form
    if action == "profile" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("profile")
    if action == "password" and password_form.is_valid():
        update_session_auth_hash(request, password_form.save())
        messages.success(request, "Your password has been changed.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"profile_form": profile_form, "password_form": password_form,
        "username": username})
