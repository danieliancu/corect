"""Legal identity and retention facts for the legal pages.

The operator is configured in settings (.env); no template repeats a name, an address or a number. Today Corect.uk can
be run by an individual trading as Corect.uk; if a company is formed later, LEGAL_OPERATOR_TYPE=company plus its name
and number change every legal page at once.
"""
from django.conf import settings

from apps.assistant.retention import CLOSED_SESSION_DAYS, NATURALIZE_USAGE_DAYS, SUBMISSION_CLAIM_DAYS

PLACE_NAMES_RO = {"England and Wales": "Anglia și Țara Galilor", "Scotland": "Scoția",
                  "Northern Ireland": "Irlanda de Nord", "United Kingdom": "Regatul Unit"}


def legal_identity():
    name = settings.LEGAL_OPERATOR_NAME
    trading = settings.LEGAL_TRADING_NAME or "Corect.uk"
    jurisdiction = PLACE_NAMES_RO.get(settings.LEGAL_JURISDICTION, settings.LEGAL_JURISDICTION)
    is_company = settings.LEGAL_OPERATOR_TYPE == "company"
    if not name:
        public_statement = legal_statement = ""
    elif is_company:
        registered = f", companie înregistrată în {jurisdiction} cu numărul {settings.COMPANY_NUMBER}" \
            if settings.COMPANY_NUMBER else ""
        public_statement = f"{trading} este operat de {name}{registered}."
        legal_statement = f"{name}, trading as {trading}."
    else:
        public_statement = f"{trading} este un nume comercial operat de {name}."
        legal_statement = f"{name}, trading as {trading}."
    return {
        "operator_name": name, "trading_name": trading, "is_company": is_company,
        "address": settings.LEGAL_SERVICE_ADDRESS, "contact_email": settings.CONTACT_EMAIL,
        "company_number": settings.COMPANY_NUMBER if is_company else "", "vat_number": settings.VAT_NUMBER,
        "jurisdiction": jurisdiction, "country": PLACE_NAMES_RO.get(settings.LEGAL_COUNTRY, settings.LEGAL_COUNTRY),
        "hosting_provider": settings.LEGAL_HOSTING_PROVIDER, "public_statement": public_statement,
        "legal_statement": legal_statement, "contact_available": bool(settings.CONTACT_EMAIL or settings.LEGAL_SERVICE_ADDRESS),
        "terms_version": settings.TERMS_VERSION, "privacy_version": settings.PRIVACY_VERSION,
    }


def retention_facts():
    """Real retention periods from code and settings; anything not decided yet is None and is shown as such."""
    return {"submission_claim_days": SUBMISSION_CLAIM_DAYS, "closed_session_days": CLOSED_SESSION_DAYS,
            "naturalize_usage_days": NATURALIZE_USAGE_DAYS,
            "login_session_days": settings.SESSION_COOKIE_AGE // (24 * 60 * 60),
            "usage_ledger_days": None, "visitor_record_days": None, "server_log_days": None}
