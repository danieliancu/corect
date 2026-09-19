import os
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Warning, register

REQUIRED_LEGAL_SETTINGS = ("LEGAL_OPERATOR_NAME", "LEGAL_SERVICE_ADDRESS", "CONTACT_EMAIL")
# Environment variables replaced by the plan quotas and the per-tier voice guardrails (README, Configuration).
REPLACED_LIMIT_SETTINGS = {
    "RATE_LIMIT_MINUTE": "NATURALIZE_RATE_LIMIT_MINUTE",
    "RATE_LIMIT_DAY": "NATURALIZE_ANONYMOUS_LIMIT_DAY, NATURALIZE_FREE_LIMIT_DAY and NATURALIZE_PRO_LIMIT_DAY",
    "VOICE_TRANSCRIBE_LIMIT_DAY": "VOICE_TRANSCRIBE_DAY_LIMITS",
    "VOICE_TTS_LIMIT_DAY": "VOICE_TTS_DAY_LIMITS",
}
# Retired switches from before billing: Free and Pro now follow the subscription (apps/core/plans.py) and are ignored.
RETIRED_PLAN_SETTINGS = ("FREE_HAS_PRO_FEATURES", "PRO_ENTITLEMENTS_ENFORCED")
UNDELIVERED_EMAIL_BACKENDS = ("console", "dummy", "filebased")


@register()
def legal_identity_configured(app_configs, **kwargs):
    """With DEBUG off, refuse to run with blank legal identity: the Terms, Privacy notice and Contact page name them."""
    if settings.DEBUG:
        return []
    return [Error(f"{name} is empty.", id=f"core.E00{number}",
                  hint="Set it in the environment before running with DJANGO_DEBUG=false; see LEGAL_LAUNCH_CHECKLIST.md.")
            for number, name in enumerate(REQUIRED_LEGAL_SETTINGS, start=1) if not str(getattr(settings, name, "")).strip()]


@register()
def replaced_limit_settings(app_configs, **kwargs):
    """Old daily limits are ignored so no second daily ceiling stays active; say so instead of failing to start."""
    return [Warning(f"{name} is deprecated." if name == "RATE_LIMIT_MINUTE" else f"{name} is no longer used.",
                    id="core.W001", hint=f"Use {replacement} instead." + (
                        " It is still read while NATURALIZE_RATE_LIMIT_MINUTE is not set." if name == "RATE_LIMIT_MINUTE" else ""))
            for name, replacement in REPLACED_LIMIT_SETTINGS.items() if name in os.environ] + [
        Warning(f"{name} is no longer used.", id="core.W001",
                hint="Free and Pro follow the Stripe subscription or the manual Pro group; remove the variable.")
        for name in RETIRED_PLAN_SETTINGS if name in os.environ]


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


@register()
def site_url_configured(app_configs, **kwargs):
    """With DEBUG off, canonical URLs, Open Graph and the sitemap must point at the real HTTPS site, never localhost."""
    if settings.DEBUG:
        return []
    value = getattr(settings, "SITE_URL", "")
    parts = urlsplit(value)
    if not value or parts.scheme != "https" or not parts.hostname or parts.hostname in LOCAL_HOSTS             or parts.path not in ("", "/") or parts.query:
        return [Error("SITE_URL must be the public HTTPS origin, e.g. https://corect.uk.", id="core.E004",
                      hint="Set SITE_URL in the environment before running with DJANGO_DEBUG=false (README, SEO).")]
    return []


@register()
def email_delivery_configured(app_configs, **kwargs):
    """With DEBUG off, verification links must really be sent: no console backend, a mail host and a real sender."""
    if settings.DEBUG:
        return []
    backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2] if "." in settings.EMAIL_BACKEND else settings.EMAIL_BACKEND
    problems = []
    if backend in UNDELIVERED_EMAIL_BACKENDS:
        problems.append(f"EMAIL_BACKEND {settings.EMAIL_BACKEND} does not deliver mail.")
    if backend == "smtp" and not settings.EMAIL_HOST:
        problems.append("EMAIL_HOST is empty.")
    if settings.DEFAULT_FROM_EMAIL.rstrip(">").endswith("@localhost"):
        problems.append("DEFAULT_FROM_EMAIL is not set.")
    return [Error(problem, id="core.E005", hint="Configure SMTP in the environment (README, Email); accounts cannot be "
                  "confirmed without it.") for problem in problems]


@register()
def billing_configured(app_configs, **kwargs):
    """Stripe is all or nothing; in production say plainly when Pro cannot be bought or test keys are live."""
    values = {name: getattr(settings, name) for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                                                         "STRIPE_PRO_PRICE_ID")}
    missing = [name for name, value in values.items() if not value]
    if missing and len(missing) < len(values):
        return [Error(f"{', '.join(missing)} missing: billing stays off.", id="core.E006",
                      hint="Set all three Stripe variables, or none (README, Stripe).")]
    if settings.DEBUG:
        return []
    if missing:
        return [Warning("Stripe is not configured: Pro cannot be bought.", id="core.W002",
                        hint="Set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET and STRIPE_PRO_PRICE_ID (README, Stripe).")]
    if values["STRIPE_SECRET_KEY"].startswith("sk_test_"):
        return [Warning("STRIPE_SECRET_KEY is a test-mode key.", id="core.W003",
                        hint="Switch all three Stripe variables to live mode together (README, Stripe).")]
    return []


@register()
def google_login_configured(app_configs, **kwargs):
    if bool(settings.GOOGLE_CLIENT_ID) != bool(settings.GOOGLE_CLIENT_SECRET):
        return [Error("Only one of GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET is set: Google sign-in stays off.",
                      id="core.E007", hint="Set both, or neither (README, Google).")]
    return []
