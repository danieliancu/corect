"""p50/p95/min/max of the durations recorded in the usage ledgers. Numbers only: no text is read."""
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analytics.models import AudioUsageEvent, UsageEvent
from apps.analytics.stats import percentiles

MAX_ROWS = 20000


class Command(BaseCommand):
    help = "Prints text and voice latency percentiles from the usage ledgers."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def line(self, label, metric, values):
        summary = percentiles(values)
        if summary["n"]:
            self.stdout.write(f"  {label:26} {metric:22} n={summary['n']:<5} p50={summary['p50']} p95={summary['p95']} "
                              f"min={summary['min']} max={summary['max']}")

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        self.stdout.write(f"Latency since {since:%Y-%m-%d %H:%M} (milliseconds)")
        text = defaultdict(lambda: defaultdict(list))
        rows = (UsageEvent.objects.filter(created_at__gte=since).order_by("-created_at")
                .values_list("request_type", "source_language", "duration_ms", "provider_duration_ms",
                             "moderation_duration_ms")[:MAX_ROWS])
        for kind, language, total, provider, moderation in rows:
            group = text[f"{kind}/{language or '-'}"]
            group["duration_ms"].append(total)
            group["provider_duration_ms"].append(provider)
            group["moderation_duration_ms"].append(moderation)
        live = defaultdict(list)
        received = []
        for startup, first_word, finalise, final in (
                AudioUsageEvent.objects.filter(created_at__gte=since, stt_mode=AudioUsageEvent.SttMode.REALTIME)
                .order_by("-created_at").values_list("startup_ms", "first_word_ms", "finalise_ms", "final_received")[:MAX_ROWS]):
            live["startup_ms"].append(startup)
            live["first_word_ms"].append(first_word)
            live["finalise_ms"].append(finalise)
            if final is not None:
                received.append(final)
        provider = defaultdict(list)
        for operation, mode, duration in (
                AudioUsageEvent.objects.filter(created_at__gte=since).exclude(stt_mode=AudioUsageEvent.SttMode.REALTIME)
                .order_by("-created_at").values_list("operation", "stt_mode", "provider_duration_ms")[:MAX_ROWS]):
            provider[f"{operation}/{mode or '-'}"].append(duration)
        if not text and not any(live.values()) and not provider:
            self.stdout.write("  no data")
            return
        self.stdout.write("Text (\"Vreau să sune natural!\")")
        for label in sorted(text):
            for metric, values in text[label].items():
                self.line(label, metric, values)
        self.stdout.write("Voice: live transcription")
        for metric, values in live.items():
            self.line("realtime", metric, values)
        if received:
            self.stdout.write(f"  realtime                   final_received {sum(received)}/{len(received)}")
        self.stdout.write("Voice: provider calls")
        for label in sorted(provider):
            self.line(label, "provider_duration_ms", provider[label])
