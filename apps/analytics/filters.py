from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.db.models import Q
from django.utils import timezone

PERIODS = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days", "all": "All time"}
PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
AUDIENCES = {"all": "All", "registered": "Registered", "anonymous": "Anonymous"}
# Effective operation of a "Vreau să sune natural!" request (apps/assistant/languages.py), not a button.
TYPES = {"all": "All", "correction": "English correction", "translation": "Into British English",
         "unclassified": "Unclassified"}


def day_start(days_back=0):
    """Start of the local day `days_back` days ago."""
    return timezone.make_aware(datetime.combine(timezone.localdate() - timedelta(days=days_back), time.min))


@dataclass(frozen=True)
class ReportFilters:
    period: str = "30d"
    audience: str = "all"
    request_type: str = "all"
    model: str = ""

    @classmethod
    def from_request(cls, request, models=()):
        value = request.GET.get
        return cls(
            period=value("period") if value("period") in PERIODS else "30d",
            audience=value("audience") if value("audience") in AUDIENCES else "all",
            request_type=value("type") if value("type") in TYPES else "all",
            model=value("model") if value("model") in models else "",
        )

    @property
    def start(self):
        days = PERIOD_DAYS.get(self.period)
        return day_start(days - 1) if days else None

    @property
    def period_label(self):
        return PERIODS[self.period]

    def events_q(self, prefix="", include_period=True):
        """Conditions on UsageEvent fields, directly or through a relation such as "usage_events__"."""
        conditions = {}
        if include_period and self.start:
            conditions["created_at__gte"] = self.start
        if self.audience != "all":
            conditions["audience"] = self.audience
        if self.request_type != "all":
            conditions["request_type"] = self.request_type
        if self.model:
            conditions["model"] = self.model
        return Q(**{prefix + key: value for key, value in conditions.items()})

    def audio_q(self, prefix="", include_period=True):
        """Conditions on AudioUsageEvent fields: period, audience and model. Request type applies to text only."""
        return replace(self, request_type="all").events_q(prefix, include_period)

    def querystring(self, **extra):
        params = {"period": self.period, "audience": self.audience, "type": self.request_type, "model": self.model, **extra}
        return urlencode({key: value for key, value in params.items() if value})
