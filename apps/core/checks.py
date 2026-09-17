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
            for name, replacement in REPLACED_LIMIT_SETTINGS.items() if name in os.environ]


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
