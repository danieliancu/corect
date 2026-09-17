import json
import logging
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.analytics.insights import spend_insight
from apps.analytics.models import AudioUsageEvent, LearningUsageEvent, UsageEvent
from apps.analytics.monitoring import ai_spend, provider_error_rate
from apps.core.log_format import JsonFormatter, RequestContextFilter
from apps.core.middleware import RequestContextMiddleware
from apps.core.monitoring import (DATABASE, GUARDRAIL, PROVIDER, QUOTA, category_for_code, category_for_exception,
                                  log_event, log_failure)
from apps.core.sentry import scrub_event

SECRET = "learner-typed-this-secret@example.com"


class HealthEndpointTests(TestCase):
    def test_liveness_answers_without_touching_the_database(self):
        with self.assertNumQueries(0):
            response = self.client.get("/healthz")
        self.assertEqual((response.status_code, response.json()), (200, {"status": "ok"}))
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_readiness_checks_the_database(self):
        response = self.client.get("/readyz")
        self.assertEqual(response.json(), {"status": "ok", "checks": {"database": "ok"}})

    def test_readiness_reports_a_database_outage_without_details(self):
        with patch("apps.core.health.connection.cursor", side_effect=DatabaseError("password authentication failed")):
            response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable", "checks": {"database": "error"}})
        self.assertNotIn("password", response.content.decode())

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_plain_http_health_checks_are_not_redirected(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/readyz").status_code, 200)
        self.assertEqual(self.client.get("/about/").status_code, 301)

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post("/healthz").status_code, 405)


class CategoryTests(SimpleTestCase):
    def test_codes_are_grouped_by_who_has_to_act(self):
        self.assertEqual(category_for_code("quota_exhausted"), QUOTA)
        self.assertEqual(category_for_code("learning_budget_exhausted"), QUOTA)
        self.assertEqual(category_for_code("timeout"), PROVIDER)
        self.assertEqual(category_for_code("tts_failed"), PROVIDER)
        self.assertEqual(category_for_code("content_blocked"), GUARDRAIL)
        self.assertEqual(category_for_code("database_unavailable"), DATABASE)
        self.assertEqual(category_for_code("something_new"), "application")

    def test_exceptions_are_classified(self):
        self.assertEqual(category_for_exception(IntegrityError()), DATABASE)
        self.assertEqual(category_for_exception(ValueError()), "application")
        import openai
        self.assertEqual(category_for_exception(openai.APIConnectionError(request=None)), PROVIDER)


def capture(logger_name="apps.test.monitoring"):
    logger = logging.getLogger(logger_name)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream, handler


class StructuredLoggingTests(SimpleTestCase):
    def setUp(self):
        self.logger, self.stream, handler = capture()
        self.addCleanup(self.logger.removeHandler, handler)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_failures_carry_their_category_and_level(self):
        log_failure(self.logger, "assistant_failed", "quota_exhausted", kind="correction")
        log_failure(self.logger, "assistant_failed", "timeout", kind="correction")
        log_event(self.logger, logging.ERROR, "usage_event_unavailable", DATABASE)
        quota, provider, database = self.lines()
        self.assertEqual((quota["level"], quota["category"], quota["message"]),
                         ("INFO", QUOTA, "assistant_failed code=quota_exhausted kind=correction"))
        self.assertEqual((provider["level"], provider["category"], provider["event"]),
                         ("WARNING", PROVIDER, "assistant_failed"))
        self.assertEqual((database["level"], database["category"]), ("ERROR", DATABASE))

    def test_exception_messages_never_reach_json_logs(self):
        try:
            raise IntegrityError(f'duplicate key value violates unique constraint: Key (email)=({SECRET})')
        except IntegrityError:
            self.logger.exception("Internal Server Error: /accounts/signup/")
        output = self.stream.getvalue()
        self.assertNotIn(SECRET, output)
        entry = self.lines()[0]
        self.assertEqual(entry["category"], DATABASE)
        self.assertEqual(entry["exc_type"], "django.db.utils.IntegrityError")
        self.assertIn("test_monitoring.py", entry["stack"])

    def test_request_context_is_attached_and_query_strings_are_not(self):
        def view(request):
            self.logger.warning("inside")
            from django.http import HttpResponse
            return HttpResponse("ok")

        response = RequestContextMiddleware(view)(RequestFactory().get("/history/?q=" + SECRET))
        entry = self.lines()[0]
        self.assertEqual((entry["method"], entry["path"]), ("GET", "/history/"))
        self.assertEqual(entry["request_id"], response["X-Request-ID"])
        self.assertNotIn(SECRET, self.stream.getvalue())
        self.logger.warning("outside")
        self.assertNotIn("request_id", self.lines()[1])

    def test_server_error_logs_written_after_the_middleware_keep_the_request_id(self):
        request = RequestFactory().get("/readyz")
        request.request_id = "abc123"
        self.logger.error("Service Unavailable: %s", request.path, extra={"request": request, "status_code": 503})
        entry = self.lines()[0]
        self.assertEqual((entry["request_id"], entry["path"], entry["status"]), ("abc123", "/readyz", 503))


