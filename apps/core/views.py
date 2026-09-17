from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.analytics.services.visitors import delete_visitor_cookie
from apps.assistant.services.voice import REALTIME_CALLS_URL
from .consent import acceptance_required, analytics_chosen, record_acceptance, set_consent_cookie
from .legal import retention_facts
from .plans import display_plans
from .seo import structured_data

# The browser opens its connection to this origin only once the learner reaches for the microphone, never on page load.
REALTIME_ORIGIN = "{0.scheme}://{0.netloc}".format(urlsplit(REALTIME_CALLS_URL))


# The hero picture of Westminster (templates/core/partials/hero_art.html), shown from 1024px. To replace it, put the new
# file in static/img and change its path and pixel size here; the layout does not change.
HERO_ART = {"desktop": "img/hero.webp", "width": 1774, "height": 887}


def home_context(**kwargs):
    return {"max_characters": settings.ASSISTANT_MAX_CHARACTERS, "voice_max_seconds": settings.VOICE_MAX_SECONDS,
            "voice_realtime_enabled": settings.VOICE_REALTIME_ENABLED, "voice_realtime_origin": REALTIME_ORIGIN,
            "voice_trailing_ms": settings.VOICE_TRAILING_AUDIO_MS, "voice_final_ms": settings.VOICE_FINAL_TRANSCRIPT_MS,
            "submission_token": str(uuid4()), "plans": display_plans(), "hero_art": HERO_ART, **kwargs}


@never_cache
def home(request):
    return render(request, "core/home.html", home_context(structured_data=structured_data(request)))


def about_page(request):
    """The homepage's landing sections, from "De ce să alegi Corect.uk" down, as a page of their own: phones and
    tablets reach it from the "?" in the header and "Despre" in the menu. Links to the editor lead back home."""
    return render(request, "core/about.html", {"plans": display_plans(), "editor_url": f"{reverse('home')}#text"})


def privacy_page(request):
    return render(request, "core/privacy.html", {"visitor_cookie": settings.ANALYTICS_VISITOR_COOKIE,
                                                 "retention": retention_facts()})


def terms_page(request):
    return render(request, "core/terms.html")


def contact_page(request):
    if not (settings.CONTACT_EMAIL or settings.LEGAL_SERVICE_ADDRESS):
        raise Http404
    return render(request, "core/contact.html")


@never_cache
@require_http_methods(["GET", "POST"])
def consent_page(request):
    """"Am înțeles" on the notice bar (acknowledge) or the cookie settings form (the analytics choice).

    Either records that the current Terms and Privacy versions were shown: in the necessary consent cookie and, for a
    signed-in user who has not accepted them yet, against the account. Analytics is on only when explicitly ticked.
    """
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse("home")
    if request.method == "GET":
        return render(request, "core/consent.html", {"consent_next": next_url})
    if request.user.is_authenticated and acceptance_required(request):
        record_acceptance(request.user, "visit")
    if "acknowledge" in request.POST:
        analytics = analytics_chosen(request)  # "Am înțeles" keeps the analytics choice as it was (off by default).
    else:
        analytics = request.POST.get("allow_analytics") == "on"
    analytics = analytics and settings.ANALYTICS_VISITOR_COOKIE
    response = set_consent_cookie(redirect(next_url), analytics)
    return response if analytics else delete_visitor_cookie(response)
