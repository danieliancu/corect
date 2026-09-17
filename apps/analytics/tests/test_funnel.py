from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.analytics.funnel import funnel_summary, window_funnel
from apps.analytics.filters import day_start
from apps.analytics.models import AnonymousVisitor, FunnelEvent, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.services.localday import local_day
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value

BROWSER = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140 Safari/537.36"
EVENT_URL = "/analytics/event/"


class FunnelBeaconTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_USER_AGENT=BROWSER)

    def send(self, name, placement="", client=None, **extra):
        return (client or self.client).post(EVENT_URL, {"name": name, "placement": placement}, **extra)

    def test_an_anonymous_visit_creates_one_visitor_and_one_row_a_day(self):
        response = self.send("site_visit")
        self.assertEqual(response.status_code, 204)
        visitor = AnonymousVisitor.objects.get()
        self.assertEqual(response.cookies[VISITOR_COOKIE].value, str(visitor.pk))
        for _ in range(3):
            self.send("site_visit")
        event = FunnelEvent.objects.get()
        self.assertEqual((event.name, event.visitor, event.user, event.audience, event.plan, event.day),
                         ("site_visit", visitor, None, "anonymous", "anonymous", local_day()))
        self.assertEqual(AnonymousVisitor.objects.count(), 1)

    def test_signed_in_steps_belong_to_the_account(self):
        user = User.objects.create_user("ana", "ana@example.com", "pass")
        self.client.force_login(user)
        self.send("pricing_viewed", "home")
        self.send("pro_cta_clicked", "quota_box")
        self.send("pro_cta_clicked", "pricing_section")
        self.assertEqual(sorted(FunnelEvent.objects.values_list("name", "placement", "audience", "plan")),
                         [("pricing_viewed", "home", "registered", "free"),
                          ("pro_cta_clicked", "pricing_section", "registered", "free"),
                          ("pro_cta_clicked", "quota_box", "registered", "free")])
        self.assertFalse(FunnelEvent.objects.filter(user__isnull=True).exists())
        self.assertFalse(AnonymousVisitor.objects.exists())

    def test_nothing_is_recorded_without_analytics_consent(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        self.assertEqual(self.send("site_visit").status_code, 204)
        with override_settings(ANALYTICS_VISITOR_COOKIE=False):
            self.send("site_visit", client=Client(HTTP_USER_AGENT=BROWSER))
        self.assertFalse(FunnelEvent.objects.exists())
        self.assertFalse(AnonymousVisitor.objects.exists())

    def test_only_known_steps_and_placements_are_accepted(self):
        for name, placement in (("checkout_started", ""), ("subscription_started", ""), ("page_view", ""),
                                ("pro_cta_clicked", "anywhere"), ("site_visit", "home"), ("pricing_viewed", "")):
            with self.subTest(name=name, placement=placement):
                self.assertEqual(self.send(name, placement).status_code, 204)
        self.assertFalse(FunnelEvent.objects.exists())

    def test_bots_and_scripts_are_ignored(self):
        for agent in ("", "Googlebot/2.1", "curl/8.4", "python-requests/2.32", "HeadlessChrome/140"):
            with self.subTest(agent=agent):
                self.send("site_visit", client=Client(HTTP_USER_AGENT=agent))
        self.assertFalse(FunnelEvent.objects.exists())

    def test_the_beacon_is_rate_limited(self):
        for _ in range(40):
            self.send("pro_cta_clicked", "quota_box")
        self.assertLessEqual(FunnelEvent.objects.count(), 1)
        from apps.assistant.models import RateBucket
        self.assertEqual(RateBucket.objects.get(key__startswith="funnel:").count, 30)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True, HTTP_USER_AGENT=BROWSER)
        self.assertEqual(self.send("site_visit", client=client).status_code, 403)
        self.assertEqual(client.get(EVENT_URL).status_code, 405)

    def test_database_trouble_never_breaks_the_page(self):
        with patch("apps.analytics.services.funnel.FunnelEvent.objects.bulk_create", side_effect=DatabaseError):
            self.assertEqual(self.send("site_visit").status_code, 204)

    def test_rows_hold_no_text_address_or_email(self):
        user = User.objects.create_user("ana", "ana@example.com", "pass")
        self.client.force_login(user)
        self.send("site_visit", REMOTE_ADDR="198.51.100.7", HTTP_REFERER="https://corect.uk/?q=secret")
        row = FunnelEvent.objects.values().get()
        self.assertEqual(set(row), {"id", "name", "placement", "audience", "plan", "user_id", "visitor_id", "day",
                                    "created_at"})
        self.assertNotIn("198.51.100.7", str(row))
        self.assertNotIn("example.com", str(row))

    def test_steps_are_deleted_with_the_account_and_the_visitor(self):
        self.send("site_visit")
        AnonymousVisitor.objects.all().delete()
        user = User.objects.create_user("ana", "ana@example.com", "pass")
        self.client.force_login(user)
        self.send("site_visit")
        user.delete()
        self.assertFalse(FunnelEvent.objects.exists())

    def test_old_steps_are_removed_by_the_daily_cleanup(self):
        self.send("site_visit")
        FunnelEvent.objects.update(created_at=timezone.now() - timedelta(days=400))
        call_command("cleanup_assistant", stdout=StringIO())
        self.assertFalse(FunnelEvent.objects.exists())


