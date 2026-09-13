import logging

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.views import home_context
from .forms import AssistantForm
from .services.correction import CorrectionService
from .services.limits import actor_key, claim_submission
from .services.openai_client import AssistantError
from .services.persistence import save_failure, save_result
from .services.translation import TranslationService

logger = logging.getLogger("apps.assistant")


@never_cache
@require_POST
def submit(request, kind):
    form = AssistantForm(request.POST)
    context = home_context(input_text=request.POST.get("text", ""), kind=kind)
    status = 200
    accepted = False
    retry_after = None
    if not form.is_valid():
        context["error"] = (form.errors.get("text") or ["Your form expired. Please reload and try again."])[0]
        status = 400
    else:
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
                    context["auto_translated"] = True
                    result = TranslationService().translate(text)
            else:
                result = TranslationService().translate(text)
            save_result(request.user, kind, text, result)
            context["result"] = result.model_dump()
        except AssistantError as exc:
            retry_after = exc.retry_after
            context["error"] = exc.message
            status = {"rate_limit": 429, "duplicate": 409, "language": 422, "romanian_input": 422}.get(exc.code, 503)
            logger.warning("assistant_failed code=%s kind=%s", exc.code, kind)
            if accepted:
                try:
                    save_failure(request.user, kind, exc.code)
                except DatabaseError:
                    logger.error("assistant_failure_record_unavailable")
        except DatabaseError:
            logger.error("assistant_database_unavailable kind=%s", kind)
            context["error"] = "We couldn't finish your request. Please try again later."
            status = 503
    template = "assistant/response.html" if request.headers.get("HX-Request") == "true" else "core/home.html"
    response = render(request, template, context, status=status)
    if status == 429:
        response["Retry-After"] = str(retry_after or 60)
    return response
