"""A signed-in account without a verified address is asked to add or confirm one before it carries on.

New accounts cannot sign in before confirming (ACCOUNT_EMAIL_VERIFICATION = "mandatory"), so in practice this concerns
accounts created before addresses were required, and a legacy account whose new address is still waiting for its link.
Only the pages for adding and confirming an address, the account page, signing out, the legal pages and technical
endpoints stay open. Anonymous visitors pass straight through; signed-in requests cost one indexed query.
"""
from allauth.account.models import EmailAddress
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .emails import EMAIL_REQUIRED

ALLOWED_URL_NAMES = frozenset({
    "profile", "logout", "login", "signup", "privacy", "terms", "contact", "consent", "healthz", "readyz", "robots_txt",
    "sitemap_xml", "funnel_event", "stripe_webhook", "billing_portal", "resend_verification",
    # django-allauth: addresses, confirmation, passwords, Google and signing out.
    "account_email", "account_confirm_email", "account_email_verification_sent", "account_logout", "account_login",
    "account_signup", "account_change_password", "account_set_password", "account_reset_password",
    "account_reset_password_done", "account_reset_password_from_key", "account_reset_password_from_key_done",
    "account_reauthenticate", "socialaccount_connections", "socialaccount_signup", "socialaccount_login_cancelled",
    "socialaccount_login_error", "google_login", "google_callback",
})
ALLOWED_PREFIXES = ("/admin/", "/static/")
JSON_PREFIXES = ("/assistant/",)


def has_verified_address(user):
    """A verified primary address; or, for an account created by staff or a management command (an address but no
    allauth address row: the public signup always creates one), its address — allauth still verifies it at the next
    password sign-in (ACCOUNT_EMAIL_VERIFICATION)."""
    cached = getattr(user, "_corect_verified_address", None)
    if cached is None:
        rows = list(EmailAddress.objects.filter(user=user).values_list("primary", "verified"))
        cached = any(primary and verified for primary, verified in rows) if rows else bool(user.email)
        user._corect_verified_address = cached
    return cached


class EmailRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if (match is not None and match.url_name in ALLOWED_URL_NAMES) or request.path.startswith(ALLOWED_PREFIXES):
            return None
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or has_verified_address(user):
            return None
        target = reverse("profile") + "?email_required=1"
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        if request.path.startswith(JSON_PREFIXES):
            return JsonResponse({"error": EMAIL_REQUIRED, "code": "email_required"}, status=403)
        return redirect(target)
