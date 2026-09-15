"""The plan tier at request time on the text and audio usage ledgers.

Anonymous rows can only have been anonymous, so they are backfilled; the historical tier of registered rows (Free or
Pro) is unknown and stays blank rather than being guessed from today's group membership. Reversible.
"""
from django.db import migrations, models


def forward(apps, schema_editor):
    for name in ("UsageEvent", "AudioUsageEvent"):
        apps.get_model("analytics", name).objects.filter(audience="anonymous", plan="").update(plan="anonymous")


def backward(apps, schema_editor):
    """Nothing to undo: removing the field drops the values."""


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0008_effective_operation_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="audiousageevent",
            name="plan",
            field=models.CharField(blank=True, choices=[("anonymous", "Anonymous"), ("free", "Free"), ("pro", "Pro")],
                                   help_text="Plan tier at request time; blank when unknown.", max_length=10),
        ),
        migrations.AddField(
            model_name="usageevent",
            name="plan",
            field=models.CharField(blank=True, choices=[("anonymous", "Anonymous"), ("free", "Free"), ("pro", "Pro")],
                                   help_text="Plan tier at request time; blank when unknown (before plan quotas existed).",
                                   max_length=10),
        ),
        migrations.AddIndex(
            model_name="usageevent",
            index=models.Index(fields=["plan", "created_at"], name="analytics_usage_plan_idx"),
        ),
        migrations.RunPython(forward, backward),
    ]
