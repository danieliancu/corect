from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group, User

from .models import LegalAcceptance
from .suspension import SUSPENDED_GROUP


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    """Acceptance records are evidence: viewable, never added or edited by hand."""

    list_display = ["user", "terms_version", "privacy_version", "source", "accepted_at"]
    list_filter = ["terms_version", "privacy_version", "source"]
    search_fields = ["user__username", "user__email"]
    date_hierarchy = "accepted_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.action(description="Suspend selected accounts (AI features are refused)")
def suspend_accounts(modeladmin, request, queryset):
    Group.objects.get_or_create(name=SUSPENDED_GROUP)[0].user_set.add(*queryset)
    modeladmin.message_user(request, f"Suspended {queryset.count()} account(s).")


@admin.action(description="Lift the suspension of selected accounts")
def lift_suspension(modeladmin, request, queryset):
    Group.objects.get_or_create(name=SUSPENDED_GROUP)[0].user_set.remove(*queryset)
    modeladmin.message_user(request, f"Lifted the suspension of {queryset.count()} account(s).")


admin.site.unregister(User)


@admin.register(User)
class CorectUserAdmin(UserAdmin):
    actions = [suspend_accounts, lift_suspension]
    list_display = (*UserAdmin.list_display, "suspended")

    @admin.display(boolean=True, description="Suspended")
    def suspended(self, obj):
        return obj.groups.filter(name=SUSPENDED_GROUP).exists()
