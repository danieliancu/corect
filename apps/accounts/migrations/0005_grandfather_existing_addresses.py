"""Email verification starts with this release. Accounts that already have an address keep working: it is recorded as
verified and primary, as if confirmed, and marked "grandfathered" because it was never actually proven (Google does not
sign in through it until it is: apps/accounts/adapters.py). Accounts without an address are untouched and are asked for one
at their next visit (EmailRequiredMiddleware). Nothing is invented; running it twice adds nothing."""
from django.db import migrations

BATCH = 500


def grandfather(apps, schema_editor):
    User = apps.get_model("auth", "User")
    EmailAddress = apps.get_model("account", "EmailAddress")
    GrandfatheredEmail = apps.get_model("accounts", "GrandfatheredEmail")
    covered = set(EmailAddress.objects.values_list("user_id", flat=True))
    pending = [(user_id, email) for user_id, email in User.objects.exclude(email="").values_list("id", "email").iterator()
               if user_id not in covered and email.strip()]
    for start in range(0, len(pending), BATCH):
        addresses = EmailAddress.objects.bulk_create([
            EmailAddress(user_id=user_id, email=email.strip().lower(), verified=True, primary=True)
            for user_id, email in pending[start:start + BATCH]])
        GrandfatheredEmail.objects.bulk_create([GrandfatheredEmail(email_address_id=address.pk) for address in addresses])


def undo(apps, schema_editor):
    EmailAddress = apps.get_model("account", "EmailAddress")
    GrandfatheredEmail = apps.get_model("accounts", "GrandfatheredEmail")
    ids = list(GrandfatheredEmail.objects.values_list("email_address_id", flat=True))
    EmailAddress.objects.filter(pk__in=ids).delete()  # the grandfathering rows go with them (CASCADE)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_grandfathered_email"), ("account", "0009_emailaddress_unique_primary_email")]
    operations = [migrations.RunPython(grandfather, undo)]
