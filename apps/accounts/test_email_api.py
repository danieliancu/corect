"""Account emails through Resend's HTTPS API (django-anymail), for hosts that block outgoing SMTP."""
import json
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .adapters import EMAIL_NOT_SENT
from .test_auth_flows import FreshRateLimits
from .tests import SIGNUP


def api_response(status, body):
    response = requests.Response()
    response.status_code, response._content = status, json.dumps(body).encode()
    response.headers["Content-Type"] = "application/json"
    response.url = "https://api.resend.com/emails"
    return response


@override_settings(EMAIL_BACKEND="anymail.backends.resend.EmailBackend", ANYMAIL={"RESEND_API_KEY": "re_test"},
                   DEFAULT_FROM_EMAIL="Corect.uk <no-reply@corect.uk>")
class ResendDeliveryTests(FreshRateLimits, TestCase):
    def sign_up(self):
        return self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on"}, follow=True)

    def test_the_confirmation_link_is_sent_through_the_api(self):
        with patch("requests.Session.request", return_value=api_response(200, {"id": "email-1"})) as call:
            self.sign_up()
        method, url = call.call_args.kwargs["method"], call.call_args.kwargs["url"]
        sent = json.loads(call.call_args.kwargs["data"])
        self.assertEqual((method.upper(), url), ("POST", "https://api.resend.com/emails"))
        self.assertEqual(call.call_args.kwargs["headers"]["Authorization"], "Bearer re_test")
        self.assertEqual((sent["from"], sent["to"]), ('"Corect.uk" <no-reply@corect.uk>', ["learner@example.com"]))
        self.assertIn("Confirmă adresa de email", sent["subject"])
        self.assertIn("/accounts/confirm-email/", sent["text"])

    def test_a_refused_send_is_a_message_and_a_log_line_not_an_error(self):
        refused = api_response(403, {"name": "validation_error", "message": "The corect.uk domain is not verified."})
        with patch("requests.Session.request", return_value=refused), \
                self.assertLogs("apps.accounts", "WARNING") as logs:
            page = self.sign_up()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, EMAIL_NOT_SENT)
        self.assertEqual(logs.output, ["WARNING:apps.accounts:email_send_failed template=email_confirmation_signup "
                                       "error=AnymailRequestsAPIError status=403"])
        self.assertNotIn("learner@example.com", "".join(logs.output))
        self.assertTrue(User.objects.filter(email="learner@example.com").exists())  # the link can be sent again
