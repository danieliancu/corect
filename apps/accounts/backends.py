from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User


class EmailBackend(ModelBackend):
    """Signs in with an email address, when it belongs to exactly one account."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or "@" not in username:
            return None
        matches = list(User.objects.filter(email__iexact=username.strip())[:2])
        if len(matches) != 1:
            return None
        user = matches[0]
        return user if user.check_password(password) and self.user_can_authenticate(user) else None
