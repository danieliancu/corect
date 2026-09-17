"""Accounts created before an email address was required are asked for one before they carry on.

Only the page for adding it, signing out, the legal pages and technical endpoints stay open. No extra query: the user
is loaded anyway, and anonymous visitors and accounts with an address pass straight through.
"""
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .emails import EMAIL_REQUIRED

ALLOWED_URL_NAMES = frozenset({"profile", "logout", "privacy", "terms", "contact", "consent", "healthz", "readyz",
                               "robots_txt", "sitemap_xml", "funnel_event"})
ALLOWED_PREFIXES = ("/admin/", "/static/")
JSON_PREFIXES = ("/assistant/",)


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
        if user is None or not user.is_authenticated or user.email:
            return None
        target = reverse("profile") + "?email_required=1"
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        if request.path.startswith(JSON_PREFIXES):
            return JsonResponse({"error": EMAIL_REQUIRED, "code": "email_required"}, status=403)
        return redirect(target)
