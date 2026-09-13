from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.analytics.models import UsageEvent
from apps.assistant.services.pricing import ModelPrice, estimate_cost, parse_pricing
from apps.assistant.services.usage import ProviderUsage

PRICING = parse_pricing({"test-model": {"input_per_1m": "0.20", "cached_input_per_1m": "0.02", "output_per_1m": "1.20"}})


def usage(**overrides):
    values = dict(model="test-model", response_model="", input_tokens=1258, cached_input_tokens=1245, output_tokens=206,
                  reasoning_tokens=0, total_tokens=1464)
    values.update(overrides)
    return ProviderUsage(**values)


@override_settings(OPENAI_PRICING=PRICING)
class PricingTests(SimpleTestCase):
    def test_cost_charges_uncached_cached_and_output_tokens_separately(self):
        # (13 × 0.20 + 1245 × 0.02 + 206 × 1.20) per million tokens
        self.assertEqual(estimate_cost(usage()), Decimal("0.00027470"))

    def test_missing_cached_count_is_charged_at_the_input_price(self):
        self.assertEqual(estimate_cost(usage(cached_input_tokens=None)), Decimal("0.00049880"))

    def test_provider_model_name_is_a_pricing_fallback(self):
        self.assertEqual(estimate_cost(usage(model="unpriced", response_model="test-model")), Decimal("0.00027470"))

    def test_unknown_model_or_tokens_give_no_cost(self):
        for case in (usage(model="unpriced"), usage(input_tokens=None), usage(output_tokens=None), None):
            with self.subTest(case=case):
                self.assertIsNone(estimate_cost(case))
        with self.settings(OPENAI_PRICING={}):
            self.assertIsNone(estimate_cost(usage()))

    def test_pricing_configuration_is_validated(self):
        prices = parse_pricing({"m": {"input_per_1m": 1, "cached_input_per_1m": "0.5", "output_per_1m": "2"}})
        self.assertEqual(prices["m"], ModelPrice(Decimal(1), Decimal("0.5"), Decimal(2)))
        bad_tables = ([], {"m": {"input_per_1m": "1"}},
                      {"m": {"input_per_1m": "x", "cached_input_per_1m": "1", "output_per_1m": "1"}},
                      {"m": {"input_per_1m": "-1", "cached_input_per_1m": "1", "output_per_1m": "1"}},
                      {"m": {"input_per_1m": "NaN", "cached_input_per_1m": "1", "output_per_1m": "1"}})
        for bad in bad_tables:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_pricing(bad)


class RecalculateCostsTests(TestCase):
    def test_only_missing_costs_with_recorded_tokens_are_filled(self):
        common = dict(request_type="correction", status="success", model="test-model")
        missing = UsageEvent.objects.create(audience="anonymous", input_tokens=1258, cached_input_tokens=1245,
                                            output_tokens=206, total_tokens=1464, **common)
        kept = UsageEvent.objects.create(audience="anonymous", input_tokens=10, output_tokens=10, total_tokens=20,
                                         estimated_cost=Decimal("9"), **common)
        historical = UsageEvent.objects.create(audience="registered", is_backfilled=True, **common)
        with override_settings(OPENAI_PRICING=PRICING):
            call_command("recalculate_usage_costs", stdout=StringIO())
        for event in (missing, kept, historical):
            event.refresh_from_db()
        self.assertEqual(missing.estimated_cost, Decimal("0.00027470"))
        self.assertEqual(kept.estimated_cost, Decimal("9"))
        self.assertIsNone(historical.estimated_cost)
