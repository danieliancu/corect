"""How django-allauth fits Corect: the signup records the Terms/Privacy acceptance, links the visitor and keeps the
analytics choice; mail failures become a message instead of an error; the steps worth marking (welcome, "done") become event screens;
Google never signs in through an address that was
only carried over from before verification existed (GrandfatheredEmail)."""
import logging
from smtplib import SMTPException

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages

from apps.analytics.services.visitors import link_visitor
from apps.core.consent import analytics_chosen, record_acceptance, set_consent_cookie
from apps.core.events import UPDATED, WELCOME, announce
from .models import GrandfatheredEmail, LegalAcceptance

logger = logging.getLogger("apps.accounts")

EMAIL_NOT_SENT = "Nu am putut trimite emailul acum. Încearcă din nou în câteva minute."
WELCOME_TEXT = "Contul tău e gata. Scrie sau spune ceva în română sau engleză și primești engleză britanică naturală."
# allauth's own messages for these steps become event screens (apps/core/events.py); the rest stay banners.
EVENT_MESSAGES = {"email_confirmed.txt": "Adresa ta de email a fost confirmată.",
                  "primary_email_set.txt": "Adresa ta principală de email a fost schimbată.",
                  "password_changed.txt": "Parola ta a fost salvată.", "password_set.txt": "Parola ta a fost salvată."}
LOGIN_MISMATCH = "Adresa de email sau parola nu este corectă. Parola ține cont de literele mari."


def after_signup(request, user):
    record_acceptance(user, LegalAcceptance.Source.SIGNUP)
    link_visitor(request, user, via="signup")
    request.corect_signed_up = True


def with_consent_cookie(request, response):
    """A new account accepted the current Terms and Privacy versions in its form; the browser remembers that too, with
    the analytics choice kept as it was."""
    if getattr(request, "corect_signed_up", False) and response is not None:
        return set_consent_cookie(response, analytics_chosen(request))
    return response


class AccountAdapter(DefaultAccountAdapter):
    error_messages = {**DefaultAccountAdapter.error_messages,
                      "username_password_mismatch": LOGIN_MISMATCH, "email_password_mismatch": LOGIN_MISMATCH}

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=commit)
        if commit:
            after_signup(request, user)
        return user

    def respond_email_verification_sent(self, request, user):
        return with_consent_cookie(request, super().respond_email_verification_sent(request, user))

    def post_login(self, request, user, **kwargs):
        return with_consent_cookie(request, super().post_login(request, user, **kwargs))

    def add_message(self, request, level, message_template=None, message_context=None, extra_tags="", message=None):
        name = (message_template or "").rsplit("/", 1)[-1]
        if name == "email_confirmed.txt" and getattr(request, "corect_first_confirmation", False):
            announce(request, WELCOME, WELCOME_TEXT)
        elif name in EVENT_MESSAGES:
            announce(request, UPDATED, EVENT_MESSAGES[name])
        elif not (name == "logged_in.txt" and getattr(request, "corect_event", None)):  # the event screen says it
            super().add_message(request, level, message_template, message_context, extra_tags, message)

    def send_mail(self, template_prefix, email, context):
        try:
            super().send_mail(template_prefix, email, context)
        except (SMTPException, OSError) as exc:
            # No address in the log: the template name and the error class are enough to diagnose the provider.
            logger.warning("email_send_failed template=%s error=%s", template_prefix.rsplit("/", 1)[-1],
                           type(exc).__name__)
            if self.request is not None:
                messages.error(self.request, EMAIL_NOT_SENT)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_auto_signup_allowed(self, request, sociallogin):
        return False  # the first Google sign-in always shows the signup step with the age, Terms and Privacy checkbox

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        after_signup(request, user)
        if EmailAddress.objects.filter(user=user, verified=True).exists():  # else the welcome waits for the link
            announce(request, WELCOME, WELCOME_TEXT)
        return user

    def can_authenticate_by_email(self, sociallogin, email):
        # Addresses carried over when verification was introduced were never proven: an account that someone else created
        # with this address must not open to (or keep access through) the address's real owner. They connect Google
        # once from the account page, after signing in with the password.
        if GrandfatheredEmail.objects.filter(email_address__email__iexact=email).exists():
            return False
        return super().can_authenticate_by_email(sociallogin, email)
