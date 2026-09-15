import logging
import re
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import ValidationError

from .usage import ParsedResponse, Result, report_usage, usage_from_response

logger = logging.getLogger("apps.assistant")
NOT_CONFIGURED = "Asistentul este momentan indisponibil. Încearcă din nou mai târziu."
CONTENT_BLOCKED = "Nu putem procesa acest text, pentru că încalcă regulile de utilizare Corect.uk. Încearcă un alt text."
SELF_HARM_BLOCKED = ("Nu putem procesa acest text. Dacă treci printr-un moment greu, poți vorbi gratuit, oricând, cu "
                     "Samaritans la 116 123 (Regatul Unit).")
MODERATION_UNAVAILABLE = "Nu am putut verifica textul acum. Încearcă din nou în câteva momente."
INSTRUCTION_ATTEMPT = ("Corect.uk corectează și traduce texte pentru învățarea englezei. Nu poate urma instrucțiuni "
                       "adresate asistentului.")
# Attempts to override the assistant's instructions (English and Romanian). Kept narrow so ordinary learner sentences
# about instructions or rules are never refused; the prompts also treat all input as text, never as instructions.
INSTRUCTION_PATTERN = re.compile(
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:(?:the|your|my)\s+)?(?:previous|prior|above|earlier|preceding)\s+"
    r"(?:instructions?|prompts?|rules|messages)\b"
    r"|\b(?:reveal|show|print|repeat|output)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)\b"
    r"|\b(?:ignor[ăa]|uit[ăa])\s+(?:toate\s+)?(?:instruc[țţt]iunile|regulile)\b"
    r"|\bpromptul\s+(?:de\s+)?sistem\b",
    re.IGNORECASE)


class AssistantError(Exception):
    def __init__(self, code="provider_error", message="Ceva nu a mers. Încearcă din nou.", retry_after=None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(code)


def parse_response(prompt: str, text: str, schema: type[Result], *, model: str = "",
                   max_output_tokens: int = 6000) -> ParsedResponse[Result]:
    model = model or settings.OPENAI_MODEL
    if not settings.OPENAI_API_KEY or not model:
        raise AssistantError("not_configured", NOT_CONFIGURED)
    try:
        with OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT, max_retries=0) as client:
            response = client.responses.parse(
                model=model,
                input=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                text_format=schema, store=False, max_output_tokens=max_output_tokens,
            )
        # Billed tokens are reported before the checks below, which may still reject the response.
        usage = usage_from_response(response, model)
        report_usage(usage)
        if response.status != "completed":
            raise AssistantError("incomplete")
        if any(getattr(part, "type", None) == "refusal" for item in response.output
               for part in getattr(item, "content", [])):
            raise AssistantError("refused", "Acest text nu a putut fi procesat. Încearcă o altă propoziție.")
        if response.output_parsed is None:
            raise AssistantError("missing_output")
        return ParsedResponse(schema.model_validate(response.output_parsed.model_dump()), usage)
    except APITimeoutError:
        raise AssistantError("timeout", "A durat prea mult. Încearcă din nou.") from None
    except (OpenAIError, ValidationError, ValueError):
        # Exception text can contain user content or provider payloads. Never log it.
        raise AssistantError("invalid_or_failed_response") from None


def flagged_categories(categories) -> set[str]:
    values = categories.model_dump(by_alias=True) if hasattr(categories, "model_dump") else vars(categories)
    return {name for name, flagged in values.items() if flagged}


def moderate_text(text: str) -> None:
    """Refuses text flagged by OpenAI's moderation endpoint (hate, harassment, violence, sexual content, self-harm,
    illicit activity, ...). Fails closed: when the check cannot run, the text is not processed."""
    if not settings.OPENAI_API_KEY:
        raise AssistantError("not_configured", NOT_CONFIGURED)
    try:
        with OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT, max_retries=0) as client:
            result = client.moderations.create(model=settings.OPENAI_MODERATION_MODEL, input=text).results[0]
        flagged, categories = bool(result.flagged), flagged_categories(result.categories)
    except (OpenAIError, AttributeError, IndexError, TypeError, ValueError):
        raise AssistantError("moderation_unavailable", MODERATION_UNAVAILABLE) from None
    if flagged:
        self_harm = any(name.replace("_", "-").startswith("self-harm") for name in categories)
        raise AssistantError("content_blocked", SELF_HARM_BLOCKED if self_harm else CONTENT_BLOCKED)


def guarded_parse(prompt: str, text: str, schema: type[Result], *, checked_text: str | None = None,
                  **options) -> ParsedResponse[Result]:
    """parse_response behind the abuse guardrails. Moderation runs alongside the model call, so it adds no waiting time;
    the result is only returned once the text has passed moderation. `checked_text` is the learner-written part when
    `text` also carries other context."""
    checked = text if checked_text is None else checked_text
    if INSTRUCTION_PATTERN.search(checked):
        raise AssistantError("instruction_attempt", INSTRUCTION_ATTEMPT)
    if not settings.CONTENT_MODERATION_ENABLED:
        return parse_response(prompt, text, schema, **options)
    with ThreadPoolExecutor(max_workers=1) as pool:
        moderation = pool.submit(moderate_text, checked)
        try:
            parsed = parse_response(prompt, text, schema, **options)
        except AssistantError:
            moderation.result()  # Abusive text is refused as such even when the model call failed as well.
            raise
        moderation.result()
    return parsed
