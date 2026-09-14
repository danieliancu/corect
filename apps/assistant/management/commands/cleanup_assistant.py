from datetime import timedelta
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.assistant.models import RateBucket, RealtimeTranscriptionSession, SubmissionClaim
from apps.assistant.retention import CLOSED_SESSION_DAYS, SUBMISSION_CLAIM_DAYS
from apps.assistant.services.realtime import abandon_expired_sessions


class Command(BaseCommand):
    help = ("Delete expired rate buckets, submission claims older than seven days and expired sign-in sessions, "
            "account for live transcription sessions the browser never finished, and delete closed sessions after "
            "seven days. Run daily.")

    def handle(self, *args, **options):
        now = timezone.now()
        buckets, _ = RateBucket.objects.filter(expires_at__lt=now).delete()
        claims, _ = SubmissionClaim.objects.filter(created_at__lt=now - timedelta(days=SUBMISSION_CLAIM_DAYS)).delete()
        abandoned = abandon_expired_sessions(now)
        # Only the operational rows go; their audio ledger rows are kept for good.
        sessions, _ = RealtimeTranscriptionSession.objects.exclude(status=RealtimeTranscriptionSession.Status.OPEN) \
            .filter(finished_at__lt=now - timedelta(days=CLOSED_SESSION_DAYS)).delete()
        # Expired sign-in sessions, as Django's clearsessions would, so the privacy notice's session period holds.
        logins, _ = Session.objects.filter(expire_date__lt=now).delete()
        self.stdout.write(f"Removed {buckets} rate buckets and {claims} old submission claims. "
                          f"Marked {abandoned} unfinished live transcription sessions as abandoned and removed "
                          f"{sessions} closed ones. Removed {logins} expired sign-in sessions.")
