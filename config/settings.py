import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from apps.assistant.services.pricing import parse_audio_pricing, parse_pricing

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY or (not DEBUG and SECRET_KEY == "replace-with-a-long-random-secret"):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a random secret.")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1",).split(",")
# Full origins such as https://example.ngrok-free.dev, needed when HTTPS ends at a tunnel or proxy in front of Django.
CSRF_TRUSTED_ORIGINS = [origin for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.core", "apps.accounts", "apps.assistant", "apps.learning", "apps.analytics",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "apps.core.middleware.AdminEnglishMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"],
              "APP_DIRS": True, "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages", "apps.core.context_processors.site"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.getenv("DATABASE_NAME", "englishcoach"),
    "USER": os.getenv("DATABASE_USER", "englishcoach"), "PASSWORD": os.getenv("DATABASE_PASSWORD", ""),
    "HOST": os.getenv("DATABASE_HOST", "127.0.0.1"), "PORT": os.getenv("DATABASE_PORT", "5434"), "CONN_MAX_AGE": 60}}
AUTH_PASSWORD_VALIDATORS = [{"NAME": f"django.contrib.auth.password_validation.{name}"} for name in
    ("UserAttributeSimilarityValidator", "MinimumLengthValidator", "CommonPasswordValidator", "NumericPasswordValidator")]
LANGUAGE_CODE = "ro"  # The public site is Romanian; AdminEnglishMiddleware keeps /admin/ in English.
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", "apps.accounts.backends.EmailBackend"]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "60"))
# The OpenAI client is shared by the whole process (apps/assistant/services/provider.py). Idle provider connections are
# kept this long so the next request skips DNS, TCP and TLS set-up; httpx's own default is 5 seconds.
OPENAI_KEEPALIVE_SECONDS = float(os.getenv("OPENAI_KEEPALIVE_SECONDS", "20"))
# Optional reasoning effort for the Naturalise call; empty sends nothing and keeps the model's own default.
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "").strip().lower()
if OPENAI_REASONING_EFFORT not in ("", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
    raise ImproperlyConfigured("OPENAI_REASONING_EFFORT must be empty or none, minimal, low, medium, high, xhigh or max.")
# USD per 1M tokens, OpenAI standard tier, from https://developers.openai.com/api/docs/pricing (checked 13 September 2026).
# OPENAI_PRICING (JSON) replaces this table entirely; "{}" disables cost estimates while tokens are still recorded.
DEFAULT_OPENAI_PRICING = {"gpt-5.6-luna": {"input_per_1m": "0.20", "cached_input_per_1m": "0.02", "output_per_1m": "1.20"}}
try:
    OPENAI_PRICING = parse_pricing(json.loads(os.environ["OPENAI_PRICING"]) if os.getenv("OPENAI_PRICING")
                                   else DEFAULT_OPENAI_PRICING)
except ValueError as exc:
    raise ImproperlyConfigured(f"OPENAI_PRICING is invalid: {exc}") from None
# First-party random visitor ID cookie for anonymous usage analytics; never an IP address or submitted text.
ANALYTICS_VISITOR_COOKIE = os.getenv("ANALYTICS_VISITOR_COOKIE", "true").lower() == "true"
# Voice input is transcribed live in the browser over WebRTC with a short-lived OpenAI credential minted here; a finished
# recording transcribed server-side is the fallback (and the only path when VOICE_REALTIME_ENABLED is false).
# British English speech is generated only on request.
VOICE_REALTIME_ENABLED = os.getenv("VOICE_REALTIME_ENABLED", "true").lower() == "true"
OPENAI_LIVE_TRANSCRIBE_MODEL = os.getenv("OPENAI_LIVE_TRANSCRIBE_MODEL", "gpt-live-transcribe")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "cedar")
try:
    OPENAI_TTS_SPEED = float(os.getenv("OPENAI_TTS_SPEED", "1.0"))
except ValueError:
    OPENAI_TTS_SPEED = -1.0
if not 0.25 <= OPENAI_TTS_SPEED <= 4.0:
    raise ImproperlyConfigured("OPENAI_TTS_SPEED must be a number from 0.25 to 4.0.")
# USD, OpenAI standard tier, from https://developers.openai.com/api/docs/pricing (checked 13 September 2026):
# gpt-live-transcribe and gpt-transcribe per audio minute (realtime is about 3.78x the file rate);
# gpt-4o-mini-tts per 1M text input tokens and 1M audio output tokens.
# OPENAI_AUDIO_PRICING (JSON) replaces this table; "{}" leaves audio costs unknown while usage is still recorded.
DEFAULT_OPENAI_AUDIO_PRICING = {"gpt-live-transcribe": {"per_minute": "0.017"},
                                "gpt-transcribe": {"per_minute": "0.0045"},
                                "gpt-4o-mini-tts": {"input_per_1m": "0.60", "output_per_1m": "12.00"}}
