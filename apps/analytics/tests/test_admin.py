from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.assistant.models import AssistantRequest


class LedgerAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", "pw-root-1234")
        self.client.force_login(self.superuser)
        self.visitor = AnonymousVisitor.objects.create()
        self.event = UsageEvent.objects.create(audience="anonymous", visitor=self.visitor, request_type="correction",
                                               status="success", model="test-model", total_tokens=10,
                                               estimated_cost=Decimal("0.00001"))
        AssistantRequest.objects.create(user=self.superuser, request_type="correction", model_used="test-model",
                                        prompt_version="v", status="success")

    def test_usage_records_cannot_be_added_or_edited(self):
        self.assertEqual(self.client.get("/admin/analytics/usageevent/add/").status_code, 403)
        change = f"/admin/analytics/usageevent/{self.event.pk}/change/"
        response = self.client.get(change)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')
        self.assertEqual(self.client.post(change, {"status": "failed"}).status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "success")
        self.assertEqual(self.client.get("/admin/analytics/anonymousvisitor/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/assistant/assistantrequest/add/").status_code, 403)

    def test_changelists_show_identity_tokens_cost_and_find_short_ids(self):
        for url in ("/admin/", "/admin/analytics/usageevent/", "/admin/analytics/anonymousvisitor/",
                    "/admin/assistant/assistantrequest/", "/admin/assistant/grammarcorrection/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get("/admin/"), "Usage analytics")
        response = self.client.get("/admin/analytics/usageevent/", {"q": self.visitor.short_id})
        self.assertContains(response, self.visitor.short_id)
        self.assertContains(response, "£0.000007")  # $0.00001 at £0.74 per dollar
        self.assertEqual(self.client.get("/admin/analytics/usageevent/", {"q": "anon-zzzz"}).status_code, 200)
