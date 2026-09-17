import uuid

from django.utils import translation

from .monitoring import category_for_exception, request_context, set_error_category


class AdminEnglishMiddleware:
    """The public site is Romanian; the Django admin and staff analytics under /admin/ stay in English."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            with translation.override("en"):
                return self.get_response(request)
        return self.get_response(request)


class RequestContextMiddleware:
    """Gives every request an ID (returned as X-Request-ID) and makes it, the method and the path available to log
    records (apps/core/log_format.py). Query strings are never logged; no database access."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.request_id = uuid.uuid4().hex
        token = request_context.set({"request_id": request_id, "method": request.method, "path": request.path})
        try:
            response = self.get_response(request)
        finally:
            request_context.reset(token)
        response["X-Request-ID"] = request_id
        return response

    def process_exception(self, request, exception):
        # Tagged for error tracking; Django still logs the traceback (django.request) and returns its normal 500.
        set_error_category(category_for_exception(exception))
        return None