try:
    OPENAI_AUDIO_PRICING = parse_audio_pricing(json.loads(os.environ["OPENAI_AUDIO_PRICING"])
                                               if os.getenv("OPENAI_AUDIO_PRICING") else DEFAULT_OPENAI_AUDIO_PRICING)
except ValueError as exc:
    raise ImproperlyConfigured(f"OPENAI_AUDIO_PRICING is invalid: {exc}") from None
# Staff analytics show costs in pounds. The ledgers stay in USD, the currency OpenAI bills in; this rate only converts
# amounts for display. Default 1 USD = 0.74 GBP (GBP/USD 1.3510 on 11 September 2026); keep it in step with your bank rate.
from decimal import Decimal, InvalidOperation  # noqa: E402
try:
    ANALYTICS_GBP_PER_USD = Decimal(os.getenv("ANALYTICS_GBP_PER_USD", "0.74"))
except InvalidOperation:
    ANALYTICS_GBP_PER_USD = Decimal(-1)
if not ANALYTICS_GBP_PER_USD.is_finite() or ANALYTICS_GBP_PER_USD <= 0:
    raise ImproperlyConfigured("ANALYTICS_GBP_PER_USD must be a positive number.")
from config.env_settings import int_setting as _int_setting, naturalize_limits, tier_limits  # noqa: E402


# Live transcription timing. The delay is how long the provider waits before emitting words ("" leaves it out).
# When the learner presses Stop, the microphone keeps sending for VOICE_TRAILING_AUDIO_MS so the last word is not cut,
# then the audio is committed; the final transcript event ends the wait at once, VOICE_FINAL_TRANSCRIPT_MS is only a
# safety limit. Values chosen with `manage.py benchmark_voice_latency` (README, Latency): 200 ms was the smallest trailing
# window that kept the last spoken word in 20 of 20 English and 20 of 20 Romanian runs (0 ms lost it once).
VOICE_REALTIME_DELAY = os.getenv("VOICE_REALTIME_DELAY", "low").strip().lower()
if VOICE_REALTIME_DELAY not in ("", "minimal", "low", "medium", "high", "xhigh"):
    raise ImproperlyConfigured("VOICE_REALTIME_DELAY must be empty or minimal, low, medium, high or xhigh.")
VOICE_TRAILING_AUDIO_MS = _int_setting("VOICE_TRAILING_AUDIO_MS", 200, 0, 1500)
VOICE_FINAL_TRANSCRIPT_MS = _int_setting("VOICE_FINAL_TRANSCRIPT_MS", 2500, 500, 4000)
VOICE_MAX_SECONDS = int(os.getenv("VOICE_MAX_SECONDS", "60"))
VOICE_MAX_BYTES = int(os.getenv("VOICE_MAX_BYTES", "5000000"))
VOICE_TRANSCRIBE_LANGUAGES = [code.strip() for code in os.getenv("VOICE_TRANSCRIBE_LANGUAGES", "en,ro").split(",")
                              if code.strip()]
VOICE_SPEECH_TOKEN_MAX_AGE = int(os.getenv("VOICE_SPEECH_TOKEN_MAX_AGE", "2700"))
ASSISTANT_MAX_CHARACTERS = int(os.getenv("ASSISTANT_MAX_CHARACTERS", "2000"))

# PRODUCT QUOTA: successful „Vreau să sune natural!” uses per London calendar day, by plan tier (apps/core/plans.py).
# Typed and spoken text count the same; the microphone and British speech never use it (apps/assistant/services/quota.py).
NATURALIZE_DAILY_LIMITS = naturalize_limits()  # 5, 20 and 200 by default; Pro's is its Fair Use ceiling.

# RATE LIMITS / ABUSE GUARDRAILS: technical protection against bursts, bots and provider-cost attacks, never the plan.
# RATE_LIMIT_MINUTE is still read as the old name of NATURALIZE_RATE_LIMIT_MINUTE; RATE_LIMIT_DAY, VOICE_TRANSCRIBE_LIMIT_DAY
# and VOICE_TTS_LIMIT_DAY are no longer used (system check core.W001 warns when they are set).
NATURALIZE_RATE_LIMIT_MINUTE = _int_setting("NATURALIZE_RATE_LIMIT_MINUTE", os.getenv("RATE_LIMIT_MINUTE", "10"), 1, 1000)
VOICE_TRANSCRIBE_LIMIT_MINUTE = _int_setting("VOICE_TRANSCRIBE_LIMIT_MINUTE", 5, 1, 1000)
VOICE_TTS_LIMIT_MINUTE = _int_setting("VOICE_TTS_LIMIT_MINUTE", 10, 1, 1000)
# Per London calendar day, by tier. More generous than the product quota, so voice never blocks legitimate use within a
# plan (Pro: about two live sessions and three British speech plays per naturalisation at 200 a day).
VOICE_DAILY_GUARDRAILS = {
    "transcription": tier_limits("VOICE_TRANSCRIBE_DAY_LIMITS", (20, 80, 400)),
    "speech": tier_limits("VOICE_TTS_DAY_LIMITS", (30, 120, 600)),
}
# TEMPORARY: a Free account gets exactly what Pro gets — the same daily quota, the same voice guardrails and every Pro
# feature (apps/core/entitlements.py). Anonymous visitors are unchanged. Set FREE_HAS_PRO_FEATURES=false to end it; the
# quota, the entitlements and the plan cards all read this one flag, so nothing else has to be undone.
FREE_HAS_PRO_FEATURES = os.getenv("FREE_HAS_PRO_FEATURES", "true").lower() == "true"
if FREE_HAS_PRO_FEATURES:
    NATURALIZE_DAILY_LIMITS["free"] = NATURALIZE_DAILY_LIMITS["pro"]
    for _guardrail in VOICE_DAILY_GUARDRAILS.values():
        _guardrail["free"] = _guardrail["pro"]
