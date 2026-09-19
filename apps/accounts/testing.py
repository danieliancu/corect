"""Test helpers shared by the apps: accounts as the site creates them."""
from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group, User

from apps.core.plans import PRO_GROUP


def verified_user(username, email, password=None):
    """An account whose address was confirmed, as every account signed up through the site ends up."""
    user = User.objects.create_user(username, email, password)
    EmailAddress.objects.create(user=user, email=email.lower(), verified=True, primary=True)
    return user


def verify(user):
    """Confirms an existing test account's address."""
    EmailAddress.objects.update_or_create(user=user, email=user.email.lower(),
                                          defaults={"verified": True, "primary": True})
    return user


def make_pro(user):
    """Pro through the manual override (the staff "Pro" group); paid Pro is tested with a subscription row."""
    user.groups.add(Group.objects.get_or_create(name=PRO_GROUP)[0])
    return user


def confirmation_path(message):
    """The confirmation link's path from a verification email."""
    link = next(line.strip() for line in message.body.splitlines() if "/accounts/confirm-email/" in line)
    return "/" + link.split("://", 1)[1].split("/", 1)[1]
