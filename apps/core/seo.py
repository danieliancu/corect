"""Technical SEO: canonical URLs, indexing rules, robots.txt, sitemap.xml and the homepage's structured data.

Indexing is allow-listed: only the public marketing and legal pages below may be indexed. Every other response —
accounts, history, learning, practice, cookie settings, AI endpoints, health checks, admin, errors — carries
`X-Robots-Tag: noindex, nofollow` (RobotsTagMiddleware) and, for HTML pages, a robots meta tag. robots.txt only keeps
crawlers away from endpoints; it is never what protects a private page (login does).
"""
import json

from django.conf import settings
from django.http import HttpResponse
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

SITE_NAME = "Corect.uk"
INDEXABLE_URL_NAMES = ("home", "about", "privacy", "terms", "contact")
ROBOTS_DISALLOW = ("/admin/", "/naturalize/", "/assistant/", "/analytics/")
OG_IMAGE = {"path": "img/og-image.jpg", "width": 1200, "height": 630,
            "alt": "Corect.uk — engleză britanică naturală, cu greșelile explicate în română"}
JSON_ESCAPES = {ord("<"): "\\u003C", ord(">"): "\\u003E", ord("&"): "\\u0026"}


def site_url(request=None) -> str:
    """The public origin, e.g. https://corect.uk. SITE_URL in production (checked at start-up); the request's own
    origin during local development, so tunnels and localhost keep working."""
    if settings.SITE_URL:
        return settings.SITE_URL
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return ""


def absolute(request, path) -> str:
    return site_url(request) + path


def contact_available() -> bool:
    return bool(settings.CONTACT_EMAIL or settings.LEGAL_SERVICE_ADDRESS)


def is_indexable(request) -> bool:
    match = getattr(request, "resolver_match", None)
    return (match is not None and not match.namespace and match.url_name in INDEXABLE_URL_NAMES
            and request.method in ("GET", "HEAD"))


def canonical_url(request) -> str:
    return absolute(request, request.path)


class RobotsTagMiddleware:
    """`X-Robots-Tag: noindex, nofollow` on every response that is not an indexable public page (no database access)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not is_indexable(request) or response.status_code != 200:
            response.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response


def seo_context(request):
    """Template context: canonical URL (indexable pages only), the robots flag and absolute Open Graph image URL."""
    indexable = is_indexable(request)
    return {
        "site_name": SITE_NAME,
        "canonical_url": canonical_url(request) if indexable else "",
        "robots_noindex": not indexable,
        "og_url": canonical_url(request),
        "og_image": {**OG_IMAGE, "url": absolute(request, static(OG_IMAGE["path"]))},
    }


def structured_data(request):
    """JSON-LD for the homepage: the website and its operator, from facts the site already publishes (no prices,
    ratings or reviews)."""
    base = site_url(request)
    data = [
        {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": f"{base}/",
         "inLanguage": "ro"},
        {"@context": "https://schema.org", "@type": "Organization", "name": settings.LEGAL_TRADING_NAME or SITE_NAME,
         "url": f"{base}/", "logo": base + static("img/logo.svg")},
    ]
    if settings.CONTACT_EMAIL:
        data[1]["email"] = settings.CONTACT_EMAIL
    return mark_safe(json.dumps(data, ensure_ascii=False).translate(JSON_ESCAPES))


@require_GET
@cache_control(max_age=3600, public=True)
def robots_txt(request):
    lines = ["User-agent: *", *(f"Disallow: {path}" for path in ROBOTS_DISALLOW), "Allow: /", "",
             f"Sitemap: {absolute(request, reverse('sitemap_xml'))}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def sitemap_urls(request):
    names = [name for name in INDEXABLE_URL_NAMES if name != "contact" or contact_available()]
    return [absolute(request, reverse(name)) for name in names]


@require_GET
@cache_control(max_age=3600, public=True)
def sitemap_xml(request):
    entries = "".join(f"  <url><loc>{escape(url)}</loc></url>\n" for url in sitemap_urls(request))
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + entries + "</urlset>\n")
    return HttpResponse(body, content_type="application/xml; charset=utf-8")
