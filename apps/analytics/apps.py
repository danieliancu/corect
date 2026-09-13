from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = "apps.analytics"
    label = "analytics"
    verbose_name = "Usage analytics"

    def ready(self):
        from . import signals  # noqa: F401
