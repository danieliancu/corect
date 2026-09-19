from allauth.account.forms import LoginForm as AllauthLoginForm
from allauth.account.forms import SignupForm as AllauthSignupForm
from allauth.socialaccount.forms import SignupForm as AllauthSocialSignupForm
from django import forms
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils.html import format_html

from .emails import is_email_conflict

LEGAL_REQUIRED = ("Ca să creezi contul, confirmă că ai cel puțin 16 ani și că accepți Termenii și Politica de "
                  "confidențialitate.")
LABELS = {"username": "Nume de utilizator", "email": "Email", "password1": "Parolă", "password2": "Confirmă parola"}


class LegalAcceptanceMixin(forms.Form):
    """The age and Terms/Privacy confirmation every new account gives, with a password or with Google
    (apps/accounts/adapters.py records it as a LegalAcceptance)."""

    accept_legal = forms.BooleanField(required=True, error_messages={"required": LEGAL_REQUIRED})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accept_legal"].label = format_html(
            'Confirm că am cel puțin 16 ani și accept <a href="{}">Termenii</a> și <a href="{}">Politica de '
            "confidențialitate</a>.", reverse("terms"), reverse("privacy"))
        for name, label in LABELS.items():
            if name in self.fields:
                self.fields[name].label = label
                self.fields[name].widget.attrs.pop("placeholder", None)


class SignupForm(LegalAcceptanceMixin, AllauthSignupForm):
    # Honeypot, positioned off screen by CSS: automated signups tend to fill every field.
    leave_empty = forms.CharField(required=False, label="Lasă acest câmp gol", widget=forms.TextInput(
        attrs={"class": "form-trap-input", "tabindex": "-1", "autocomplete": "off"}))

    field_order = ("username", "email", "password1", "password2", "accept_legal", "leave_empty")

    def clean_leave_empty(self):
        if self.cleaned_data["leave_empty"]:
            raise forms.ValidationError("Nu am putut crea contul. Reîncarcă pagina și încearcă din nou.", code="automated")
        return ""

    def try_save(self, request):
        """The account, its address and its acceptance record are saved together. When a concurrent signup took the
        same address first, the database index refuses the second one and it is treated like any existing address."""
        try:
            with transaction.atomic():
                return super().try_save(request)
        except IntegrityError as exc:
            if not is_email_conflict(exc):
                raise
            self.account_already_exists = True
            return super().try_save(request)


class SocialSignupForm(LegalAcceptanceMixin, AllauthSocialSignupForm):
    """The one step of a first Google sign-in: a username and the same confirmation as a normal signup."""

    field_order = ("username", "email", "accept_legal")


class LoginForm(AllauthLoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["login"].label = "Nume de utilizator sau email"
        self.fields["password"].label = "Parolă"
        for field in self.fields.values():
            field.widget.attrs.pop("placeholder", None)


class ProfileForm(forms.ModelForm):
    """The username. The address changes on its own page, where the new one is confirmed before it replaces the old."""

    class Meta:
        model = User
        fields = ("username",)


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(label="Adresa ta de email", max_length=254,
                             widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class DeleteAccountForm(forms.Form):
    password = forms.CharField(label="Parola ta, pentru confirmare", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not user.has_usable_password():
            # Accounts created with Google have no password: they confirm by typing their username instead.
            self.fields["password"] = forms.CharField(label="Scrie numele tău de utilizator, pentru confirmare")

    def clean_password(self):
        value = self.cleaned_data["password"]
        confirmed = (self.user.check_password(value) if self.user.has_usable_password()
                     else value.strip() == self.user.get_username())
        if not confirmed:
            raise forms.ValidationError("Parola nu este corectă." if self.user.has_usable_password()
                                        else "Numele de utilizator nu se potrivește.")
        return value
