from django.core.management.base import BaseCommand

from apps.analytics.models import UsageEvent
from apps.assistant.services.pricing import estimate_cost
from apps.assistant.services.usage import ProviderUsage

BATCH_SIZE = 1000


class Command(BaseCommand):
    help = ("Fill in missing estimated costs for usage events whose tokens were recorded, using the current "
            "OPENAI_PRICING. Existing costs are never changed.")

    def add_arguments(self, parser):
        parser.add_argument("--model", help="Only events recorded for this configured model.")

    def handle(self, *args, **options):
        events = UsageEvent.objects.filter(estimated_cost__isnull=True, input_tokens__isnull=False,
                                           output_tokens__isnull=False)
        if options["model"]:
            events = events.filter(model=options["model"])
        updated, batch = 0, []
        fields = ["model", "response_model", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                  "total_tokens"]
        for event in events.only(*fields).iterator(chunk_size=BATCH_SIZE):
            # Token counts are summed across calls; the cost formula is linear, so the estimate is exact.
            event.estimated_cost = estimate_cost(ProviderUsage(
                model=event.model, response_model=event.response_model, input_tokens=event.input_tokens,
                cached_input_tokens=event.cached_input_tokens, output_tokens=event.output_tokens,
                reasoning_tokens=event.reasoning_tokens, total_tokens=event.total_tokens))
            if event.estimated_cost is not None:
                batch.append(event)
            if len(batch) == BATCH_SIZE:
                updated += UsageEvent.objects.bulk_update(batch, ["estimated_cost"])
                batch = []
        if batch:
            updated += UsageEvent.objects.bulk_update(batch, ["estimated_cost"])
        self.stdout.write(f"Estimated the cost of {updated} usage events.")
