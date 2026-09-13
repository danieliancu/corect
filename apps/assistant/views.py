import logging

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.analytics.models import UsageEvent
from apps.analytics.services.recording import record_usage_event
from apps.analytics.services.visitors import attach_visitor_cookie, existing_visitor, get_or_create_visitor
from apps.core.views import home_context
from .forms import AssistantForm
from .services.correction import CorrectionService
from .services.limits import actor_key, claim_submission
from .services.openai_client import AssistantError
from .services.persistence import save_failure, save_result
from .services.translation import TranslationService
from .services.usage import collect_provider_usage

logger = logging.getLogger("apps.assistant")
REJECTION_CODES = {"rate_limit", "duplicate"}
EMPTY_TEXT_MESSAGES = {
    "correction": "Ca să facem corectura, scrie ceva în casetă sau apasă microfonul și vorbește.",
    "translation": "Ca să facem traducerea, scrie ceva în casetă sau apasă microfonul și vorbește.",
}


@never_cache
@require_POST
def submit(request, kind):
    form = AssistantForm(request.POST)
    requested_kind = kind
    context = home_context(input_text=request.POST.get("text", ""), kind=kind)
    status = 200
    accepted = False
    retry_after = None
    anonymous = not request.user.is_authenticated
    visitor = None
    if not form.is_valid():
        if form.has_error("text", "required"):
            # Nothing to work on: say what to do rather than suggesting the (empty) text was kept.
            context["error"], context["empty_text"] = EMPTY_TEXT_MESSAGES[kind], True
        else:
            context["error"] = (form.errors.get("text") or ["Formularul a expirat. Reîncarcă pagina și încearcă din nou."])[0]
        status = 400
    else:
        visitor = get_or_create_visitor(request) if anonymous else existing_visitor(request)
        usage = {"status": UsageEvent.Status.SUCCESS, "error_code": "", "assistant_request": None, "auto_translated": False}
        with collect_provider_usage() as calls:
            try:
                claim_submission(actor_key(request), form.cleaned_data["submission_token"])
                accepted = True
                text = form.cleaned_data["text"]
                if kind == "correction":
                    try:
                        result = CorrectionService().correct(text)
                    except AssistantError as exc:
                        if exc.code != "romanian_input":
                            raise
                        # Romanian sent to Correct is translated straight away instead of asking to resubmit.
                        kind = context["kind"] = "translation"
                        context["auto_translated"] = usage["auto_translated"] = True
                        result = TranslationService().translate(text)
                else:
                    result = TranslationService().translate(text)
                usage["assistant_request"] = save_result(request.user, kind, text, result)
                context["result"] = result.model_dump()
            except AssistantError as exc:
                retry_after = exc.retry_after
                context["error"] = exc.message
                status = {"rate_limit": 429, "duplicate": 409, "language": 422, "romanian_input": 422}.get(exc.code, 503)
                usage.update(status=UsageEvent.Status.REJECTED if exc.code in REJECTION_CODES else UsageEvent.Status.FAILED,
                             error_code=exc.code)
                logger.warning("assistant_failed code=%s kind=%s", exc.code, kind)
                if accepted:
                    try:
                        usage["assistant_request"] = save_failure(request.user, kind, exc.code)
                    except DatabaseError:
                        logger.error("assistant_failure_record_unavailable")
            except DatabaseError:
                logger.error("assistant_database_unavailable kind=%s", kind)
                context["error"] = "Nu am putut finaliza cererea. Încearcă din nou mai târziu."
                status = 503
                usage.update(status=UsageEvent.Status.FAILED, error_code="database_unavailable")
        event = record_usage_event(request=request, kind=requested_kind, calls=calls, visitor=visitor, **usage)
        if event is not None:
            context["usage_event_id"] = event.pk  # Links later speech requests to this correction.
    template = "assistant/response.html" if request.headers.get("HX-Request") == "true" else "core/home.html"
    response = render(request, template, context, status=status)
    if status == 429:
        response["Retry-After"] = str(retry_after or 60)
    if anonymous:
        attach_visitor_cookie(response, visitor)
    return response
