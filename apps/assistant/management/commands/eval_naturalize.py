"""Live language-quality eval of "Vreau să sune natural!" (real, billed OpenAI calls; opt-in with --live).

Writes nothing to the database. The JSON report (with the generated text, for review) goes under artifacts/, which is
not committed.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test.utils import override_settings

from apps.assistant.evals.dataset import DEFAULT_CASES, GROUPS, load_cases
from apps.assistant.evals.live import require_live
from apps.assistant.evals.scoring import score_case, summarise
from apps.assistant.services.naturalize import NaturalizeService, output_token_budget
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.prompts import PROMPT_VERSION
from apps.assistant.services.usage import collect_provider_usage


class Command(BaseCommand):
    help = "Runs the language-quality dataset against the live model and prints a summary (use --live)."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Confirm real, billed provider calls.")
        parser.add_argument("--cases", default=str(DEFAULT_CASES))
        parser.add_argument("--group", action="append", choices=GROUPS, help="Only these groups (repeatable).")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--repeat", type=int, default=1)
        parser.add_argument("--concurrency", type=int, default=4)
        parser.add_argument("--reasoning-effort", default=None)
        parser.add_argument("--json-out")
        parser.add_argument("--fail-under", type=float)

    def handle(self, *args, **options):
        require_live(options)
        cases = load_cases(Path(options["cases"]), options["group"], options["limit"]) * max(1, options["repeat"])
        service = NaturalizeService()
        overrides = {} if options["reasoning_effort"] is None else {"OPENAI_REASONING_EFFORT": options["reasoning_effort"]}

        def run(case):
            outcome = error = None
            with collect_provider_usage() as calls:
                started = perf_counter()
                try:
                    outcome = service.naturalize(case.input, polite=case.polite)
                except AssistantError as exc:
                    error = exc
                duration = round((perf_counter() - started) * 1000)
            return {"case": case, "outcome": outcome, "error": error, "calls": list(calls), "duration_ms": duration,
                    "budget": output_token_budget(case.input), "score": score_case(case, outcome, error)}

        self.stdout.write(f"{len(cases)} cases · prompt {PROMPT_VERSION} · model {settings.OPENAI_MODEL} · "
                          f"moderation {settings.CONTENT_MODERATION_ENABLED} · concurrency {options['concurrency']}")
        with override_settings(**overrides), ThreadPoolExecutor(max_workers=max(1, options["concurrency"])) as pool:
            rows = []
            for index, row in enumerate(pool.map(run, cases), start=1):
                rows.append(row)
                if index % 25 == 0:
                    self.stdout.write(f"  {index}/{len(cases)}")
        summary = summarise(rows)
        self.print_summary(summary)
        path = Path(options["json_out"] or settings.BASE_DIR / "artifacts" / "evals" /
                    f"naturalize-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"summary": summary, "prompt_version": PROMPT_VERSION, "model": settings.OPENAI_MODEL,
                                    "cases": [self.describe(row) for row in rows]}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        self.stdout.write(f"report: {path}")
        if options["fail_under"] is not None and (summary["pass_rate"] or 0) < options["fail_under"]:
            raise SystemExit(1)

    def print_summary(self, summary):
        write = self.stdout.write
        write(f"\npass rate {summary['pass_rate']} ({summary['passed']}/{summary['cases']})")
        for group, values in summary["groups"].items():
            write(f"  {group:22} {values['passed']:>3}/{values['cases']:<3} {values['rate']}")
        write(f"routing accuracy {summary['routing_accuracy']} {summary['routing_confusion']}")
        write(f"errors: precision {summary['error_precision']} · recall {summary['error_recall']} · "
              f"false-error rate {summary['false_error_rate']} · paraphrase rate {summary['paraphrase_rate']}")
        write(f"provider calls {summary['provider_calls']} · error codes {summary['error_codes']}")
        write(f"latency ms {summary['latency_ms']}")
        write(f"provider ms {summary['provider_ms']}")
        write(f"output tokens {summary['output_tokens']} · reasoning {summary['reasoning_tokens']} · "
              f"cached input ratio {summary['cached_input_ratio']} · max output/budget {summary['max_output_budget_ratio']}")
        write(f"failure reasons {summary['failure_reasons']}")
        for failing in summary["failing"][:25]:
            write(f"  FAIL {failing['id']}: {'; '.join(failing['failures'])}")

    @staticmethod
    def describe(row):
        outcome, score = row["outcome"], row["score"]
        return {"id": row["case"].id, "group": row["case"].group, "passed": score.passed, "failures": score.failures,
                "duration_ms": row["duration_ms"], "provider_calls": len(row["calls"]),
                "output_tokens": [call.output_tokens for call in row["calls"]],
                "error": score.error_code or None, "source_language": score.source_language,
                "operation": score.operation, "input": row["case"].input,
                "result": outcome.result.model_dump() if outcome else None}
