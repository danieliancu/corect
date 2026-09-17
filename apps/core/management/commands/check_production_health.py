"""One-shot production check for cron or a hosting monitor: exit 0 when healthy, 1 when something needs attention.

Prints one JSON line (safe to email or forward to a webhook) and logs `monitoring_alert` at ERROR, which Sentry
records when SENTRY_DSN is set. Example cron (every 15 minutes, mail only on problems):
    */15 * * * * cd /srv/corect && .venv/bin/python manage.py check_production_health --quiet
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError
from django.utils import timezone

from apps.core.health import database_ok
from apps.core.monitoring import APPLICATION, DATABASE, PROVIDER, log_event

logger = logging.getLogger("apps.core")


class Command(BaseCommand):
    help = "Checks the database, AI spend and provider error rate against the alert thresholds."
    requires_system_checks = []  # Cron output stays one JSON line; `manage.py check` covers configuration.

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true", help="Print nothing when everything is healthy.")

    def handle(self, *args, quiet=False, **options):
        report, alerts = self.collect(timezone.now())
        report["status"] = "alert" if alerts else "ok"
        report["alerts"] = alerts
        for alert in alerts:
            log_event(logger, logging.ERROR, "monitoring_alert", alert["category"], check=alert["check"])
        if alerts or not quiet:
            self.stdout.write(json.dumps(report, default=str, sort_keys=True))
        if alerts:
            raise SystemExit(1)

    def collect(self, now):
        report, alerts = {"checked_at": now.isoformat(timespec="seconds")}, []
        if not database_ok():
            alerts.append({"check": "database", "category": DATABASE})
            return report, alerts
        from apps.accounts.emails import missing_email_count
        from apps.analytics.monitoring import ai_spend, day_start, provider_error_rate

        try:
            today = ai_spend(day_start(now), now)
            last_hour = ai_spend(now - timedelta(hours=1), now)
            errors = provider_error_rate(now - timedelta(hours=1), now)
            report["accounts_without_email"] = missing_email_count()  # Informational: they are asked on next visit.
        except DatabaseError:
            alerts.append({"check": "database", "category": DATABASE})
            return report, alerts
        report.update({
            "ai_cost_today_usd": str(today.cost), "ai_cost_today_by_ledger_usd": {k: str(v) for k, v in today.by_ledger.items()},
            "ai_cost_last_hour_usd": str(last_hour.cost), "unpriced_calls_today": today.unpriced,
            "ai_requests_last_hour": errors.requests, "provider_failures_last_hour": errors.provider_failures,
        })
        if today.cost > settings.AI_COST_ALERT_DAILY_USD:
            alerts.append({"check": "ai_cost_today", "category": PROVIDER,
                           "value": str(today.cost), "threshold": str(settings.AI_COST_ALERT_DAILY_USD)})
        if last_hour.cost > settings.AI_COST_ALERT_HOURLY_USD:
            alerts.append({"check": "ai_cost_last_hour", "category": PROVIDER,
                           "value": str(last_hour.cost), "threshold": str(settings.AI_COST_ALERT_HOURLY_USD)})
        if errors.requests >= settings.ALERT_MIN_REQUESTS and errors.percent >= settings.ALERT_ERROR_RATE_PERCENT:
            alerts.append({"check": "provider_error_rate", "category": PROVIDER,
                           "value": round(errors.percent, 1), "threshold": settings.ALERT_ERROR_RATE_PERCENT})
        if today.unpriced:
            alerts.append({"check": "unpriced_calls", "category": APPLICATION, "value": today.unpriced})
        return report, alerts
