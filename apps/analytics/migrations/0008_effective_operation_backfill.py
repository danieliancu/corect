"""Aligns earlier ledger rows with the effective-operation meaning of UsageEvent.request_type.

Before "Vreau să sune natural!", request_type was the button the visitor chose, and Romanian sent to Correct was
translated (auto_translated). Those rows become translations from Romanian. source_language is copied from the linked
history row where there is one. No submitted or generated text, and no stored result JSON, is read or changed.
"""
from django.db import migrations
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def forward(apps, schema_editor):
    UsageEvent = apps.get_model("analytics", "UsageEvent")
    AssistantRequest = apps.get_model("assistant", "AssistantRequest")
    UsageEvent.objects.filter(auto_translated=True, request_type="correction").update(
        request_type="translation", source_language="ro")
    language = AssistantRequest.objects.filter(pk=OuterRef("assistant_request_id")).values("detected_language")[:1]
    UsageEvent.objects.filter(source_language="", assistant_request__isnull=False).update(
        source_language=Coalesce(Subquery(language), Value("")))


def backward(apps, schema_editor):
    UsageEvent = apps.get_model("analytics", "UsageEvent")
    UsageEvent.objects.filter(auto_translated=True, request_type="translation").update(
        request_type="correction", source_language="")


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0007_effective_operation_and_timings"),
        ("assistant", "0003_request_type_unclassified"),
    ]
    operations = [migrations.RunPython(forward, backward)]
