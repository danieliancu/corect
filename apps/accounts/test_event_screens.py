"""The event screens (apps/core/events.py): a welcome when an account opens, "done" after an account change, a goodbye
after deletion; shown once, over the next page, and closable without JavaScript."""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from .test_auth_flows import FreshRateLimits
from .testing import confirmation_path, verified_user
from .tests import PASSWORD, SIGNUP

SCREEN = '<dialog class="wait-screen event-screen is-{}" open'


class EventScreenTests(FreshRateLimits, TestCase):
    def assertScreen(self, page, kind, *texts):
        self.assertContains(page, SCREEN.format(kind), count=1)
        self.assertContains(page, '<form method="dialog"><button class="button" type="submit" autofocus>')
        for text in texts:
            self.assertContains(page, text)

    def assertNoScreen(self, page):
        self.assertNotContains(page, "event-screen is-")

    def test_confirming_a_new_account_welcomes_it_once(self):
        self.client.post("/accounts/signup/", {**SIGNUP, "accept_legal": "on"})
        self.assertNoScreen(self.client.get("/accounts/confirm-email/"))  # not yet: the account is not open
        home = self.client.post(confirmation_path(mail.outbox[-1]), follow=True)
        self.assertScreen(home, "welcome", "Bun venit, Ana Learner!", "Contul tău e gata.", ">Începe</button>")
        self.assertNotContains(home, 'class="messages"')  # allauth's "confirmed" and "signed in" banners say the same
        self.assertNoScreen(self.client.get("/"))

    def test_confirming_a_changed_address_is_done_not_welcome(self):
        self.client.force_login(verified_user("ana", "ana@example.com"))
        self.client.post("/accounts/email/", {"email": "ana.new@example.com", "action_add": ""})
        page = self.client.post(confirmation_path(mail.outbox[-1]), follow=True)
        self.assertScreen(page, "updated", "Gata!", "Adresa ta de email a fost confirmată.", ">Continuă</button>")
        self.assertNotContains(page, "Bun venit")

    def test_saving_the_profile_or_the_password_is_done(self):
        self.client.force_login(verified_user("ana", "ana@example.com", PASSWORD))
        page = self.client.post("/accounts/profile/", {"action": "profile", "first_name": "Ana Maria"}, follow=True)
        self.assertScreen(page, "updated", "Gata!", "Profilul tău a fost actualizat.")
        new = "Another-unique-pass-2718"
        page = self.client.post("/accounts/profile/", {"action": "password", "old_password": PASSWORD,
                                                       "new_password1": new, "new_password2": new}, follow=True)
        self.assertScreen(page, "updated", "Parola ta a fost salvată.")
        self.assertNoScreen(self.client.get("/accounts/profile/"))

    def test_deleting_the_account_says_goodbye_after_signing_out(self):
        user = verified_user("ana", "ana@example.com", PASSWORD)
        self.client.force_login(user)
        page = self.client.post("/accounts/profile/", {"action": "delete", "delete-password": PASSWORD}, follow=True)
        self.assertScreen(page, "goodbye", "Rămas bun!", "Contul tău și istoricul au fost șterse.")
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_other_messages_stay_banners(self):
        page = self.client.post("/accounts/verification/resend/", {"email": "nobody@example.com"}, follow=True)
        self.assertContains(page, '<div class="messages"><p role="status">Dacă adresa aparține')
        self.assertNoScreen(page)

