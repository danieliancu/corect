from django.db import migrations

SUSPENDED_GROUP = "Suspended"


def create_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name=SUSPENDED_GROUP)


def remove_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=SUSPENDED_GROUP).delete()


class Migration(migrations.Migration):
    """Group for manually suspended accounts (apps/accounts/suspension.py): AI features are refused for its members."""

    dependencies = [("accounts", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]

    operations = [migrations.RunPython(create_group, remove_group)]
