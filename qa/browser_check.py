"""Opt-in Chromium checks. Run separately with the documented test settings.

Backward-compatible entry point: `python manage.py test qa.browser_check` runs every browser check. The checks live
in qa/browser/, one module per area (`python manage.py test qa.browser.test_voice` runs one area).
"""
from qa.browser.test_homepage import HomepageChecks  # noqa: F401
from qa.browser.test_assistant import AssistantChecks  # noqa: F401
from qa.browser.test_accounts import AccountChecks  # noqa: F401
from qa.browser.test_learning import LearningChecks  # noqa: F401
from qa.browser.test_plans import PlanQuotaChecks  # noqa: F401
from qa.browser.test_voice import VoiceChecks  # noqa: F401
from qa.browser.test_legal import LegalChecks  # noqa: F401
from qa.browser.test_accessibility import AccessibilityChecks  # noqa: F401
