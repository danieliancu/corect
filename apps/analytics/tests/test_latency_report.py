import io

from django.core.management import call_command
from django.test import TestCase

from apps.analytics.models import AudioUsageEvent, UsageEvent


class LatencyReportTests(TestCase):
    def report(self):
        out = io.StringIO()
        call_command("latency_report", stdout=out)
        return out.getvalue()

    def test_percentiles_per_operation_and_language_and_for_live_voice(self):
        for milliseconds in (1000, 2000, 3000, 4000):
            UsageEvent.objects.create(audience="anonymous", request_type="correction", source_language="en",
                                      status="success", duration_ms=milliseconds, provider_duration_ms=milliseconds - 200)
        UsageEvent.objects.create(audience="anonymous", request_type="translation", source_language="ro",
                                  status="success", duration_ms=2500)
        AudioUsageEvent.objects.create(operation="transcription", audience="anonymous", status="success",
                                       stt_mode="realtime", startup_ms=1500, first_word_ms=900, finalise_ms=700,
                                       final_received=True)
        AudioUsageEvent.objects.create(operation="speech", audience="anonymous", status="success", provider_duration_ms=1300)
        text = self.report()
        correction = [line for line in text.splitlines() if "correction/en" in line and "duration_ms" in line][0]
        self.assertIn("n=4", correction)
        self.assertIn("p50=2000 p95=4000 min=1000 max=4000", correction)
        self.assertIn("translation/ro", text)
        for metric in ("startup_ms", "first_word_ms", "finalise_ms", "final_received 1/1", "speech/-"):
            self.assertIn(metric, text)

    def test_no_data(self):
        self.assertIn("no data", self.report())
