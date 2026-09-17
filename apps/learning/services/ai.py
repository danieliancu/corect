"""The only door to AI for learning features: one ledger row per call, minimal JSON context, graceful failure."""
import json
import logging

from django.conf import settings

from apps.accounts.suspension import is_suspended
from apps.analytics.models import LearningUsageEvent
from apps.analytics.services.recording import record_learning_event
from apps.assistant.services.openai_client import AssistantError, guarded_parse, parse_response
from apps.assistant.services.usage import collect_provider_usage
from apps.core.monitoring import log_failure

logger = logging.getLogger("apps.assistant")
Feature = LearningUsageEvent.Feature
Status = LearningUsageEvent.Status
REFUSED = {"content_blocked", "instruction_attempt"}


class LearningAIUnavailable(Exception):
    """New AI content could not be produced. Everything already stored keeps working."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def learning_call(*, user, feature, prompt, prompt_version, payload, schema, validate=None, learner_text=None,
                  max_output_tokens=4000):
    """Runs one structured call and records exactly one LearningUsageEvent (success, failure or refusal).

    `payload` is the minimal JSON context; `learner_text` (an answer the learner typed) goes through the abuse guardrails.
    `validate` may clean the output or raise ValueError when it is unusable (recorded as invalid_output).
    Returns (output, event).
    """
    if is_suspended(user):
        raise LearningAIUnavailable("account_suspended")
    text = json.dumps(payload, ensure_ascii=False)
    options = {"model": settings.OPENAI_LEARNING_MODEL, "max_output_tokens": max_output_tokens}
    with collect_provider_usage() as calls:
        try:
            if learner_text is not None:
                parsed = guarded_parse(prompt, text, schema, checked_text=learner_text, **options)
            else:
                parsed = parse_response(prompt, text, schema, **options)
            output = validate(parsed.output) if validate else parsed.output
        except AssistantError as exc:
            status = Status.REJECTED if exc.code in REFUSED else Status.FAILED
            record_learning_event(user=user, feature=feature, status=status, calls=calls, error_code=exc.code,
                                  prompt_version=prompt_version)
            log_failure(logger, "learning_ai_failed", exc.code, feature=feature)
            raise LearningAIUnavailable(exc.code) from None
        except ValueError:
            record_learning_event(user=user, feature=feature, status=Status.FAILED, calls=calls,
                                  error_code="invalid_output", prompt_version=prompt_version)
            log_failure(logger, "learning_ai_failed", "invalid_output", feature=feature)
            raise LearningAIUnavailable("invalid_output") from None
    event = record_learning_event(user=user, feature=feature, status=Status.SUCCESS, calls=calls,
                                  prompt_version=prompt_version)
    return output, event
