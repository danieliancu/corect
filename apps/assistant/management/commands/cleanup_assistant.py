from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.assistant.models import RateBucket, SubmissionClaim


class Command(BaseCommand):
    help = "Delete expired rate buckets and submission claims older than seven days. Run daily."

    def handle(self, *args, **options):
        now = timezone.now()
        buckets, _ = RateBucket.objects.filter(expires_at__lt=now).delete()
        claims, _ = SubmissionClaim.objects.filter(created_at__lt=now - timedelta(days=7)).delete()
        self.stdout.write(f"Removed {buckets} rate buckets and {claims} old submission claims.")
