from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.assistant.models import NaturalizeUsage
from apps.assistant.services.limits import actor_key
from apps.assistant.tests.examples import correction_result, naturalized_english
from apps.core.client_ip import client_ip, parse_ip, rate_limit_identity
from config.env_settings import client_ip_settings

PROXY = "10.0.0.5"
NGINX = override_settings(CLIENT_IP_HEADER="x-forwarded-for", TRUSTED_PROXY_CIDRS=["10.0.0.0/24", "fd00::/8"])
CLOUDFLARE = override_settings(CLIENT_IP_HEADER="cf-connecting-ip", TRUSTED_PROXY_CIDRS=["173.245.48.0/20"])


def request(remote=PROXY, **headers):
    made = RequestFactory().get("/", REMOTE_ADDR=remote, **headers)
    made.user = AnonymousUser()
    return made


class DirectConnectionTests(SimpleTestCase):
    def test_default_uses_remote_addr_and_ignores_every_forwarding_header(self):
        forged = request("198.51.100.7", HTTP_X_FORWARDED_FOR="1.1.1.1", HTTP_CF_CONNECTING_IP="2.2.2.2",
                         HTTP_X_REAL_IP="3.3.3.3")
        self.assertEqual(client_ip(forged), "198.51.100.7")

    def test_ipv4_ipv6_and_mapped_addresses(self):
        self.assertEqual(client_ip(request("203.0.113.9")), "203.0.113.9")
        self.assertEqual(client_ip(request("2001:db8::1")), "2001:db8::1")
        self.assertEqual(client_ip(request("::ffff:203.0.113.9")), "203.0.113.9")
        self.assertEqual(client_ip(request("")), "unknown")
        self.assertEqual(client_ip(request("not-an-ip")), "unknown")

    def test_ports_and_brackets_are_tolerated(self):
        self.assertEqual(str(parse_ip("203.0.113.9:443")), "203.0.113.9")
        self.assertEqual(str(parse_ip("[2001:db8::1]:443")), "2001:db8::1")
        self.assertIsNone(parse_ip("unknown"))


@NGINX
class TrustedProxyTests(SimpleTestCase):
    def test_trusted_proxy_passes_the_visitor_address(self):
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="198.51.100.7")), "198.51.100.7")
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="2001:db8::7")), "2001:db8::7")

    def test_untrusted_connection_cannot_choose_its_address(self):
        self.assertEqual(client_ip(request("198.51.100.99", HTTP_X_FORWARDED_FOR="1.2.3.4")), "198.51.100.99")

    def test_client_prefixed_forged_entries_are_ignored(self):
        # The visitor sent "X-Forwarded-For: 1.2.3.4"; nginx appended the address it really saw.
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.7")), "198.51.100.7")

    def test_chains_of_trusted_proxies_are_skipped(self):
        chain = "198.51.100.7, 10.0.0.9, fd00::2"
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR=chain)), "198.51.100.7")
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="10.0.0.8, 10.0.0.9")), "10.0.0.8")

    def test_malformed_or_missing_header_falls_back_to_the_proxy(self):
        self.assertEqual(client_ip(request()), PROXY)
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="garbage")), PROXY)
        self.assertEqual(client_ip(request(HTTP_X_FORWARDED_FOR="1.2.3.4, garbage, 10.0.0.9")), "10.0.0.9")

    def test_only_the_configured_header_is_read(self):
        self.assertEqual(client_ip(request(HTTP_CF_CONNECTING_IP="1.2.3.4")), PROXY)


@CLOUDFLARE
class CloudflareTests(SimpleTestCase):
    def test_cloudflare_header_is_used_only_from_cloudflare(self):
        self.assertEqual(client_ip(request("173.245.48.10", HTTP_CF_CONNECTING_IP="198.51.100.7")), "198.51.100.7")
        self.assertEqual(client_ip(request("198.51.100.99", HTTP_CF_CONNECTING_IP="1.2.3.4")), "198.51.100.99")

    def test_forged_forwarded_for_is_ignored_even_from_cloudflare(self):
        self.assertEqual(client_ip(request("173.245.48.10", HTTP_CF_CONNECTING_IP="198.51.100.7",
                                           HTTP_X_FORWARDED_FOR="1.2.3.4")), "198.51.100.7")

    def test_invalid_cloudflare_value_falls_back_to_the_connection(self):
        self.assertEqual(client_ip(request("173.245.48.10", HTTP_CF_CONNECTING_IP="nope")), "173.245.48.10")


