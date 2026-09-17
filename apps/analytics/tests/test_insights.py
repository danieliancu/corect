"""The overview's insight panel: read off the figures already on the page, never from a query of its own."""
from decimal import Decimal

from django.test import TestCase

from apps.analytics.insights import dashboard_insights

EMPTY = {
    "totals": {"requests": 0, "failures": 0, "rejections": 0, "without_cost": 0},
    "costs": {"total": None, "text_share": None, "learning_share": None, "stt_share": None, "tts_share": None,
              "audio_share": None},
    "audio_totals": {"audio_calls": 0, "audio_without_cost": 0},
    "learning_totals": {"learning_without_cost": 0},
    "plan_rows": [], "visitors": {"total": 0, "signed_up": 0, "signed_in": 0},
    "signup_rate": None, "success_rate": None, "plan_conversions": 0,
}


def figures(**changes):
    data = {key: dict(value) if isinstance(value, dict) else value for key, value in EMPTY.items()}
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return data


def titles(**changes):
    return [item["title"] for item in dashboard_insights(**figures(**changes))]


def levels(**changes):
    return {item["title"]: item["level"] for item in dashboard_insights(**figures(**changes))}


class DashboardInsightTests(TestCase):
    def test_an_empty_period_says_nothing_rather_than_inventing_a_trend(self):
        self.assertEqual(dashboard_insights(**EMPTY), [])

    def test_the_largest_cost_source_is_named_with_its_share(self):
        shown = titles(totals={"requests": 40}, costs={"total": Decimal("1.5"), "text_share": 0.2,
                                                       "learning_share": 0.1, "stt_share": 0.6, "tts_share": 0.1})
        self.assertIn("Voice input leads the AI cost", shown)

    def test_a_cost_share_is_not_claimed_before_there_is_enough_spend(self):
        self.assertNotIn("Text AI leads the AI cost",
                         titles(totals={"requests": 3}, costs={"total": Decimal("0.01"), "text_share": 0.9}))

    def test_a_high_failure_rate_is_an_alert_and_a_low_one_is_reported_as_healthy(self):
        alert = levels(totals={"requests": 100, "failures": 9}, success_rate=0.91)
        self.assertEqual(alert["9.0% of text requests failed"], "alert")
        healthy = levels(totals={"requests": 100, "failures": 1}, success_rate=0.99)
        self.assertEqual(healthy["Text success rate 99.0%"], "good")

    def test_a_plan_running_out_of_its_daily_quota_is_flagged(self):
        rows = [{"label": "Free", "hit_rate": 0.4, "hit": 2, "active": 5, "quota_rejections": 7},
                {"label": "Pro", "hit_rate": 0.0, "hit": 0, "active": 4, "quota_rejections": 0}]
        shown = levels(plan_rows=rows)
        self.assertEqual(shown["Free: 40.0% used up the daily quota"], "watch")
        self.assertNotIn("Pro: 0.0% used up the daily quota", shown)

    def test_conversion_is_good_above_the_threshold_and_neutral_below_it(self):
        strong = levels(visitors={"total": 100, "signed_up": 9, "signed_in": 2}, signup_rate=0.09)
        self.assertEqual(strong["Signup conversion 9.0%"], "good")
        weak = levels(visitors={"total": 100, "signed_up": 1, "signed_in": 0}, signup_rate=0.01)
        self.assertEqual(weak["Signup conversion 1.0%"], "info")

    def test_calls_recorded_without_a_price_are_called_out_across_all_three_ledgers(self):
        shown = dashboard_insights(**figures(totals={"without_cost": 2}, learning_totals={"learning_without_cost": 3},
                                             audio_totals={"audio_without_cost": 1}))
        item = next(row for row in shown if "without pricing" in row["title"])
        self.assertEqual((item["title"], item["level"]), ("6 calls without pricing", "watch"))

    def test_one_missing_price_is_written_in_the_singular(self):
        shown = dashboard_insights(**figures(totals={"without_cost": 1}))
        self.assertEqual(next(row for row in shown if "without pricing" in row["title"])["title"],
                         "1 call without pricing")
