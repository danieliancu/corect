from django.db import migrations

PRO_GROUP = "Pro"


def create_pro_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name=PRO_GROUP)


def remove_pro_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=PRO_GROUP).delete()


class Migration(migrations.Migration):
    """Group staff add users to, in /admin/, to show them the Pro view of the homepage plans (no billing yet)."""

    dependencies = [("auth", "0012_alter_user_first_name_max_length")]

    operations = [migrations.RunPython(create_pro_group, remove_pro_group)]
