"""First-party anonymous visitor identity for product analytics, used only after the visitor opts in
(apps/core/consent.py). Rate limiting keeps its own IP-based actor key."""
import logging
import uuid

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from apps.analytics.models import AnonymousVisitor
from apps.core.consent import analytics_allowed
from apps.core.monitoring import DATABASE, log_event

VISITOR_COOKIE = "corect_visitor_id"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
logger = logging.getLogger("apps.analytics")


def cookie_visitor_id(request):
    try:
        return uuid.UUID(request.COOKIES.get(VISITOR_COOKIE, ""))
    except (AttributeError, ValueError):
        return None


def existing_visitor(request):
    """The visitor already identified by this browser's cookie, without creating one."""
    visitor_id = cookie_visitor_id(request) if analytics_allowed(request) else None
    if visitor_id is None:
        return None
    try:
        return AnonymousVisitor.objects.filter(pk=visitor_id).first()
    except DatabaseError:
        log_event(logger, logging.ERROR, "visitor_lookup_unavailable", DATABASE)
        return None


def get_or_create_visitor(request):
    """For anonymous submissions. An unknown cookie value is never adopted: a new random ID replaces it."""
    if not analytics_allowed(request):
        return None
    now = timezone.now()
    try:
        visitor = existing_visitor(request)
        if visitor is not None:
            AnonymousVisitor.objects.filter(pk=visitor.pk).update(last_seen_at=now)
            visitor.last_seen_at = now
            return visitor
        return AnonymousVisitor.objects.create(first_seen_at=now, last_seen_at=now)
    except DatabaseError:
        log_event(logger, logging.ERROR, "visitor_lookup_unavailable", DATABASE)
        return None


def attach_visitor_cookie(request, response, visitor):
    if visitor is not None and analytics_allowed(request):
        response.set_cookie(VISITOR_COOKIE, str(visitor.pk), max_age=VISITOR_COOKIE_MAX_AGE, httponly=True,
                            samesite="Lax", secure=settings.SESSION_COOKIE_SECURE)
    return response


def delete_visitor_cookie(response):
    """Removes the visitor ID from the browser (analytics withdrawn or account deleted). It is never regenerated."""
    response.delete_cookie(VISITOR_COOKIE, samesite="Lax")
    return response


def link_visitor(request, user, via):
    """Records that this browser's anonymous visitor became `user`. The first conversion wins; nothing is copied."""
    visitor_id = cookie_visitor_id(request) if analytics_allowed(request) else None
    if visitor_id is None:
        return 0
    try:
        return AnonymousVisitor.objects.filter(pk=visitor_id, converted_user__isnull=True).update(
            converted_user=user, converted_at=timezone.now(), converted_via=via)
    except DatabaseError:
        log_event(logger, logging.ERROR, "visitor_link_unavailable", DATABASE)
        return 0
