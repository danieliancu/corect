from django import forms
from django.conf import settings


EMPTY_TEXT = "Scrie sau spune ceva mai întâi."


class AssistantForm(forms.Form):
    text = forms.CharField(strip=False, error_messages={"required": EMPTY_TEXT})
    submission_token = forms.UUIDField()

    def clean_text(self):
        # Browsers submit textarea line breaks as CRLF and the model doesn't echo outer whitespace reliably,
        # so normalise both before the text reaches the services.
        text = self.cleaned_data["text"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text.strip():
            raise forms.ValidationError(EMPTY_TEXT, code="required")
        if len(text) > settings.ASSISTANT_MAX_CHARACTERS:
            raise forms.ValidationError(f"Folosește cel mult {settings.ASSISTANT_MAX_CHARACTERS} de caractere.")
        return text
