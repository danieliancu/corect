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
OPENAI_MODEL = "test-model"
# A fixed display rate, so expected pound amounts never depend on a developer's .env.
ANALYTICS_GBP_PER_USD = Decimal("0.74")  # noqa: F405
