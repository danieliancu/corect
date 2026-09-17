"""axe-core WCAG 2 A/AA audits of the main pages and states."""
import json
import os
from pathlib import Path

from django.conf import settings
from playwright.sync_api import expect

from apps.assistant.tests.examples import correction_result
from .base import BrowserTestCase
from .mocks import ROMANIAN, UNNATURAL


class AccessibilityChecks(BrowserTestCase):
    # ----- Accessibility -----
    def test_accessibility_with_axe(self):
        source = Path(os.environ.get("AXE_CORE_PATH") or Path(settings.BASE_DIR) / "artifacts" / "axe.min.js")
        if not source.exists():
            self.skipTest("axe-core not available: set AXE_CORE_PATH to axe.min.js")
        script = source.read_text(encoding="utf-8")
        checked = []

        def audit(name):
            self.page.add_script_tag(content=script)
            violations = self.page.evaluate("""async () => (await axe.run(document, {runOnly: {type: 'tag',
                values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => ({id: v.id, impact: v.impact,
                help: v.help, nodes: v.nodes.slice(0, 5).map(n => ({target: n.target.join(' '),
                summary: n.failureSummary}))}))""")
            (self.artifacts / f"axe-{name}.json").write_text(json.dumps(violations, indent=2), encoding="utf-8")
            checked.append((name, [violation for violation in violations if violation["impact"] in ("serious", "critical")]))

        for width in (390, 1440):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.goto(self.live_server_url)
            audit(f"home-{width}")
        self.page.set_viewport_size({"width": 390, "height": 844})
        for value, name in ((correction_result().original_text, "english"), (UNNATURAL, "unnatural"), (ROMANIAN, "romanian")):
            self.page.goto(self.live_server_url)
            self.page.locator("#text").fill(value)
            self.submit()
            expect(self.page.locator(".result-loading")).to_have_count(0)
            expect(self.page.locator("#result-actions")).to_be_visible()
            self.page.wait_for_timeout(400)  # Let the buttons' 0.15 s colour transition finish before measuring contrast.
            audit(f"result-{name}")
        self.page.goto(self.live_server_url + "/about/")
        audit("despre")
        with self.settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200}):
            self.set_usage(self.ANONYMOUS_ACTOR, 5)
            self.page.goto(self.live_server_url)
            self.page.locator("#text").fill(correction_result().original_text)
            self.submit()
            expect(self.page.locator(".quota-box")).to_be_visible()
            self.page.wait_for_timeout(400)
            audit("quota-anonymous")
        self.assertEqual([(name, found) for name, found in checked if found], [])