class SentryScrubbingTests(SimpleTestCase):
    def test_personal_data_and_content_are_removed(self):
        event = {
            "request": {"url": "https://corect.uk/naturalize/", "method": "POST", "data": {"text": SECRET},
                        "cookies": {"sessionid": "s"}, "headers": {"Cookie": "s"}, "query_string": "q=1",
                        "env": {"REMOTE_ADDR": "198.51.100.7"}},
            "user": {"id": 7, "email": SECRET, "ip_address": "198.51.100.7", "username": "ana"},
            "exception": {"values": [{"type": "IntegrityError", "value": SECRET,
                                      "stacktrace": {"frames": [{"function": "save", "vars": {"text": SECRET}}]}}]},
            "breadcrumbs": {"values": [{"message": SECRET}]},
            "extra": {"category": "database", "text": SECRET},
        }
        scrubbed = scrub_event(event, {"exc_info": (IntegrityError, IntegrityError(), None)})
        self.assertNotIn(SECRET, json.dumps(scrubbed))
        self.assertNotIn("198.51.100.7", json.dumps(scrubbed))
        self.assertEqual(scrubbed["user"], {"id": 7})
        self.assertEqual(scrubbed["request"], {"url": "https://corect.uk/naturalize/", "method": "POST"})
        self.assertEqual(scrubbed["tags"]["category"], "database")

    def test_exceptions_without_a_logged_category_are_classified(self):
        scrubbed = scrub_event({}, {"exc_info": (DatabaseError, DatabaseError(), None)})
        self.assertEqual(scrubbed["tags"]["category"], DATABASE)

    def test_sentry_is_off_without_a_dsn(self):
        import sentry_sdk
        self.assertFalse(sentry_sdk.get_client().is_active())


def usage(model=UsageEvent, **fields):
    defaults = {"model": "test-model", "status": "success", "audience": "anonymous"}
    if model is UsageEvent:
        defaults["request_type"] = "correction"
    if model is LearningUsageEvent:
        defaults.update(feature="practice", audience="registered")
    if model is AudioUsageEvent:
        defaults["operation"] = "speech"
    return model.objects.create(**{**defaults, **fields})


@override_settings(AI_COST_ALERT_DAILY_USD=Decimal("1.00"), AI_COST_ALERT_HOURLY_USD=Decimal("0.50"),
                   ALERT_ERROR_RATE_PERCENT=20, ALERT_MIN_REQUESTS=5)
class ProductionHealthCommandTests(TestCase):
    def run_check(self, *args):
        out = StringIO()
        try:
            call_command("check_production_health", *args, stdout=out)
            code = 0
        except SystemExit as exit_:
            code = exit_.code
        output = out.getvalue().strip()
        return code, json.loads(output) if output else None

    def test_healthy_system_exits_zero(self):
        usage(estimated_cost=Decimal("0.10"), provider_calls=1)
        code, report = self.run_check()
        self.assertEqual((code, report["status"], report["alerts"]), (0, "ok", []))
        self.assertEqual(report["ai_cost_today_usd"], "0.10000000")
        self.assertEqual(self.run_check("--quiet"), (0, None))

    def test_spend_above_the_thresholds_alerts(self):
        usage(estimated_cost=Decimal("0.60"), provider_calls=1)
        usage(LearningUsageEvent, estimated_cost=Decimal("0.30"), provider_calls=1)
        usage(AudioUsageEvent, estimated_cost=Decimal("0.20"), provider_calls=1)
        code, report = self.run_check("--quiet")
        self.assertEqual(code, 1)
        self.assertEqual({alert["check"] for alert in report["alerts"]}, {"ai_cost_today", "ai_cost_last_hour"})
        self.assertEqual(report["ai_cost_today_by_ledger_usd"],
                         {"text": "0.60000000", "learning": "0.30000000", "audio": "0.20000000"})

    def test_old_spend_does_not_count_towards_today(self):
        usage(estimated_cost=Decimal("5.00"), provider_calls=1, created_at=timezone.now() - timedelta(days=2))
        self.assertEqual(self.run_check()[0], 0)

    def test_provider_outage_alerts_but_quota_refusals_do_not(self):
        for _ in range(10):
            usage(status="rejected", error_code="quota_exhausted")
        self.assertEqual(self.run_check()[0], 0)
        for _ in range(4):
            usage(status="failed", error_code="timeout")
        usage(provider_calls=1, estimated_cost=Decimal("0.001"))
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual([alert["check"] for alert in report["alerts"]], ["provider_error_rate"])
        self.assertEqual((report["ai_requests_last_hour"], report["provider_failures_last_hour"]), (5, 4))

    def test_unpriced_calls_are_reported(self):
        usage(estimated_cost=None, provider_calls=1)
        code, report = self.run_check()
        self.assertEqual((code, report["unpriced_calls_today"]), (1, 1))

    def test_database_outage_alerts(self):
        with patch("apps.core.management.commands.check_production_health.database_ok", return_value=False):
            code, report = self.run_check()
        self.assertEqual((code, report["alerts"]), (1, [{"check": "database", "category": "database"}]))


class SpendFigureTests(TestCase):
    def test_spend_and_error_rate_helpers(self):
        now = timezone.now()
        usage(estimated_cost=Decimal("0.25"), provider_calls=1)
        usage(LearningUsageEvent, estimated_cost=None, provider_calls=1)
        usage(status="failed", error_code="content_blocked")
        spend = ai_spend(now - timedelta(hours=1))
        self.assertEqual((spend.cost, spend.unpriced), (Decimal("0.25"), 1))
        self.assertEqual(provider_error_rate(now - timedelta(hours=1)).requests, 2)

    def test_dashboard_warns_about_todays_spend(self):
        self.assertIsNone(spend_insight(Decimal("1"), Decimal("10")))
        self.assertEqual(spend_insight(Decimal("8.5"), Decimal("10"))["level"], "watch")
        self.assertEqual(spend_insight(Decimal("11"), Decimal("10"))["level"], "alert")
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        usage(estimated_cost=Decimal("30"), provider_calls=1)
        self.client.force_login(staff)
        self.assertContains(self.client.get("/admin/analytics/"), "AI spend today is above the alert threshold")
