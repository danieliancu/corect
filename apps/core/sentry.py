"""Optional error tracking with Sentry, enabled only when SENTRY_DSN is set (config/settings.py).

Privacy first: no default PII, no request bodies, cookies, headers or query strings, no local variables, and no log
messages as breadcrumbs (they are sanitised codes anyway, but nothing is sent that is not needed to find a bug).
"""
from .monitoring import category_for_exception

SCRUBBED_REQUEST_KEYS = ("data", "cookies", "headers", "query_string", "env")


def scrub_event(event, hint):
    request = event.get("request")
    if isinstance(request, dict):
        for key in SCRUBBED_REQUEST_KEYS:
            request.pop(key, None)
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {key: value for key, value in user.items() if key == "id"}
    for exception in (event.get("exception") or {}).get("values") or []:
        exception.pop("value", None)  # Exception messages can quote submitted values.
        for frame in (exception.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    tags = event.setdefault("tags", {})
    logged_category = (event.get("extra") or {}).get("category")  # From log_event(); a short fixed word.
    exc_info = (hint or {}).get("exc_info")
    if "category" not in tags:
        if logged_category:
            tags["category"] = str(logged_category)
        elif exc_info:
            tags["category"] = category_for_exception(exc_info[1])
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    return event


def init_sentry(dsn, *, environment="", release="", traces_sample_rate=0.0):
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("SENTRY_DSN is set but sentry-sdk is not installed (pip install -r "
                                   "requirements.txt).") from exc
    import logging

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or None,
        release=release or None,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=traces_sample_rate,
        before_send=scrub_event,
        before_breadcrumb=lambda crumb, hint: None,
        integrations=[DjangoIntegration(), LoggingIntegration(level=None, event_level=logging.ERROR)],
    )
