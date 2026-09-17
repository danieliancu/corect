from datetime import timedelta
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.assistant.models import NaturalizeUsage, RateBucket, RealtimeTranscriptionSession, SubmissionClaim
from apps.analytics.models import FunnelEvent
from apps.assistant.retention import (CLOSED_SESSION_DAYS, FUNNEL_EVENT_DAYS, NATURALIZE_USAGE_DAYS,
                                      SUBMISSION_CLAIM_DAYS)
from apps.assistant.services.localday import local_day
from apps.assistant.services.realtime import abandon_expired_sessions


class Command(BaseCommand):
    help = ("Delete expired rate buckets, daily plan-quota counters older than two days, submission claims older than "
            "seven days and expired sign-in sessions, account for live transcription sessions the browser never "
            "finished, delete closed sessions after seven days and funnel events after about 13 months. Run daily.")

    def handle(self, *args, **options):
        now = timezone.now()
        buckets, _ = RateBucket.objects.filter(expires_at__lt=now).delete()
        usage, _ = NaturalizeUsage.objects.filter(day__lt=local_day(now) - timedelta(days=NATURALIZE_USAGE_DAYS)).delete()
        claims, _ = SubmissionClaim.objects.filter(created_at__lt=now - timedelta(days=SUBMISSION_CLAIM_DAYS)).delete()
        abandoned = abandon_expired_sessions(now)
        # Only the operational rows go; their audio ledger rows are kept for good.
        sessions, _ = RealtimeTranscriptionSession.objects.exclude(status=RealtimeTranscriptionSession.Status.OPEN) \
            .filter(finished_at__lt=now - timedelta(days=CLOSED_SESSION_DAYS)).delete()
        # Expired sign-in sessions, as Django's clearsessions would, so the privacy notice's session period holds.
        logins, _ = Session.objects.filter(expire_date__lt=now).delete()
        funnel, _ = FunnelEvent.objects.filter(created_at__lt=now - timedelta(days=FUNNEL_EVENT_DAYS)).delete()
        self.stdout.write(f"Removed {buckets} rate buckets, {usage} old daily quota counters and {claims} old submission "
                          f"claims. Marked {abandoned} unfinished live transcription sessions as abandoned and removed "
                          f"{sessions} closed ones. Removed {logins} expired sign-in sessions and {funnel} old funnel events.")
