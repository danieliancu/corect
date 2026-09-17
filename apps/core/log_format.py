"""Production logging: one JSON object per line (LOG_FORMAT=json) with request and category context.

Exception messages are left out of JSON logs: database and provider errors can quote submitted values. The exception
class and the stack frames are enough to find the code that failed.
"""
import json
import logging
import traceback
from datetime import datetime, timezone

from .monitoring import APPLICATION, category_for_exception, request_context

CONTEXT_FIELDS = ("request_id", "method", "path")


class RequestContextFilter(logging.Filter):
    """Adds request_id, method, path and category to every record, so every formatter can rely on them."""

    def filter(self, record):
        context = request_context.get() or {}
        request = getattr(record, "request", None)  # django.request logs 4xx/5xx after the middleware has returned.
        if not context and request is not None:
            context = {"request_id": getattr(request, "request_id", "-"), "method": getattr(request, "method", "-"),
                       "path": getattr(request, "path", "-")}
        for field in CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, context.get(field, "-"))
        if not getattr(record, "category", None):
            exc = record.exc_info[1] if record.exc_info else None
            record.category = category_for_exception(exc) if exc is not None else APPLICATION
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "category": getattr(record, "category", APPLICATION),
        }
        if getattr(record, "event", None):
            entry["event"] = record.event
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, "-")
            if value != "-":
                entry[field] = value
        status = getattr(record, "status_code", None)
        if status is not None:
            entry["status"] = status
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = f"{record.exc_info[0].__module__}.{record.exc_info[0].__qualname__}"
            entry["stack"] = "".join(traceback.format_tb(record.exc_info[2]))
        return json.dumps(entry, ensure_ascii=False)
