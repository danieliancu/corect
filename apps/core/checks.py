from django.conf import settings
from django.core.checks import Error, register

REQUIRED_LEGAL_SETTINGS = ("LEGAL_OPERATOR_NAME", "LEGAL_SERVICE_ADDRESS", "CONTACT_EMAIL")


@register()
def legal_identity_configured(app_configs, **kwargs):
    """With DEBUG off, refuse to run with blank legal identity: the Terms, Privacy notice and Contact page name them."""
    if settings.DEBUG:
        return []
    return [Error(f"{name} is empty.", id=f"core.E00{number}",
                  hint="Set it in the environment before running with DJANGO_DEBUG=false; see LEGAL_LAUNCH_CHECKLIST.md.")
            for number, name in enumerate(REQUIRED_LEGAL_SETTINGS, start=1) if not str(getattr(settings, name, "")).strip()]
