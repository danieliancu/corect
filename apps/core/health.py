"""Health endpoints for uptime monitors and load balancers.

/healthz: the process answers (liveness). No database access, so it stays cheap and never cascades a database outage
into restarts. /readyz: the process can serve requests (readiness): the database answers a trivial query. OpenAI is
deliberately not called here — it would cost money and add latency; provider trouble is detected from the usage
ledgers by `manage.py check_production_health`. Responses reveal only ok/error, never versions or settings.
"""
import logging

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .monitoring import DATABASE, log_event

logger = logging.getLogger("apps.core")


def _response(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def database_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        log_event(logger, logging.ERROR, "readiness_database_unavailable", DATABASE)
        return False


@never_cache
@require_GET
def healthz(request):
    return _response({"status": "ok"})


@never_cache
@require_GET
def readyz(request):
    if database_ok():
        return _response({"status": "ok", "checks": {"database": "ok"}})
    return _response({"status": "unavailable", "checks": {"database": "error"}}, status=503)
