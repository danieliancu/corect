from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.assistant.models import RateBucket, SubmissionClaim
from .openai_client import AssistantError


def actor_key(request) -> str:
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    # REMOTE_ADDR only: do not trust client-supplied forwarding headers.
    return "anon:" + salted_hmac("assistant-rate", request.META.get("REMOTE_ADDR", "unknown")).hexdigest()


@transaction.atomic
def claim_submission(actor: str, token) -> None:
    now = timezone.now()
    windows = [(60, settings.RATE_LIMIT_MINUTE), (86400, settings.RATE_LIMIT_DAY)]
    for seconds, limit in windows:
        window = int(now.timestamp()) // seconds
        key = f"{actor}:{seconds}:{window}"
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={"expires_at": now + timedelta(seconds=seconds)})
        bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
        if bucket.count >= limit:
            retry_after = max(1, (window + 1) * seconds - int(now.timestamp()))
            raise AssistantError("rate_limit", "You've reached the request limit. Please try again later.", retry_after=retry_after)
        bucket.count += 1
        bucket.save(update_fields=["count"])
    try:
        with transaction.atomic():
            SubmissionClaim.objects.create(actor=actor, token=token)
    except IntegrityError:
        raise AssistantError("duplicate", "This request has already been submitted. Please try again when it finishes.") from None
