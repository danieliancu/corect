"""Guards shared by the live eval and the benchmarks: they make real, billed provider calls only when asked to."""
from django.conf import settings
from django.core.management.base import CommandError


def require_live(options) -> None:
    if not options.get("live"):
        raise CommandError("This command makes real, billed OpenAI calls. Run it again with --live.")
    if not settings.OPENAI_API_KEY or not settings.OPENAI_MODEL or settings.OPENAI_MODEL == "test-model":
        raise CommandError("Set OPENAI_API_KEY and a real OPENAI_MODEL before running live checks.")
