from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")


class LoginForm(AuthenticationForm):
    error_messages = {**AuthenticationForm.error_messages,
        "invalid_login": "Please enter a correct username or email and password. Both fields may be case-sensitive."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Username or email"


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "email")
