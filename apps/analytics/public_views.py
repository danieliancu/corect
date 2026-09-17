"""The browser's funnel beacon (static/js/funnel.js). Answers 204 whatever happens, so it never affects the page."""
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.assistant.services.limits import MINUTE, actor_key, consume_quota
from apps.assistant.services.openai_client import AssistantError
from .services.funnel import CLIENT_EVENTS, looks_automated, record_funnel_event
from .services.visitors import attach_visitor_cookie

EVENTS_PER_MINUTE = 30


@never_cache
@require_POST
def funnel_event(request):
    response = HttpResponse(status=204)
    name, placement = request.POST.get("name", ""), request.POST.get("placement", "")
    if placement not in CLIENT_EVENTS.get(name, ()) or looks_automated(request):
        return response
    try:
        with transaction.atomic():
            consume_quota(f"funnel:{actor_key(request)}", [(MINUTE, EVENTS_PER_MINUTE)], "rate_limit", "")
    except (AssistantError, DatabaseError):
        return response
    visitor = record_funnel_event(request, name, placement, create_visitor=True)
    return attach_visitor_cookie(request, response, visitor)
