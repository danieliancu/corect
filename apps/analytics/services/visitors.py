"""First-party anonymous visitor identity for product analytics. Rate limiting keeps its own IP-based actor key."""
import logging
import uuid

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from apps.analytics.models import AnonymousVisitor

VISITOR_COOKIE = "corect_visitor_id"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
logger = logging.getLogger("apps.analytics")


def _cookie_id(request):
    try:
        return uuid.UUID(request.COOKIES.get(VISITOR_COOKIE, ""))
    except (AttributeError, ValueError):
        return None


def existing_visitor(request):
    """The visitor already identified by this browser's cookie, without creating one."""
    visitor_id = _cookie_id(request) if settings.ANALYTICS_VISITOR_COOKIE else None
    if visitor_id is None:
        return None
    try:
        return AnonymousVisitor.objects.filter(pk=visitor_id).first()
    except DatabaseError:
        logger.error("visitor_lookup_unavailable")
        return None


def get_or_create_visitor(request):
    """For anonymous submissions. An unknown cookie value is never adopted: a new random ID replaces it."""
    if not settings.ANALYTICS_VISITOR_COOKIE:
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
        logger.error("visitor_lookup_unavailable")
        return None


def attach_visitor_cookie(response, visitor):
    if visitor is not None and settings.ANALYTICS_VISITOR_COOKIE:
        response.set_cookie(VISITOR_COOKIE, str(visitor.pk), max_age=VISITOR_COOKIE_MAX_AGE, httponly=True,
                            samesite="Lax", secure=settings.SESSION_COOKIE_SECURE)
    return response


def link_visitor(request, user, via):
    """Records that this browser's anonymous visitor became `user`. The first conversion wins; nothing is copied."""
    visitor_id = _cookie_id(request) if settings.ANALYTICS_VISITOR_COOKIE and request is not None else None
    if visitor_id is None:
        return 0
    try:
        return AnonymousVisitor.objects.filter(pk=visitor_id, converted_user__isnull=True).update(
            converted_user=user, converted_at=timezone.now(), converted_via=via)
    except DatabaseError:
        logger.error("visitor_link_unavailable")
        return 0
