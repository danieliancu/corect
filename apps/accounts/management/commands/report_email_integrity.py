"""Lists what stands between the database and "every account has one unique email address".

Shared addresses (they block migration accounts.0003) are shown masked, with the account IDs involved, so staff can
decide which account keeps the address — nothing is merged or guessed. Exit status 1 when anything is found.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection

from apps.accounts.emails import duplicate_email_groups, mask_email, missing_email_count


class Command(BaseCommand):
    help = "Reports accounts that share an email address (ignoring case) or have none."
    requires_system_checks = []

    def handle(self, *args, **options):
        User = get_user_model()
        if User._meta.db_table not in connection.introspection.table_names():
            self.stderr.write("The accounts table does not exist yet: run migrate first.")
            raise SystemExit(1)
        groups = duplicate_email_groups()
        missing = missing_email_count()
        for ids in groups:
            address = User.objects.filter(pk=ids[0]).values_list("email", flat=True).first()
            self.stdout.write(f"Shared address {mask_email(address)}: account IDs {', '.join(map(str, ids))}")
        self.stdout.write(f"Accounts sharing an address: {sum(map(len, groups))} in {len(groups)} group(s)")
        self.stdout.write(f"Accounts without an address: {missing} (asked for one on their next visit)")
        if groups or missing:
            raise SystemExit(1)
        self.stdout.write("Every account has a unique email address.")
