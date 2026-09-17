from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Group, User

from .emails import EMAIL_TAKEN, email_taken, normalize_email
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


class StaffEmailMixin:
    """Staff see the same email rules as learners: lower-case and unique ignoring case."""

    email_required = True

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email"))
        if not email and self.email_required:
            raise forms.ValidationError("Every new account needs an email address.", code="required")
        if email and email_taken(email, exclude_pk=self.instance.pk):
            raise forms.ValidationError(EMAIL_TAKEN, code="unique")
        return email


class CorectUserCreationForm(StaffEmailMixin, AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        fields = ("username", "email")


class CorectUserChangeForm(StaffEmailMixin, UserChangeForm):
    # Accounts created before emails were required may stay blank here, so staff can still suspend or edit them;
    # the account holder is asked for an address on their next visit.
    email_required = False


admin.site.unregister(User)


@admin.register(User)
class CorectUserAdmin(UserAdmin):
    add_form = CorectUserCreationForm
    form = CorectUserChangeForm
    add_fieldsets = ((None, {"classes": ("wide",),
                             "fields": ("username", "email", "usable_password", "password1", "password2")}),)
    list_filter = (*UserAdmin.list_filter, ("email", admin.EmptyFieldListFilter))
    actions = [suspend_accounts, lift_suspension]
    list_display = (*UserAdmin.list_display, "suspended")

    @admin.display(boolean=True, description="Suspended")
    def suspended(self, obj):
        return obj.groups.filter(name=SUSPENDED_GROUP).exists()