class ActorKeyTests(SimpleTestCase):
    @NGINX
    def test_visitors_behind_one_proxy_get_different_stable_keys(self):
        first = actor_key(request(HTTP_X_FORWARDED_FOR="198.51.100.7"))
        second = actor_key(request(HTTP_X_FORWARDED_FOR="198.51.100.8"))
        self.assertNotEqual(first, second)
        self.assertEqual(first, actor_key(request(HTTP_X_FORWARDED_FOR="198.51.100.7")))
        self.assertTrue(first.startswith("anon:"))
        self.assertNotIn("198.51.100.7", first)

    def test_a_forged_header_does_not_change_the_key(self):
        plain = actor_key(request("198.51.100.7"))
        self.assertEqual(plain, actor_key(request("198.51.100.7", HTTP_X_FORWARDED_FOR="1.2.3.4",
                                                  HTTP_CF_CONNECTING_IP="5.6.7.8")))

    def test_ipv6_visitors_are_counted_per_64(self):
        self.assertEqual(rate_limit_identity(request("2001:db8:1:2::a")), "2001:db8:1:2::/64")
        self.assertEqual(actor_key(request("2001:db8:1:2::a")), actor_key(request("2001:db8:1:2:ffff::1")))
        self.assertNotEqual(actor_key(request("2001:db8:1:2::a")), actor_key(request("2001:db8:1:3::a")))

    def test_signed_in_users_are_keyed_by_account(self):
        made = request("198.51.100.7")
        made.user = User(pk=42)
        self.assertEqual(actor_key(made), "user:42")


class SettingsValidationTests(SimpleTestCase):
    def test_default_trusts_no_proxy(self):
        self.assertEqual(client_ip_settings({}), ("none", []))

    def test_valid_configuration_is_normalised(self):
        self.assertEqual(client_ip_settings({"CLIENT_IP_HEADER": "X-Forwarded-For",
                                             "TRUSTED_PROXY_CIDRS": "10.0.0.1, 2001:db8::/32"}),
                         ("x-forwarded-for", ["10.0.0.1/32", "2001:db8::/32"]))

    def test_unsafe_or_invalid_configuration_stops_the_app(self):
        for env in ({"CLIENT_IP_HEADER": "x-forwarded-for"},
                    {"CLIENT_IP_HEADER": "forwarded", "TRUSTED_PROXY_CIDRS": "10.0.0.1"},
                    {"TRUSTED_PROXY_CIDRS": "10.0.0.300"},
                    {"CLIENT_IP_HEADER": "x-forwarded-for", "TRUSTED_PROXY_CIDRS": "0.0.0.0/0"},
                    {"CLIENT_IP_HEADER": "cf-connecting-ip", "TRUSTED_PROXY_CIDRS": "::/0"}):
            with self.subTest(env=env), self.assertRaises(ImproperlyConfigured):
                client_ip_settings(env)


@NGINX
@override_settings(NATURALIZE_RATE_LIMIT_MINUTE=1000, NATURALIZE_DAILY_LIMITS={"anonymous": 2, "free": 20, "pro": 200})
class AnonymousQuotaBehindProxyTests(TestCase):
    def setUp(self):
        patch("apps.assistant.views.NaturalizeService.naturalize", return_value=naturalized_english()).start()
        self.addCleanup(patch.stopall)

    def post(self, forwarded):
        return self.client.post("/naturalize/", {"text": correction_result().original_text,
                                                 "submission_token": uuid4()},
                                REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=forwarded)

    def test_each_visitor_has_their_own_quota_and_forging_does_not_reset_it(self):
        for _ in range(2):
            self.assertEqual(self.post("198.51.100.7").status_code, 200)
        self.assertEqual(self.post("198.51.100.7").status_code, 429)
        # A forged left-most entry is ignored: nginx appended the real address.
        self.assertEqual(self.post("9.9.9.9, 198.51.100.7").status_code, 429)
        # Another visitor behind the same proxy is unaffected.
        self.assertEqual(self.post("198.51.100.8").status_code, 200)
        self.assertEqual(NaturalizeUsage.objects.count(), 2)

    def test_untrusted_sender_cannot_escape_its_quota_with_a_header(self):
        for number in range(2):
            response = self.client.post("/naturalize/", {"text": correction_result().original_text,
                                                         "submission_token": uuid4()},
                                        REMOTE_ADDR="198.51.100.50", HTTP_X_FORWARDED_FOR=f"9.9.9.{number}")
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/naturalize/", {"text": correction_result().original_text,
                                                     "submission_token": uuid4()},
                                    REMOTE_ADDR="198.51.100.50", HTTP_X_FORWARDED_FOR="9.9.9.99")
        self.assertEqual(response.status_code, 429)
