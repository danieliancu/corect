"""Scores one live answer against a case's expectations, and summarises a run. Pure functions: no provider calls."""
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from apps.analytics.stats import percentiles
from apps.assistant.languages import CORRECTION

FALSE_ERROR_GROUPS = ("en_already_natural", "en_punctuation_only", "en_american", "en_correct_unnatural")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^\w']+", " ", text).split())


def contains(haystack: str, needle: str) -> bool:
    """Whole-word containment after normalisation."""
    return f" {normalise(needle)} " in f" {haystack} "


@dataclass
class CaseScore:
    case_id: str
    group: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    source_language: str = ""
    operation: str = ""
    error_code: str = ""
    has_errors: bool | None = None
    natural_given: bool | None = None


def score_case(case, outcome=None, error=None) -> CaseScore:
    """`outcome` is a Naturalized (or None); `error` the AssistantError raised instead (or None)."""
    expect, failures = case.expect, []
    score = CaseScore(case.id, case.group, False, failures)
    if error is not None:
        score.error_code, score.source_language = error.code, getattr(error, "source_language", "")
        if expect.error_code != error.code:
            failures.append(f"error {error.code}" + (f" (expected {expect.error_code})" if expect.error_code else ""))
        score.passed = not failures
        return score
    if expect.error_code:
        failures.append(f"expected error {expect.error_code}, got a result")
    score.source_language, score.operation = outcome.source_language, outcome.operation
    if expect.source_language and outcome.source_language != expect.source_language:
        failures.append(f"routing: {outcome.source_language} (expected {expect.source_language})")
    if expect.operation and outcome.operation != expect.operation:
        failures.append(f"operation: {outcome.operation} (expected {expect.operation})")
    result = outcome.result
    if outcome.operation == CORRECTION:
        corrected, natural = normalise(result.corrected_text), normalise(result.native_text)
        outputs = (corrected, natural)
        genuine = [item for item in result.corrections if not item.is_british_english_preference]
        score.has_errors, score.natural_given = result.has_errors, bool(result.native_text.strip())
        if expect.has_errors is not None and result.has_errors != expect.has_errors:
            failures.append(f"has_errors {result.has_errors} (expected {expect.has_errors})")
        if expect.natural == "required" and not score.natural_given:
            failures.append("natural version missing")
        if expect.natural == "forbidden" and score.natural_given:
            failures.append("paraphrased already natural English")
        for alternatives in expect.corrected_includes_any:
            if not any(contains(corrected, phrase) for phrase in alternatives):
                failures.append(f"corrected text lacks {alternatives}")
        if expect.categories_any and not any(item.category in expect.categories_any for item in genuine):
            failures.append(f"categories {[item.category for item in genuine]} (expected one of {expect.categories_any})")
        if expect.min_corrections is not None and len(genuine) < expect.min_corrections:
            failures.append(f"{len(genuine)} corrections (expected at least {expect.min_corrections})")
        if expect.max_corrections is not None and len(genuine) > expect.max_corrections:
            failures.append(f"{len(genuine)} corrections (expected at most {expect.max_corrections})")
    else:
        outputs = (normalise(result.translated_text),)
    for alternatives in expect.output_includes_any:
        if not any(contains(text, phrase) for text in outputs for phrase in alternatives):
            failures.append(f"output lacks {alternatives}")
    for phrase in expect.output_excludes:
        if any(contains(text, phrase) for text in outputs):
            failures.append(f"output keeps {phrase!r}")
    score.passed = not failures
    return score


def summarise(rows) -> dict:
    """rows: dicts with `case`, `score`, `duration_ms`, `calls` (ProviderUsage list) and `budget`."""
    scores = [row["score"] for row in rows]
    by_group = defaultdict(lambda: [0, 0])
    for score in scores:
        by_group[score.group][0] += score.passed
        by_group[score.group][1] += 1
    routed = [(row["case"].expect.source_language, row["score"].source_language) for row in rows
              if row["case"].expect.source_language]
    error_cases = [(row["case"].expect.has_errors, row["score"].has_errors) for row in rows
                   if row["case"].expect.has_errors is not None and row["score"].has_errors is not None]
    true_positive = sum(1 for expected, actual in error_cases if expected and actual)
    false_positive = sum(1 for expected, actual in error_cases if not expected and actual)
    false_negative = sum(1 for expected, actual in error_cases if expected and not actual)
    false_error_pool = [row["score"] for row in rows if row["case"].group in FALSE_ERROR_GROUPS and row["score"].has_errors is not None]
    forbidden = [row["score"] for row in rows if row["case"].expect.natural == "forbidden" and row["score"].natural_given is not None]
    calls = [call for row in rows for call in row["calls"]]
    ratios = [call.output_tokens / row["budget"] for row in rows for call in row["calls"]
              if call.output_tokens is not None and row["budget"]]
    reasons = Counter(failure.split(" ")[0] for score in scores for failure in score.failures)
    return {
        "cases": len(scores),
        "passed": sum(score.passed for score in scores),
        "pass_rate": round(sum(score.passed for score in scores) / len(scores), 3) if scores else None,
        "groups": {group: {"passed": passed, "cases": total, "rate": round(passed / total, 3)}
                   for group, (passed, total) in sorted(by_group.items())},
        "routing_accuracy": round(sum(expected == actual for expected, actual in routed) / len(routed), 3) if routed else None,
        "routing_confusion": dict(Counter(f"{expected}->{actual or '?'}" for expected, actual in routed)),
        "error_precision": round(true_positive / (true_positive + false_positive), 3) if true_positive + false_positive else None,
        "error_recall": round(true_positive / (true_positive + false_negative), 3) if true_positive + false_negative else None,
        "false_error_rate": round(sum(bool(score.has_errors) for score in false_error_pool) / len(false_error_pool), 3)
                            if false_error_pool else None,
        "paraphrase_rate": round(sum(bool(score.natural_given) for score in forbidden) / len(forbidden), 3) if forbidden else None,
        "error_codes": dict(Counter(score.error_code for score in scores if score.error_code)),
        "failure_reasons": dict(reasons),
        "provider_calls": len(calls),
        "latency_ms": percentiles([row["duration_ms"] for row in rows]),
        "provider_ms": percentiles([call.duration_ms for call in calls]),
        "output_tokens": percentiles([call.output_tokens for call in calls]),
        "reasoning_tokens": percentiles([call.reasoning_tokens for call in calls]),
        "cached_input_ratio": round(sum(call.cached_input_tokens or 0 for call in calls) /
                                    max(1, sum(call.input_tokens or 0 for call in calls)), 3),
        "max_output_budget_ratio": round(max(ratios), 3) if ratios else None,
        "failing": [{"id": score.case_id, "failures": score.failures} for score in scores if not score.passed],
    }
