from django.conf import settings
from django.db import models


class LegalAcceptance(models.Model):
    """A signed-in user's acceptance of one version of the Terms and the Privacy notice. When either version changes,
    users without a row for the new pair are asked again; nobody is assumed to have accepted a version they never saw."""

    class Source(models.TextChoices):
        SIGNUP = "signup", "Signup"
        VISIT = "visit", "Visit (first time or new version)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="legal_acceptances", on_delete=models.CASCADE)
    terms_version = models.CharField(max_length=20)
    privacy_version = models.CharField(max_length=20)
    source = models.CharField(max_length=10, choices=Source.choices)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-accepted_at"]
        constraints = [models.UniqueConstraint(fields=["user", "terms_version", "privacy_version"],
                                               name="unique_legal_acceptance_per_version")]

    def __str__(self):
        return f"{self.user} accepted Terms {self.terms_version} / Privacy {self.privacy_version}"
