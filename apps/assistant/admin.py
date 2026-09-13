from django.contrib import admin
from .models import AssistantRequest, GrammarCorrection


class CorrectionInline(admin.TabularInline):
    model = GrammarCorrection
    extra = 0


@admin.register(AssistantRequest)
class AssistantRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "request_type", "status", "model_used", "created_at"]
    list_filter = ["request_type", "status", "created_at", "user", "model_used"]
    search_fields = ["user__username"]
    readonly_fields = ["created_at"]
    inlines = [CorrectionInline]


@admin.register(GrammarCorrection)
class GrammarCorrectionAdmin(admin.ModelAdmin):
    list_display = ["id", "request", "category", "is_british_preference", "created_at"]
    list_filter = ["category", "created_at", "is_british_preference", "request__user"]
