from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import format_html

from .emails import EMAIL_REQUIRED, EMAIL_TAKEN, email_taken, normalize_email


class RequiredEmailMixin:
    """A required, normalised (trimmed, lower-case) address that no other account uses, ignoring case."""

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email"))
        if not email:
            raise forms.ValidationError(EMAIL_REQUIRED, code="required")
        if email_taken(email, exclude_pk=self.instance.pk):
            raise forms.ValidationError(EMAIL_TAKEN, code="unique")
        return email


def required_email_field():
    return forms.EmailField(label="Email", max_length=254, error_messages={"required": EMAIL_REQUIRED},
                            widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class SignupForm(RequiredEmailMixin, UserCreationForm):
    email = required_email_field()
    accept_legal = forms.BooleanField(required=True, error_messages={
        "required": "Ca să creezi contul, confirmă că ai cel puțin 16 ani și că accepți Termenii și Politica de "
                    "confidențialitate."})
    # Honeypot, positioned off screen by CSS: automated signups tend to fill every field.
    leave_empty = forms.CharField(required=False, label="Lasă acest câmp gol", widget=forms.TextInput(
        attrs={"class": "form-trap-input", "tabindex": "-1", "autocomplete": "off"}))

    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")

    def clean_leave_empty(self):
        if self.cleaned_data["leave_empty"]:
            raise forms.ValidationError("Nu am putut crea contul. Reîncarcă pagina și încearcă din nou.", code="automated")
        return ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accept_legal"].label = format_html(
            'Confirm că am cel puțin 16 ani și accept <a href="{}">Termenii</a> și <a href="{}">Politica de '
            "confidențialitate</a>.", reverse("terms"), reverse("privacy"))


class LoginForm(AuthenticationForm):
    error_messages = {**AuthenticationForm.error_messages,
        "invalid_login": "Introdu un nume de utilizator sau email și o parolă corecte. Ambele câmpuri pot ține cont de "
                         "literele mari."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Nume de utilizator sau email"


class ProfileForm(RequiredEmailMixin, forms.ModelForm):
    email = required_email_field()

    class Meta:
        model = User
        fields = ("username", "email")


class DeleteAccountForm(forms.Form):
    password = forms.CharField(label="Parola ta, pentru confirmare", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Parola nu este corectă.")
        return password
