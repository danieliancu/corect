"""Versioned acceptance of the Terms and the Privacy notice, and the separate, optional analytics choice.

Anonymous visitors: one necessary first-party cookie holds the accepted document versions and the analytics choice,
never personal data. Signed-in users: acceptance of the current versions is recorded against the account
(accounts.LegalAcceptance), and that record, not the cookie, decides whether they are asked again.
"""
from django.conf import settings

CONSENT_COOKIE = "corect_consent"
CONSENT_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def current_versions():
    return settings.TERMS_VERSION, settings.PRIVACY_VERSION


def read_consent(request):
    """(terms_version, privacy_version, analytics) from the cookie, or None when it is missing or malformed."""
    parts = request.COOKIES.get(CONSENT_COOKIE, "").split("|")
    if len(parts) != 3 or parts[2] not in ("0", "1"):
        return None
    return parts[0], parts[1], parts[2] == "1"


def analytics_chosen(request):
    """Anonymous analytics is on by default (a product decision, see LEGAL_LAUNCH_CHECKLIST.md) until the visitor
    switches it off in the cookie settings."""
    consent = read_consent(request)
    return True if consent is None else consent[2]


def analytics_allowed(request):
    """The anonymous visitor ID is used unless the visitor switched it off, and never when disabled site-wide."""
    return request is not None and settings.ANALYTICS_VISITOR_COOKIE and analytics_chosen(request)


def has_accepted_current(user):
    from apps.accounts.models import LegalAcceptance

    terms, privacy = current_versions()
    return LegalAcceptance.objects.filter(user=user, terms_version=terms, privacy_version=privacy).exists()


def record_acceptance(user, source):
    from apps.accounts.models import LegalAcceptance

    terms, privacy = current_versions()
    LegalAcceptance.objects.get_or_create(user=user, terms_version=terms, privacy_version=privacy,
                                          defaults={"source": source})


def acceptance_required(request):
    """True until the current Terms and Privacy versions are accepted. A version change asks everyone again."""
    if request.user.is_authenticated:
        return not has_accepted_current(request.user)
    consent = read_consent(request)
    return consent is None or consent[:2] != current_versions()


def consent_cookie_value(analytics):
    terms, privacy = current_versions()
    return f"{terms}|{privacy}|{int(bool(analytics))}"


def set_consent_cookie(response, analytics):
    response.set_cookie(CONSENT_COOKIE, consent_cookie_value(analytics), max_age=CONSENT_COOKIE_MAX_AGE, httponly=True,
                        samesite="Lax", secure=settings.SESSION_COOKIE_SECURE)
    return response