class FunnelPageTests(TestCase):
    def test_script_and_hooks_are_on_the_pages_only_with_consent(self):
        home = self.client.get("/").content.decode()
        self.assertIn('data-funnel-url="/analytics/event/"', home)
        self.assertIn('data-funnel-pricing="home"', home)
        self.assertIn('data-funnel-pricing="about"', self.client.get("/about/").content.decode())
        self.assertIn("data-funnel-signup", self.client.get("/accounts/signup/").content.decode())
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        self.assertNotIn("funnel.js", self.client.get("/").content.decode())


def step(name, *, user=None, visitor=None, days_ago=0, placement=""):
    when = timezone.now() - timedelta(days=days_ago)
    return FunnelEvent.objects.create(name=name, placement=placement, user=user, visitor=visitor, day=local_day(when),
                                      created_at=when, audience="registered" if user else "anonymous")


class FunnelAggregateTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.anon_a = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
        self.anon_b = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
        self.anon_old = AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
        self.member = User.objects.create_user("member", "member@example.com", "pass")
        User.objects.filter(pk=self.member.pk).update(date_joined=now - timedelta(days=40))
        self.newcomer = User.objects.create_user("new", "new@example.com", "pass")
        # Visits: anon_a today and 3 days ago (returning), anon_b today, member 2 days ago, anon_old 20 days ago.
        step("site_visit", visitor=self.anon_a)
        step("site_visit", visitor=self.anon_a, days_ago=3)
        step("site_visit", visitor=self.anon_b)
        step("site_visit", user=self.member, days_ago=2)
        step("site_visit", visitor=self.anon_old, days_ago=20)
        step("signup_viewed", visitor=self.anon_b)
        step("pricing_viewed", visitor=self.anon_a, placement="home")
        step("pricing_viewed", user=self.member, placement="about", days_ago=2)
        step("pro_cta_clicked", user=self.member, placement="quota_box", days_ago=2)
        UsageEvent.objects.create(audience="anonymous", visitor=self.anon_a, request_type="correction",
                                  status="success", plan="anonymous")
        UsageEvent.objects.create(audience="registered", user=self.member, request_type="correction",
                                  status="success", plan="free")
        UsageEvent.objects.create(audience="registered", user=self.member, request_type="correction",
                                  status="rejected", error_code="quota_exhausted", plan="free")
        # Usage by someone without a site visit is not part of the funnel.
        UsageEvent.objects.create(audience="registered", user=self.newcomer, request_type="correction",
                                  status="success", plan="free")

    def test_today(self):
        today = window_funnel(day_start(0))
        self.assertEqual((today["visitors"], today["anonymous_visitors"], today["registered_visitors"]), (2, 2, 0))
        self.assertEqual((today["used"], today["use_rate"]), (1, 0.5))
        self.assertEqual((today["returning"], today["signup_viewed"], today["pricing_viewed"]), (0, 1, 1))
        self.assertEqual(today["pro_clicks"], 0)

    def test_last_seven_days(self):
        week = window_funnel(day_start(6))
        self.assertEqual((week["visitors"], week["anonymous_visitors"], week["registered_visitors"]), (3, 2, 1))
        self.assertEqual((week["used"], week["returning"], week["return_rate"]), (2, 1, 1 / 3))
        self.assertEqual((week["signups"], week["signup_rate"]), (1, 0.5))
        self.assertEqual((week["free_hit_rate"], week["free_hit"]), (0.5, 1))
        self.assertEqual((week["pricing_viewed"], week["pricing_rate"]), (2, 2 / 3))
        self.assertEqual((week["pro_clicks"], week["pro_click_rate"], week["pro_click_after_pricing"]),
                         (1, 1 / 3, 0.5))
        self.assertEqual((week["checkouts"], week["subscriptions"], week["checkout_rate"]), (0, 0, None))

    def test_thirty_days_and_billing_flag(self):
        summary = funnel_summary()
        self.assertEqual([window["key"] for window in summary["funnel_windows"]], ["today", "last_7", "last_30"])
        self.assertEqual(summary["funnel_windows"][2]["visitors"], 4)
        self.assertFalse(summary["billing_live"])
        step("checkout_started", user=self.member)
        step("subscription_started", user=self.member)
        summary = funnel_summary()
        self.assertTrue(summary["billing_live"])
        self.assertEqual(summary["funnel_windows"][0]["checkout_rate"], 1.0)

    def test_empty_database_has_no_rates(self):
        FunnelEvent.objects.all().delete()
        today = window_funnel(day_start(0))
        self.assertEqual((today["visitors"], today["use_rate"], today["pro_click_rate"]), (0, None, None))

    def test_dashboard_shows_the_funnel(self):
        staff = User.objects.create_user("staff", "staff@example.com", "pass", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get("/admin/analytics/")
        for text in ("Business funnel", "Are people coming, using Corect, coming back and interested in paying?",
                     "Unique visitors", "Returning (2+ days)", "Free quota hit rate", "Clicked Pro",
                     "billing not live", 'href="#an-funnel"', "Funnel, last 7 days"):
            self.assertContains(response, text)
