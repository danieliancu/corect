from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .consent import acceptance_required, analytics_chosen
from .legal import legal_identity
from .plans import is_pro


def site(request):
    # Lazy values: the lookups run only on pages that use them. `consent` drives the first-visit dialog and the cookie
    # settings form; `legal` is the single source of the operator's identity.
    return {
        "contact_email": settings.CONTACT_EMAIL,
        "user_is_pro": SimpleLazyObject(lambda: is_pro(request.user)),
        "legal": SimpleLazyObject(legal_identity),
        "consent": SimpleLazyObject(lambda: {"required": acceptance_required(request),
                                             "analytics": analytics_chosen(request),
                                             "analytics_available": settings.ANALYTICS_VISITOR_COOKIE}),
    }
