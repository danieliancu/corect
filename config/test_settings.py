from .settings import *  # noqa: F403

DEBUG = False
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Persistent connections from threaded tests otherwise keep the test database busy at teardown.
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
# Prevent real credentials being used by tests, even if a developer configured them.
OPENAI_API_KEY = "test-not-a-real-key"
OPENAI_MODEL = OPENAI_LEARNING_MODEL = "test-model"
# A fixed display rate, so expected pound amounts never depend on a developer's .env.
ANALYTICS_GBP_PER_USD = Decimal("0.74")  # noqa: F405
# Tests run with DEBUG off, where the legal identity check requires these; fixed, so nothing depends on a .env.
LEGAL_OPERATOR_TYPE = "sole_trader"
LEGAL_OPERATOR_NAME = "Test Operator"
LEGAL_SERVICE_ADDRESS = "1 Test Street, London"
CONTACT_EMAIL = "contact@example.com"
COMPANY_NUMBER = VAT_NUMBER = LEGAL_HOSTING_PROVIDER = ""
SITE_URL = "https://corect.uk"  # Required with DEBUG off; fixed so canonical URLs never depend on a .env.
PRO_DISPLAY_PRICE, PRO_PROMO_ENABLED, PRO_PROMO_PRICE = "£9.99", True, "£4.99"
# Moderation calls OpenAI; tests that exercise it enable it and mock the client (apps/assistant/tests/test_guardrails.py).
CONTENT_MODERATION_ENABLED = False
