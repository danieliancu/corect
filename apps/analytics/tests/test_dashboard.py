from datetime import timedelta
from decimal import Decimal

from django.apps import apps as app_registry
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.analytics.backfill import backfill_usage_events
from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.assistant.models import AssistantRequest

DASHBOARD, USERS, VISITORS = "/admin/analytics/", "/admin/analytics/users/", "/admin/analytics/visitors/"


class AnalyticsReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user("staff", "staff@example.com", password="pw-staff-1234", is_staff=True)
        cls.learner = User.objects.create_user("learner", "learner@example.com", "pw-learner-1234")
        cls.visitor = AnonymousVisitor.objects.create(first_seen_at=now - timedelta(days=40), last_seen_at=now,
                                                      converted_user=cls.learner, converted_via="signup",
                                                      converted_at=now - timedelta(days=1))
        cls.lurker = AnonymousVisitor.objects.create(first_seen_at=now - timedelta(days=2), last_seen_at=now)

        def event(**fields):
            return UsageEvent.objects.create(**{"model": "test-model", "status": "success", "request_type": "correction",
                                                **fields})
        event(audience="registered", user=cls.learner, input_tokens=1000, output_tokens=200, total_tokens=1200,
              estimated_cost=Decimal("0.00044"))
        event(audience="registered", user=cls.learner, request_type="translation", input_tokens=300, output_tokens=50,
              total_tokens=350, estimated_cost=Decimal("0.00012"), created_at=now - timedelta(days=10))
        event(audience="anonymous", visitor=cls.visitor, input_tokens=500, output_tokens=100, total_tokens=600,
              estimated_cost=Decimal("0.00022"), created_at=now - timedelta(days=40))
        event(audience="anonymous", visitor=cls.lurker, status="failed", error_code="timeout",
              created_at=now - timedelta(days=2))
        event(audience="anonymous", visitor=cls.lurker, status="rejected", error_code="rate_limit", input_tokens=0,
              output_tokens=0, total_tokens=0, estimated_cost=Decimal(0))

    def pages(self):
        return [DASHBOARD, USERS, VISITORS, f"{USERS}{self.learner.pk}/", f"{VISITORS}{self.visitor.pk}/"]

    def test_only_staff_can_open_analytics(self):
        for member in (None, self.learner):
            if member:
                self.client.force_login(member)
            for page in self.pages():
                with self.subTest(member=member, page=page):
                    response = self.client.get(page)
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("/admin/login/", response["Location"])
        superuser = User.objects.create_superuser("root", "root@example.com", "pw-root-1234")
        for member in (self.staff, superuser):
            self.client.force_login(member)
            for page in self.pages():
                with self.subTest(member=member.username, page=page):
                    self.assertEqual(self.client.get(page).status_code, 200)

    def test_dashboard_totals_follow_period_audience_and_type_filters(self):
        self.client.force_login(self.staff)

        def totals(**params):
            return self.client.get(DASHBOARD, params).context["totals"]
        everything = totals(period="all")
        self.assertEqual((everything["requests"], everything["successes"], everything["failures"],
                          everything["rejections"], everything["corrections"], everything["translations"]),
                         (5, 3, 1, 1, 4, 1))
        self.assertEqual((everything["tokens_in"], everything["tokens_out"], everything["tokens_total"],
                          everything["cost"]), (1800, 350, 2150, Decimal("0.00078")))
        self.assertEqual([totals(period=p)["requests"] for p in ("90d", "30d", "7d", "today")], [5, 4, 3, 2])
        registered = totals(period="all", audience="registered")
        self.assertEqual((registered["requests"], registered["tokens_total"], registered["cost"]),
                         (2, 1550, Decimal("0.00056")))
        self.assertEqual(totals(period="90d", audience="anonymous")["requests"], 3)
        self.assertEqual(totals(period="all", type="translation")["requests"], 1)
        response = self.client.get(DASHBOARD, {"period": "all"})
        self.assertEqual(response.context["success_rate"], 0.75)
        self.assertEqual((response.context["users"]["total"], response.context["visitors"]["all_time"]), (2, 2))
        self.assertEqual((response.context["visitors"]["signed_up"], response.context["signup_rate"]), (1, 0.5))
        self.assertEqual([(row["error_code"], row["total"]) for row in response.context["errors"]],
                         [("rate_limit", 1), ("timeout", 1)])
        self.assertEqual(response.context["windows"]["today"], 2)
        self.assertContains(response, "£0.0006")  # $0.00078 at £0.74 per dollar
        self.assertContains(response, self.visitor.short_id)

    def test_plan_summary_counts_successes_quota_rejections_and_who_used_up_the_quota(self):
        now = timezone.now()
        free_user = User.objects.create_user("free-one", "free-one@example.com", password="pw-free-1234")

        def event(**fields):
            return UsageEvent.objects.create(**{"model": "test-model", "request_type": "unclassified", **fields})
        event(audience="registered", plan="free", user=free_user, status="success", request_type="correction")
        event(audience="registered", plan="free", user=free_user, status="rejected", error_code="quota_exhausted")
        event(audience="registered", plan="pro", user=self.learner, status="success", request_type="correction")
        # This visitor used up its anonymous quota two days ago and signed up yesterday.
        event(audience="anonymous", plan="anonymous", visitor=self.visitor, status="rejected", error_code="quota_exhausted",
              created_at=now - timedelta(days=2))
        event(audience="anonymous", plan="anonymous", visitor=self.lurker, status="rejected", error_code="rate_limit")
        self.client.force_login(self.staff)
        response = self.client.get(DASHBOARD, {"period": "all"})
        rows = {row["plan"]: row for row in response.context["plan_rows"]}
        self.assertEqual([row["plan"] for row in response.context["plan_rows"]], ["anonymous", "free", "pro"])
        free = rows["free"]
        self.assertEqual((free["successes"], free["quota_rejections"], free["hit"], free["active"], free["hit_rate"]),
                         (1, 1, 1, 1, 1.0))
        self.assertEqual((rows["pro"]["successes"], rows["pro"]["quota_rejections"], rows["pro"]["hit"]), (1, 0, 0))
        anonymous = rows["anonymous"]
        self.assertEqual((anonymous["quota_rejections"], anonymous["hit"], anonymous["active"]), (1, 1, 2))
        self.assertEqual(response.context["plan_conversions"], 1)
        self.assertContains(response, "Plans and daily quota")

    def test_users_report_aggregates_per_user_without_extra_queries_per_row(self):
        self.client.force_login(self.staff)
        response = self.client.get(USERS, {"period": "all"})
        learner = next(member for member in response.context["page_obj"] if member.pk == self.learner.pk)
        self.assertEqual((learner.requests, learner.corrections, learner.translations, learner.successes,
                          learner.tokens_total, learner.cost), (2, 1, 1, 2, 1550, Decimal("0.00056")))
        self.assertIsNotNone(learner.last_active)
        seven_days = self.client.get(USERS, {"period": "7d"}).context["page_obj"]
        self.assertEqual(next(m for m in seven_days if m.pk == self.learner.pk).requests, 1)
        self.assertEqual([m.pk for m in self.client.get(USERS, {"q": "learner@"}).context["page_obj"]], [self.learner.pk])
        with CaptureQueriesContext(connection) as few:
            self.client.get(USERS, {"period": "all"})
        for index in range(5):
            member = User.objects.create_user(f"extra{index}", f"extra{index}@example.com")
            UsageEvent.objects.create(audience="registered", user=member, request_type="correction", status="success")
        with CaptureQueriesContext(connection) as many:
            self.client.get(USERS, {"period": "all"})
        self.assertEqual(len(few), len(many))

    def test_visitors_report_counts_anonymous_usage_conversion_and_short_id_search(self):
        self.client.force_login(self.staff)
        response = self.client.get(VISITORS, {"period": "all"})
        rows = {visitor.pk: visitor for visitor in response.context["page_obj"]}
        self.assertEqual((rows[self.visitor.pk].requests, rows[self.visitor.pk].tokens_total, rows[self.lurker.pk].requests),
                         (1, 600, 2))
        self.assertContains(response, self.visitor.short_id)
        self.assertContains(response, "learner")
        self.assertEqual([v.pk for v in self.client.get(VISITORS).context["page_obj"]], [self.lurker.pk])
        search = self.client.get(VISITORS, {"period": "all", "q": self.lurker.short_id})
        self.assertEqual([v.pk for v in search.context["page_obj"]], [self.lurker.pk])
        converted = self.client.get(VISITORS, {"period": "all", "converted": "yes"})
        self.assertEqual([v.pk for v in converted.context["page_obj"]], [self.visitor.pk])
        with CaptureQueriesContext(connection) as few:
            self.client.get(VISITORS, {"period": "all"})
        for _ in range(5):
            extra = AnonymousVisitor.objects.create()
            UsageEvent.objects.create(audience="anonymous", visitor=extra, request_type="translation", status="success")
        with CaptureQueriesContext(connection) as many:
            self.client.get(VISITORS, {"period": "all"})
        self.assertEqual(len(few), len(many))

    def test_detail_pages_show_windows_and_conversion(self):
        self.client.force_login(self.staff)
        summary = self.client.get(f"{USERS}{self.learner.pk}/").context["summary"]
        self.assertEqual((summary["today"], summary["last_7"], summary["last_30"], summary["requests"]), (1, 1, 2, 2))
        response = self.client.get(f"{VISITORS}{self.visitor.pk}/")
        self.assertEqual(response.context["summary"]["before_conversion"], 1)
        self.assertEqual(response.context["after"]["requests"], 1)
        self.assertContains(response, "learner")

    def test_historical_requests_are_backfilled_once_with_unknown_tokens(self):
        old = AssistantRequest.objects.create(user=self.learner, request_type="translation", original_text="Salut",
                                              result_text="Hello", model_used="gpt-old", prompt_version="2026-09-v1",
                                              status="success")
        created_at = timezone.now() - timedelta(days=100)
        AssistantRequest.objects.filter(pk=old.pk).update(created_at=created_at)
        backfill_usage_events(app_registry)
        backfill_usage_events(app_registry)
        event = UsageEvent.objects.get(assistant_request=old)
        self.assertEqual((event.is_backfilled, event.user, event.request_type, event.model, event.prompt_version,
                          event.status, event.created_at), (True, self.learner, "translation", "gpt-old", "2026-09-v1",
                                                            "success", created_at))
        self.assertIsNone(event.total_tokens)
        self.assertIsNone(event.estimated_cost)
        self.client.force_login(self.staff)
        response = self.client.get(DASHBOARD, {"period": "all", "audience": "registered"})
        self.assertEqual((response.context["totals"]["requests"], response.context["totals"]["without_tokens"]), (3, 1))
        self.assertContains(response, "gpt-old")
        self.assertEqual(self.client.get(DASHBOARD, {"period": "all", "model": "gpt-old"}).context["totals"]["requests"], 1)
