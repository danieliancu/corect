"""Daily plan quotas and what each plan is told when it runs out."""
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import override_settings
from playwright.sync_api import expect

from apps.assistant.services.voice import AudioUsage
from apps.assistant.tests.examples import correction_result
from .base import BrowserTestCase
from .mocks import MEDIA_MOCKS


class PlanQuotaChecks(BrowserTestCase):
    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200}, VOICE_REALTIME_ENABLED=False)
    def test_anonymous_quota_invites_to_create_an_account_and_loaded_speech_still_plays(self):
        speak = patch("apps.assistant.voice_views.synthesize_speech", return_value=(
            b"ID3fake-mp3", AudioUsage(model="gpt-4o-mini-tts", voice="cedar", input_tokens=8, output_tokens=90,
                                       total_tokens=98))).start()
        self.page.add_init_script(MEDIA_MOCKS)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.set_usage(self.ANONYMOUS_ACTOR, 3)
        self.naturalize_text()
        self.naturalize_text()
        expect(self.page.get_by_text("utilizări rămase")).to_have_count(0)  # the count is shown only on the profile
        speaker = self.page.get_by_role("button", name="Ascultă varianta corectă în engleză britanică")
        speaker.click()
        expect(speaker).to_have_attribute("aria-pressed", "true")
        self.assertEqual(self.usage(self.ANONYMOUS_ACTOR), 5)  # Listening used nothing.
        self.naturalize_text()
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai folosit cele 5 utilizări gratuite de azi. Creează un cont gratuit și primești 20 pe zi.")
        expect(alert.get_by_role("link", name="Creează cont gratuit")).to_have_attribute("href", "/accounts/signup/")
        expect(self.page.locator("#text")).to_have_value(correction_result().original_text)  # The text is kept.
        expect(self.page.locator(".quota-note")).to_have_count(0)
        self.assertNotIn("voce", alert.inner_text().lower())  # There is no separate voice allowance.
        self.assertEqual(self.usage(self.ANONYMOUS_ACTOR), 5)
        self.assert_no_overflow(self.page, 390)
        self.page.screenshot(path=str(self.artifacts / "quota-anonymous-390.png"), full_page=True)
        self.assertEqual(speak.call_count, 1)
        self.assertEqual(self.errors, [])

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200})
    def test_free_quota_points_to_pro_and_pro_gets_the_fair_use_message(self):
        self.sign_in_learner("quota-learner")
        user = self.in_database_thread(lambda: User.objects.get(username="quota-learner"))
        actor = f"user:{user.pk}"
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.set_usage(actor, 19)
        self.naturalize_text("The twentieth text of the day.")
        expect(self.page.get_by_text("utilizări rămase")).to_have_count(0)
        self.naturalize_text("One text too many.")
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai folosit cele 20 de utilizări de azi. Cu Pro ai cereri nelimitate, "
                                      "în regim Fair Use.")
        expect(alert.get_by_role("link", name="Vezi planul Pro")).to_have_attribute("href", "/about/#plans")
        self.page.screenshot(path=str(self.artifacts / "quota-free-1440.png"))
        self.page.goto(self.live_server_url + "/accounts/profile/")
        expect(self.page.locator(".plan-status")).to_have_text("Plan Free · 0 din 20 de utilizări rămase astăzi")
        for path in ("/history/", "/progress/", "/mistakes/"):  # Nothing else is blocked.
            self.page.goto(self.live_server_url + path)
            expect(self.page.locator("h1")).to_be_visible()
        self.in_database_thread(lambda: user.groups.add(Group.objects.get_or_create(name="Pro")[0]))
        self.set_usage(actor, 199)
        self.page.goto(self.live_server_url)
        self.naturalize_text("The two hundredth text of the day.")
        expect(self.page.locator(".result-text").first).to_be_visible()
        expect(self.page.locator(".quota-note")).to_have_count(0)  # Pro is not counted down.
        self.naturalize_text("One Pro text too many.")
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai atins limita Fair Use de 200 de utilizări pentru astăzi. Limita se resetează la "
                                      "miezul nopții (ora Regatului Unit).")
        expect(alert.get_by_role("link")).to_have_count(0)  # No plan to upgrade to.
        self.page.goto(self.live_server_url + "/accounts/profile/")
        expect(self.page.locator(".plan-status")).to_have_text("Plan Pro · Cereri nelimitate (Fair Use)")
        self.assertEqual(self.usage(actor), 200)
        self.assertEqual(self.errors, [])
