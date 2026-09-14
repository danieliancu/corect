from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .plans import is_pro


def site(request):
    # The footer shows the Contact link only when a contact address is configured.
    # user_is_pro is lazy: the group lookup runs only on pages that use it (the homepage plans).
    return {"contact_email": settings.CONTACT_EMAIL, "user_is_pro": SimpleLazyObject(lambda: is_pro(request.user))}
