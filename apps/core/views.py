from uuid import uuid4

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.cache import never_cache


def home_context(**kwargs):
    return {"max_characters": settings.ASSISTANT_MAX_CHARACTERS, "voice_max_seconds": settings.VOICE_MAX_SECONDS,
            "submission_token": str(uuid4()), **kwargs}


@never_cache
def home(request):
    return render(request, "core/home.html", home_context())


def settings_page(request):
    return render(request, "core/settings.html", {"visitor_cookie": settings.ANALYTICS_VISITOR_COOKIE})
