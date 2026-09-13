from django.contrib import admin

from apps.analytics.formatting import format_money, format_number
from .models import AssistantRequest, GrammarCorrection


class ReadOnlyAdminMixin:
    """Saved requests record what happened; staff can inspect or delete them, not rewrite them."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class CorrectionInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = GrammarCorrection
    extra = 0
    can_delete = False
    fields = ["original", "replacement", "category", "severity", "is_british_preference", "explanation_ro"]


@admin.register(AssistantRequest)
class AssistantRequestAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["id", "user", "request_type", "status", "error_code", "model_used", "prompt_version", "tokens",
                    "cost", "created_at"]
    list_filter = ["request_type", "status", "model_used", "prompt_version", "created_at"]
    search_fields = ["user__username", "user__email"]
    date_hierarchy = "created_at"
    list_select_related = ["user", "usage_event"]
    inlines = [CorrectionInline]

    @admin.display(description="Tokens", ordering="usage_event__total_tokens")
    def tokens(self, obj):
        return format_number(getattr(getattr(obj, "usage_event", None), "total_tokens", None))

    @admin.display(description="Estimated cost", ordering="usage_event__estimated_cost")
    def cost(self, obj):
        return format_money(getattr(getattr(obj, "usage_event", None), "estimated_cost", None))


@admin.register(GrammarCorrection)
class GrammarCorrectionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["id", "request", "user", "category", "severity", "is_british_preference", "created_at"]
    list_filter = ["category", "severity", "is_british_preference", "created_at"]
    search_fields = ["request__user__username", "original", "replacement"]
    date_hierarchy = "created_at"
    list_select_related = ["request__user"]

    @admin.display(description="User", ordering="request__user__username")
    def user(self, obj):
        return obj.request.user
