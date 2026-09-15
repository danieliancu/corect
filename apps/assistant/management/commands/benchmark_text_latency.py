"""Repeatable text latency benchmark (real, billed OpenAI calls; opt-in with --live).

Modes: `reused` uses the shared process client; `fresh` builds and closes a new client for every call (the behaviour
before the shared client); `idle` reuses the client after waiting, to show what keep-alive buys at low traffic.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test.utils import override_settings

from apps.analytics.stats import percentiles
from apps.assistant.evals.live import require_live
from apps.assistant.services.naturalize import NaturalizeService
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.provider import fresh_openai_client
from apps.assistant.services.timing import collect_timings
from apps.assistant.services.usage import collect_provider_usage

TEXTS = {
    "en_errors_short": "I didn't went to work yesterday because I was sick.",
    "en_natural_short": "I'll give you a call when I get home.",
    "ro_short": "Nu cred că ajung la muncă înainte de nouă.",
    "en_long": ("Hello Mark, I am writing you because I want to ask you if I can take a day off next Friday. "
                "My son has an appointment at the hospital and I must to go with him because my wife is working. "
                "I have finished already the report for the client and I sent it to Sarah yesterday. "
                "If there is any problem, you can call me on my phone, I will have it with me all the day. "
                "Thank you very much for your understanding and sorry for the short notice."),
}


class Command(BaseCommand):
    help = "Measures text request latency (total, provider, moderation) with p50/p95/min/max (use --live)."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Confirm real, billed provider calls.")
        parser.add_argument("--samples", type=int, default=8)
        parser.add_argument("--modes", default="reused,fresh", help="Comma-separated: reused, fresh, idle.")
        parser.add_argument("--idle-seconds", type=float, default=25.0)
        parser.add_argument("--texts", default=",".join(TEXTS), help=f"Comma-separated from: {', '.join(TEXTS)}.")
        parser.add_argument("--moderation", choices=("on", "off", "settings"), default="settings")
        parser.add_argument("--json-out")

    def handle(self, *args, **options):
        require_live(options)
        overrides = {} if options["moderation"] == "settings" else {
            "CONTENT_MODERATION_ENABLED": options["moderation"] == "on"}
        report = {"model": settings.OPENAI_MODEL, "samples": options["samples"], "results": {}}
        with override_settings(**overrides):
            self.stdout.write(f"model {settings.OPENAI_MODEL} · moderation {settings.CONTENT_MODERATION_ENABLED} · "
                              f"samples {options['samples']}")
            for mode in [mode.strip() for mode in options["modes"].split(",") if mode.strip()]:
                for name in [name.strip() for name in options["texts"].split(",") if name.strip()]:
                    rows = self.measure(TEXTS[name], mode, options)
                    summary = self.summarise(rows)
                    report["results"][f"{mode}:{name}"] = {"summary": summary, "rows": rows}
                    self.stdout.write(f"{mode:6} {name:18} total {summary['total_ms']} · provider p50 "
                                      f"{summary['provider_ms'].get('p50')} · moderation p50 "
                                      f"{summary['moderation_ms'].get('p50')} · calls {summary['calls']} · "
                                      f"cached {summary['cached_ratio']} · errors {summary['errors']}")
        path = Path(options["json_out"] or settings.BASE_DIR / "artifacts" / "benchmarks" /
                    f"text-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.stdout.write(f"report: {path}")

    def measure(self, text, mode, options):
        def once():
            with collect_timings() as timings, collect_provider_usage() as calls:
                started = perf_counter()
                error = None
                try:
                    NaturalizeService().naturalize(text)
                except AssistantError as exc:
                    error = exc.code
                total = round((perf_counter() - started) * 1000)
            return {"total_ms": total, "provider_ms": round(timings.get("provider", 0)) or None,
                    "moderation_ms": round(timings["moderation"]) if "moderation" in timings else None,
                    "calls": len(calls), "error": error,
                    "input_tokens": sum(call.input_tokens or 0 for call in calls),
                    "cached_input_tokens": sum(call.cached_input_tokens or 0 for call in calls),
                    "output_tokens": sum(call.output_tokens or 0 for call in calls)}

        if mode == "fresh":
            opened = []

            def fresh():
                client = fresh_openai_client()
                opened.append(client)
                return client
            context = patch("apps.assistant.services.openai_client.openai_client", side_effect=fresh)
        else:
            opened, context = [], None
        rows = []
        try:
            if context:
                context.start()
            once()  # Warm-up: primes the prompt cache (and, when reused, the connection).
            for _ in range(options["samples"]):
                if mode == "idle":
                    time.sleep(options["idle_seconds"])
                rows.append(once())
                for client in opened:
                    client.close()
                opened.clear()
        finally:
            if context:
                context.stop()
        return rows

    @staticmethod
    def summarise(rows):
        ok = [row for row in rows if not row["error"]]
        return {"total_ms": percentiles([row["total_ms"] for row in ok]),
                "provider_ms": percentiles([row["provider_ms"] for row in ok]),
                "moderation_ms": percentiles([row["moderation_ms"] for row in ok]),
                "calls": sorted({row["calls"] for row in ok}), "errors": len(rows) - len(ok),
                "cached_ratio": round(sum(row["cached_input_tokens"] for row in ok) /
                                      max(1, sum(row["input_tokens"] for row in ok)), 2)}
