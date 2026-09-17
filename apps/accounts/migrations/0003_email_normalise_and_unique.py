"""Stage 1 of "every account has one valid, unique email address".

1. Preflight: stop if two accounts share an address (ignoring case and spaces). Nothing is changed or guessed: the
   owner decides which account keeps the address (README, Account emails). `manage.py report_email_integrity` lists
   the accounts involved.
2. Store existing addresses trimmed and lower-case.
3. Enforce uniqueness in the database with a partial unique index on LOWER(email), for non-blank addresses. Blank
   addresses of accounts created before the rule are allowed until their owners add one (stage 2, a NOT blank check,
   follows once `report_email_integrity` shows none are left).

On PostgreSQL the whole migration runs in one transaction, so a failed preflight leaves the database unchanged.
"""
from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower, Trim

INDEX = "accounts_user_email_ci_unique"


def preflight(apps, schema_editor):
    User = apps.get_model("auth", "User")
    duplicates = (User.objects.exclude(email="").annotate(address=Lower(Trim("email"))).values("address")
                  .annotate(total=Count("id")).filter(total__gt=1))
    groups = list(duplicates.values_list("address", flat=True))
    if groups:
        ids = sorted(User.objects.annotate(address=Lower(Trim("email"))).filter(address__in=groups)
                     .values_list("pk", flat=True))
        raise RuntimeError(
            f"Cannot make account emails unique: {len(groups)} address(es) are shared by more than one account "
            f"(account IDs {ids}). Resolve them first — run `python manage.py report_email_integrity` and see README, "
            f"Account emails — then run migrate again. No data was changed.")


def normalise(apps, schema_editor):
    User = apps.get_model("auth", "User")
    for pk, email in User.objects.exclude(email="").values_list("pk", "email").iterator():
        if email != email.strip().lower():
            User.objects.filter(pk=pk).update(email=email.strip().lower())


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_suspended_group"), ("auth", "0012_alter_user_first_name_max_length")]

    operations = [
        migrations.RunPython(preflight, migrations.RunPython.noop),
        migrations.RunPython(normalise, migrations.RunPython.noop),
        migrations.RunSQL(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON auth_user (LOWER(email)) WHERE email <> ''",
            f"DROP INDEX IF EXISTS {INDEX}",
        ),
    ]
