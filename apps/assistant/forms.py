from django import forms
from django.conf import settings


class AssistantForm(forms.Form):
    text = forms.CharField(strip=False)
    submission_token = forms.UUIDField()

    def clean_text(self):
        # Browsers submit textarea line breaks as CRLF and the model doesn't echo outer whitespace reliably,
        # so normalise both before the text reaches the services.
        text = self.cleaned_data["text"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text.strip():
            raise forms.ValidationError("Write a little text first.")
        if len(text) > settings.ASSISTANT_MAX_CHARACTERS:
            raise forms.ValidationError(f"Please use {settings.ASSISTANT_MAX_CHARACTERS:,} characters or fewer.")
        return text