# Abuse guardrails. Text sent to "Vreau să sune natural!" is checked with OpenAI's free moderation endpoint, in parallel
# with the model call so it adds no waiting time; flagged text is refused, and the check fails closed when it cannot run.
CONTENT_MODERATION_ENABLED = os.getenv("CONTENT_MODERATION_ENABLED", "true").lower() == "true"
# Learning engine (apps/learning/services). Python decides what to practise; AI only creates or evaluates content, which
# is stored and reused. Every learning AI call is recorded in analytics.LearningUsageEvent.
OPENAI_LEARNING_MODEL = os.getenv("OPENAI_LEARNING_MODEL", "").strip() or OPENAI_MODEL
LEARNING_REUSE_THRESHOLD = int(os.getenv("LEARNING_REUSE_THRESHOLD", "7"))  # Unused stored exercises that avoid AI.
LEARNING_BATCH_SIZE = int(os.getenv("LEARNING_BATCH_SIZE", "8"))  # Exercises per generation call.
# Pro features stay open to every signed-in user until billing exists (apps/core/entitlements.py). While
# FREE_HAS_PRO_FEATURES is on they stay open whatever this is set to, so the two flags cannot contradict each other.
PRO_ENTITLEMENTS_ENFORCED = (os.getenv("PRO_ENTITLEMENTS_ENFORCED", "false").lower() == "true"
                             and not FREE_HAS_PRO_FEATURES)
OPENAI_MODERATION_MODEL = os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest")
# Public contact address; the Contact page and footer link appear only when it is set.
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
# Pro prices shown on the homepage plans. Display only: payments are not implemented yet (NATURALIZE_DAILY_LIMITS is enforced).
# With the promotion on, the normal price is shown struck through beside the promotional monthly price.
PRO_DISPLAY_PRICE = os.getenv("PRO_DISPLAY_PRICE", "£9.99").strip()
PRO_PROMO_ENABLED = os.getenv("PRO_PROMO_ENABLED", "true").lower() == "true"
PRO_PROMO_PRICE = os.getenv("PRO_PROMO_PRICE", "£4.99").strip()
# Legal identity used by the Terms, the Privacy notice, Contact and the footer (apps/core/legal.py). Leave anything you
# do not have empty: nothing is invented. With DEBUG off, the operator name, service address and CONTACT_EMAIL are
# required (apps/core/checks.py).
LEGAL_OPERATOR_TYPE = os.getenv("LEGAL_OPERATOR_TYPE", "sole_trader").strip()  # sole_trader or company
LEGAL_OPERATOR_NAME = os.getenv("LEGAL_OPERATOR_NAME", "").strip()
LEGAL_TRADING_NAME = os.getenv("LEGAL_TRADING_NAME", "Corect.uk").strip()
LEGAL_SERVICE_ADDRESS = os.getenv("LEGAL_SERVICE_ADDRESS", "").strip()
LEGAL_JURISDICTION = os.getenv("LEGAL_JURISDICTION", "England and Wales").strip()
LEGAL_COUNTRY = os.getenv("LEGAL_COUNTRY", "United Kingdom").strip()
COMPANY_NUMBER = os.getenv("COMPANY_NUMBER", "").strip()
VAT_NUMBER = os.getenv("VAT_NUMBER", "").strip()
LEGAL_HOSTING_PROVIDER = os.getenv("LEGAL_HOSTING_PROVIDER", "").strip()
# Versions of the Terms and the Privacy notice. Change them with any material change to templates/core/terms.html or
# privacy.html: everyone (including signed-in users) is then asked to accept the new version.
TERMS_VERSION = "2026-09-17"  # Pro: unlimited requests under Fair Use, with a stated technical ceiling (200 a day).
PRIVACY_VERSION = "2026-09-17"  # Google Fonts on the homepage; Mod Politicos in usage statistics.
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
LOGGING = {"version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps.assistant": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "openai": {"level": "CRITICAL"}, "httpx": {"level": "CRITICAL"}}}
