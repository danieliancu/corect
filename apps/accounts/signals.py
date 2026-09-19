"""An address stops being "grandfathered" once it is really proven: by its confirmation link, or by a Google sign-in whose
verified address it is (which also marks it verified, so the account needs no second confirmation)."""
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import GrandfatheredEmail


@receiver(email_confirmed)
def forget_grandfathering(sender, request, email_address, **kwargs):
    GrandfatheredEmail.objects.filter(email_address=email_address).delete()
    if request is not None:
        # No other confirmed address: this link opened the account (a welcome), not an address change ("done").
        request.corect_first_confirmation = not EmailAddress.objects.filter(
            user_id=email_address.user_id, verified=True).exclude(pk=email_address.pk).exists()


@receiver(user_logged_in)
def trust_provider_verified_addresses(sender, request, user, **kwargs):
    sociallogin = kwargs.get("sociallogin")
    if sociallogin is None:
        return
    proven = {address.email.lower() for address in sociallogin.email_addresses if address.verified}
    if not proven:
        return
    addresses = EmailAddress.objects.filter(user=user, email__in=proven)
    addresses.filter(verified=False).update(verified=True)
    GrandfatheredEmail.objects.filter(email_address__in=addresses).delete()
