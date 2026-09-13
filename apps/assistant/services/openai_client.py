import logging
from typing import TypeVar

from django.conf import settings
from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger("apps.assistant")
Result = TypeVar("Result", bound=BaseModel)


class AssistantError(Exception):
    def __init__(self, code="provider_error", message="Something went wrong. Please try again.", retry_after=None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(code)


def parse_response(prompt: str, text: str, schema: type[Result]) -> Result:
    if not settings.OPENAI_API_KEY or not settings.OPENAI_MODEL:
        raise AssistantError("not_configured", "The English coach is temporarily unavailable. Please try again later.")
    try:
        with OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT, max_retries=0) as client:
            response = client.responses.parse(
                model=settings.OPENAI_MODEL,
                input=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                text_format=schema, store=False, max_output_tokens=6000,
            )
        if response.status != "completed":
            raise AssistantError("incomplete")
        if any(getattr(part, "type", None) == "refusal" for item in response.output
               for part in getattr(item, "content", [])):
            raise AssistantError("refused", "This text couldn't be processed. Please try a different sentence.")
        if response.output_parsed is None:
            raise AssistantError("missing_output")
        return schema.model_validate(response.output_parsed.model_dump())
    except APITimeoutError:
        raise AssistantError("timeout", "That took too long. Please try again.") from None
    except (OpenAIError, ValidationError, ValueError):
        # Exception text can contain user content or provider payloads. Never log it.
        raise AssistantError("invalid_or_failed_response") from None
