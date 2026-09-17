from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User

from .emails import normalize_email


class EmailBackend(ModelBackend):
    """Signs in with an email address, ignoring case. Addresses are unique (apps/accounts/emails.py), so at most one
    account matches; the check for a second match stays as a guard for data created before the rule."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = normalize_email(username)
        if not email or not password or "@" not in email:
            return None
        matches = list(User.objects.filter(email__iexact=email)[:2])
        if len(matches) != 1:
            User().set_password(password)  # Same work as a real check, so timing does not reveal registered emails.
            return None
        user = matches[0]
        return user if user.check_password(password) and self.user_can_authenticate(user) else None
