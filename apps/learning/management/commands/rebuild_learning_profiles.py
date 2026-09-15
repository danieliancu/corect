from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.assistant.models import AssistantRequest
from apps.learning.models import MistakeOccurrence, UserMistakePattern
from apps.learning.services.profile import occurrences_for, refresh_patterns


class Command(BaseCommand):
    help = ("Rebuild learning profiles (mistake occurrences and patterns) from saved corrections. Never calls AI and "
            "never changes the corrections themselves. Safe to run again: existing occurrences are kept, patterns are "
            "recalculated.")

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, help="Only this user id.")

    def handle(self, *args, user=None, **options):
        entries = AssistantRequest.objects.filter(status="success", request_type="correction", user__isnull=False)
        if user:
            entries = entries.filter(user_id=user)
        created = 0
        for entry in entries.order_by("pk").iterator(chunk_size=200):
            occurrences = occurrences_for(entry)
            if not occurrences:
                continue
            known = set(MistakeOccurrence.objects.filter(correction_id__in=[item.correction_id for item in occurrences])
                        .values_list("correction_id", flat=True))
            new = [item for item in occurrences if item.correction_id not in known]
            with transaction.atomic():
                created += len(MistakeOccurrence.objects.bulk_create(new, ignore_conflicts=True))
        user_ids = MistakeOccurrence.objects.values_list("user_id", flat=True).distinct()
        if user:
            user_ids = user_ids.filter(user_id=user)
        patterns_created = patterns_updated = 0
        for member in get_user_model().objects.filter(pk__in=list(user_ids)):
            new_patterns, changed = refresh_patterns(member)
            patterns_created += new_patterns
            patterns_updated += changed
        self.stdout.write(f"Created {created} mistake occurrences. Learning profiles for {len(set(user_ids))} users: "
                          f"{patterns_created} new patterns, {patterns_updated} recalculated. "
                          f"{UserMistakePattern.objects.count()} patterns in total.")
