"""Ledger rows for requests saved before usage tracking. Only exact facts are copied: tokens and cost stay unknown."""

BATCH_SIZE = 1000


def backfill_usage_events(apps, schema_editor=None):
    AssistantRequest = apps.get_model("assistant", "AssistantRequest")
    UsageEvent = apps.get_model("analytics", "UsageEvent")
    linked = UsageEvent.objects.filter(assistant_request__isnull=False).values("assistant_request_id")
    batch = []
    for entry in AssistantRequest.objects.exclude(pk__in=linked).order_by("pk").iterator(chunk_size=BATCH_SIZE):
        batch.append(UsageEvent(
            audience="registered", user_id=entry.user_id, request_type=entry.request_type, model=entry.model_used,
            prompt_version=entry.prompt_version, status=entry.status, error_code=entry.error_code,
            created_at=entry.created_at, assistant_request_id=entry.pk, is_backfilled=True))
        if len(batch) == BATCH_SIZE:
            UsageEvent.objects.bulk_create(batch)
            batch = []
    if batch:
        UsageEvent.objects.bulk_create(batch)


def remove_backfilled_usage_events(apps, schema_editor=None):
    apps.get_model("analytics", "UsageEvent").objects.filter(is_backfilled=True).delete()
