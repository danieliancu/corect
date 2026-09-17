import json
import re
from xml.etree import ElementTree

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.core.checks import site_url_configured

PUBLIC = ("/", "/about/", "/confidentialitate/", "/termeni/", "/contact/")
PRIVATE_SIGNED_IN = ("/accounts/profile/", "/history/", "/mistakes/", "/mistakes/verb_form/", "/progress/",
                     "/practice/", "/learn/")
PRIVATE_ANONYMOUS = ("/accounts/login/", "/accounts/signup/", "/cookie-uri/")


def head(response):
    html = response.content.decode()
    return html[:html.index("</head>")]


class IndexingTests(TestCase):
    def test_public_pages_are_indexable_with_a_production_canonical(self):
        for path in PUBLIC:
            with self.subTest(path=path):
                response = self.client.get(path + "?utm_source=x", HTTP_HOST="localhost")
                self.assertEqual(response.status_code, 200)
                markup = head(response)
                self.assertIn(f'<link rel="canonical" href="https://corect.uk{path}">', markup)
                self.assertIn(f'<meta property="og:url" content="https://corect.uk{path}">', markup)
                self.assertNotIn("noindex", markup)
                self.assertNotIn("localhost", markup)
                self.assertNotIn("X-Robots-Tag", response.headers)

    def test_private_pages_are_never_indexed(self):
        user = User.objects.create_user("ana", "ana@example.com", "pass")
        self.client.force_login(user)
        for path in PRIVATE_SIGNED_IN:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                self.assertIn('<meta name="robots" content="noindex, nofollow">', head(response))
                self.assertNotIn('rel="canonical"', head(response))
        self.client.logout()
        for path in PRIVATE_ANONYMOUS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                self.assertIn('<meta name="robots" content="noindex, nofollow">', head(response))

    def test_redirects_errors_endpoints_and_admin_carry_the_header(self):
        for path in ("/history/", "/admin/", "/admin/analytics/", "/does-not-exist/", "/healthz", "/despre/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path)["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(self.client.post("/naturalize/", {})["X-Robots-Tag"], "noindex, nofollow")

    def test_social_metadata(self):
        markup = head(self.client.get("/about/"))
        for tag in ('<meta property="og:site_name" content="Corect.uk">',
                    '<meta property="og:image" content="https://corect.uk/static/img/og-image.jpg">',
                    '<meta property="og:image:width" content="1200">', '<meta property="og:locale" content="ro_RO">',
                    '<meta name="twitter:card" content="summary_large_image">',
                    '<meta name="twitter:image" content="https://corect.uk/static/img/og-image.jpg">',
                    '<meta name="description" content="Corect.uk pe scurt: scrii',
                    '<link rel="icon" href="/static/img/logo.svg?v=4" type="image/svg+xml">'):
            self.assertIn(tag, markup)
        self.assertEqual(len(re.findall(r'<meta name="description"', markup)), 1)

    @override_settings(SITE_URL="")
    def test_local_development_uses_the_request_origin(self):
        self.assertIn('<link rel="canonical" href="http://testserver/about/">', head(self.client.get("/about/")))


class StructuredDataTests(TestCase):
    def test_homepage_json_ld_is_valid_and_invents_nothing(self):
        markup = head(self.client.get("/"))
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', markup, re.S).group(1)
        data = json.loads(raw)
        self.assertEqual([item["@type"] for item in data], ["WebSite", "Organization"])
        self.assertEqual(data[0]["url"], "https://corect.uk/")
        self.assertEqual(data[1]["email"], "contact@example.com")
        for invented in ("offers", "price", "aggregateRating", "review", "award"):
            self.assertNotIn(invented, raw)
        self.assertNotIn("application/ld+json", head(self.client.get("/about/")))

    @override_settings(LEGAL_TRADING_NAME="</script><script>alert(1)</script>")
    def test_json_ld_cannot_break_out_of_its_script(self):
        markup = head(self.client.get("/"))
        self.assertNotIn("<script>alert(1)", markup)
        self.assertIn("\u003C/script\u003E", markup)


class RobotsAndSitemapTests(TestCase):
    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        body = response.content.decode()
        self.assertIn("User-agent: *\n", body)
        self.assertIn("Disallow: /admin/\n", body)
        self.assertIn("Sitemap: https://corect.uk/sitemap.xml\n", body)
        self.assertNotIn("Disallow: /\n", body)

    def test_sitemap_lists_only_public_absolute_urls(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        tree = ElementTree.fromstring(response.content)
        urls = [node.text for node in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertEqual(urls, [f"https://corect.uk{path}" for path in PUBLIC])
        User.objects.create_user("ana", "ana@example.com", "pass")
        self.assertNotIn(b"ana", self.client.get("/sitemap.xml").content)

    @override_settings(CONTACT_EMAIL="", LEGAL_SERVICE_ADDRESS="")
    def test_disabled_contact_page_is_left_out(self):
        self.assertNotIn(b"/contact/", self.client.get("/sitemap.xml").content)


class SiteUrlCheckTests(TestCase):
    def test_production_requires_a_public_https_origin(self):
        for value in ("", "http://corect.uk", "https://localhost", "https://127.0.0.1:8000", "https://corect.uk/app",
                      "corect.uk"):
            with self.subTest(value=value), override_settings(DEBUG=False, SITE_URL=value):
                self.assertEqual([error.id for error in site_url_configured(None)], ["core.E004"])
        with override_settings(DEBUG=False, SITE_URL="https://corect.uk"):
            self.assertEqual(site_url_configured(None), [])
        with override_settings(DEBUG=True, SITE_URL=""):
            self.assertEqual(site_url_configured(None), [])
