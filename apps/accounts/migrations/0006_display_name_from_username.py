"""Sign-in moves to email only and the site shows User.first_name, a name that need not be unique. Accounts that have no
name yet keep showing what they chose before: their username is copied into it. Names already set are left alone;
running it twice changes nothing; reversing it leaves the names (they are harmless)."""
from django.db import migrations
from django.db.models import F


def name_from_username(apps, schema_editor):
    User = apps.get_model("auth", "User")
    User.objects.filter(first_name="").update(first_name=F("username"))


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_grandfather_existing_addresses"), ("auth", "0012_alter_user_first_name_max_length")]

    operations = [migrations.RunPython(name_from_username, migrations.RunPython.noop)]
