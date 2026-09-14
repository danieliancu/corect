from uuid import uuid4

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from .plans import display_plans


def home_context(**kwargs):
    return {"max_characters": settings.ASSISTANT_MAX_CHARACTERS, "voice_max_seconds": settings.VOICE_MAX_SECONDS,
            "voice_realtime_enabled": settings.VOICE_REALTIME_ENABLED, "submission_token": str(uuid4()),
            "plans": display_plans(), **kwargs}


@never_cache
def home(request):
    return render(request, "core/home.html", home_context())


def privacy_page(request):
    return render(request, "core/privacy.html", {"visitor_cookie": settings.ANALYTICS_VISITOR_COOKIE})


def terms_page(request):
    return render(request, "core/terms.html")


def contact_page(request):
    if not settings.CONTACT_EMAIL:
        raise Http404
    return render(request, "core/contact.html")
