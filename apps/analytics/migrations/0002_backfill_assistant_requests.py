from django.db import migrations

from apps.analytics.backfill import backfill_usage_events, remove_backfilled_usage_events


class Migration(migrations.Migration):
    """Historical AssistantRequests become ledger rows with exact facts only; tokens and cost stay unknown."""

    dependencies = [
        ("analytics", "0001_initial"),
        ("assistant", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill_usage_events, remove_backfilled_usage_events)]
