import logging
from time import perf_counter

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.suspension import SUSPENDED_MESSAGE, is_suspended
from apps.analytics.models import UsageEvent
from apps.analytics.services.recording import record_usage_event
from apps.analytics.services.visitors import attach_visitor_cookie, existing_visitor, get_or_create_visitor
from apps.core.views import home_context
from apps.learning.services.profile import record_correction_occurrences
from .forms import AssistantForm
from .languages import CORRECTION, UNCLASSIFIED, operation_for
from .services.limits import actor_key, claim_submission
from .services.naturalize import NaturalizeService
from .services.openai_client import AssistantError
from .services.persistence import save_failure, save_result
from .services.timing import collect_timings, server_timing_header, since, timed
from .services.usage import collect_provider_usage

logger = logging.getLogger("apps.assistant")
# Refused by a rule rather than failed: quotas, duplicates and the abuse guardrails.
REJECTION_CODES = {"rate_limit", "duplicate", "content_blocked", "instruction_attempt", "account_suspended"}
STATUS_CODES = {"rate_limit": 429, "duplicate": 409, "language": 422, "content_blocked": 422,
                "instruction_attempt": 422, "account_suspended": 403}
EMPTY_TEXT_MESSAGE = "Ca să continuăm, scrie ceva în casetă sau apasă microfonul și vorbește."


@never_cache
@require_POST
def naturalize(request):
    """Vreau să sune natural!: the single public action. The browser sends only the text and its submission token; the
    service decides whether the text is corrected (English) or rewritten in British English (Romanian), in one call."""
    started = perf_counter()
    with collect_timings() as timings:
        form = AssistantForm(request.POST)
        context = home_context(input_text=request.POST.get("text", ""))
        status = 200
        accepted = False
        retry_after = None
        anonymous = not request.user.is_authenticated
        visitor = None
        if not form.is_valid():
            if form.has_error("text", "required"):
                # Nothing to work on: say what to do rather than suggesting the (empty) text was kept.
                context["error"], context["empty_text"] = EMPTY_TEXT_MESSAGE, True
            else:
                context["error"] = (form.errors.get("text") or ["Formularul a expirat. Reîncarcă pagina și încearcă din nou."])[0]
            status = 400
        else:
            with timed("db"):
                visitor = get_or_create_visitor(request) if anonymous else existing_visitor(request)
            usage = {"status": UsageEvent.Status.SUCCESS, "error_code": "", "assistant_request": None,
                     "kind": UNCLASSIFIED, "source_language": ""}
            with collect_provider_usage() as calls:
                try:
                    with timed("db"):
                        if is_suspended(request.user):
                            raise AssistantError("account_suspended", SUSPENDED_MESSAGE)
                        claim_submission(actor_key(request), form.cleaned_data["submission_token"])
                    accepted = True
                    outcome = NaturalizeService().naturalize(form.cleaned_data["text"])
                    usage.update(kind=outcome.operation, source_language=outcome.source_language)
                    with timed("db"):
                        usage["assistant_request"] = save_result(request.user, outcome)
                    context.update(kind=outcome.operation, result=outcome.result.model_dump())
                    if outcome.operation == CORRECTION and usage["assistant_request"] is not None:
                        try:
                            with timed("db"):
                                # The learner's profile: "you have made this mistake N times" and "Exersează acum".
                                context["learning_hints"] = record_correction_occurrences(
                                    request.user, usage["assistant_request"])
                        except DatabaseError:
                            logger.error("learning_profile_unavailable")
                except AssistantError as exc:
                    retry_after = exc.retry_after
                    context["error"] = exc.message
                    status = STATUS_CODES.get(exc.code, 503)
                    usage.update(status=UsageEvent.Status.REJECTED if exc.code in REJECTION_CODES else UsageEvent.Status.FAILED,
                                 error_code=exc.code, kind=operation_for(exc.source_language) or UNCLASSIFIED,
                                 source_language=exc.source_language)
                    logger.warning("assistant_failed code=%s kind=%s", exc.code, usage["kind"])
                    if accepted:
                        try:
                            usage["assistant_request"] = save_failure(request.user, usage["kind"], exc.code,
                                                                      exc.source_language)
                        except DatabaseError:
                            logger.error("assistant_failure_record_unavailable")
                except DatabaseError:
                    logger.error("assistant_database_unavailable kind=%s", usage["kind"])
                    context["error"] = "Nu am putut finaliza cererea. Încearcă din nou mai târziu."
                    status = 503
                    usage.update(status=UsageEvent.Status.FAILED, error_code="database_unavailable")
            event = record_usage_event(request=request, calls=calls, visitor=visitor, duration_ms=since(started),
                                       moderation_ms=timings.get("moderation"), **usage)
            if event is not None:
                context["usage_event_id"] = event.pk  # Links later speech requests to this result.
        template = "assistant/response.html" if request.headers.get("HX-Request") == "true" else "core/home.html"
        response = render(request, template, context, status=status)
    timings["total"] = since(started)
    response["Server-Timing"] = server_timing_header(timings)
    if status == 429:
        response["Retry-After"] = str(retry_after or 60)
    if anonymous:
        attach_visitor_cookie(request, response, visitor)
    return response
