from pathlib import Path

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .consent import acceptance_required, analytics_chosen
from .legal import legal_identity
from .plans import is_pro


def asset_query():
    """"?v=<newest change>" for CSS and JS links in development, so the browser never keeps an old copy (the development
    server sends static files without Cache-Control). Production links already carry content hashes: nothing is added."""
    if not settings.DEBUG:
        return ""
    files = (path for folder in settings.STATICFILES_DIRS for path in Path(folder).glob("*/*.*"))
    return f"?v={max((int(path.stat().st_mtime) for path in files), default=0)}"


def site(request):
    # Lazy values: the lookups run only on pages that use them. `consent` drives the first-visit dialog and the cookie
    # settings form; `legal` is the single source of the operator's identity.
    return {
        "contact_email": settings.CONTACT_EMAIL,
        "asset_query": asset_query(),
        "user_is_pro": SimpleLazyObject(lambda: is_pro(request.user)),
        "legal": SimpleLazyObject(legal_identity),
        "consent": SimpleLazyObject(lambda: {"required": acceptance_required(request),
                                             "analytics": analytics_chosen(request),
                                             "analytics_available": settings.ANALYTICS_VISITOR_COOKIE}),
    }
