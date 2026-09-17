"""Account email rules: every account has one valid email address, stored lower-case and unique ignoring case.

The database enforces uniqueness with the partial index accounts_user_email_ci_unique (migration 0003) on LOWER(email)
for non-blank addresses. Accounts created before the rule may still have a blank address; they are asked for one on
their next visit (apps/accounts/middleware.py) and counted by `manage.py report_email_integrity`.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models.functions import Lower, Trim

EMAIL_INDEX = "accounts_user_email_ci_unique"
EMAIL_TAKEN = "Există deja un cont cu această adresă de email."
EMAIL_REQUIRED = "Introdu adresa ta de email. O folosim ca să îți poți recupera contul."


def normalize_email(value) -> str:
    return (value or "").strip().lower()


def email_taken(email, exclude_pk=None) -> bool:
    users = get_user_model().objects.filter(email__iexact=normalize_email(email))
    if exclude_pk is not None:
        users = users.exclude(pk=exclude_pk)
    return users.exists()


def is_email_conflict(exc) -> bool:
    """Whether an IntegrityError came from the email index (a concurrent signup or profile change)."""
    return EMAIL_INDEX in str(exc)


def duplicate_email_groups(user_model=None):
    """Lists of account IDs sharing one address, ignoring case and surrounding spaces. No addresses are returned."""
    users = (user_model or get_user_model()).objects.exclude(email="")
    duplicates = (users.annotate(address=Lower(Trim("email"))).values("address").annotate(total=Count("id"))
                  .filter(total__gt=1).values_list("address", flat=True))
    groups = {}
    for pk, email in users.annotate(address=Lower(Trim("email"))).filter(address__in=list(duplicates)) \
            .order_by("pk").values_list("pk", "address"):
        groups.setdefault(email, []).append(pk)
    return list(groups.values())


def missing_email_count(user_model=None) -> int:
    return (user_model or get_user_model()).objects.filter(email="").count()


def mask_email(email) -> str:
    """"daniel@example.com" -> "d***@example.com", for reports read by staff."""
    local, _, domain = normalize_email(email).partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
