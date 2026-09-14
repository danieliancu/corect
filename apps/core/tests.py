from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.assistant.tests.examples import correction_result
from apps.core.plans import PRO_GROUP, display_plans

FEATURES = ("Corectare inteligentă", "Versiune nativă", "Traducere RO ↔ EN", "Istoric", "Categorii de greșeli", "Progres",
            "Practice", "Exerciții bazate pe greșelile tale", "Statistici de evoluție")


def section(html, marker, end="</section>"):
    start = html.index(marker)
    return html[start:html.index(end, start)]


class LandingPageTests(TestCase):
    def test_homepage_marketing_sections_for_anonymous_visitors(self):
        html = self.client.get("/").content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('<h1 id="hero-title">Corectează-ți engleza. Vorbește natural.</h1>', html)
        self.assertEqual(html.count('class="feature-card'), 9)
        # Features not included in Free carry a Pro ribbon.
        self.assertEqual(html.count('class="pro-ribbon"'), 5)
        for title in ("Categorii de greșeli", "Progres", "Practice", "Exerciții bazate pe greșelile tale",
                      "Statistici de evoluție"):
            card = html[html.rindex("<li", 0, html.index(f"<h3>{title}</h3>")):html.index(f"<h3>{title}</h3>")]
            self.assertIn('class="feature-card is-pro"', card)
        for title in FEATURES:
            self.assertIn(f"<h3>{title}</h3>", html)
        self.assertNotIn("Pentru administratori", html)
        self.assertNotIn('href="/admin/', html)
        self.assertNotIn('class="benefits"', html)
        for heading in ("Tot ce primești în Corect.uk", "Cum funcționează", "Alege planul potrivit"):
            self.assertIn(f">{heading}</h2>", html)
        self.assertEqual(html.count('class="step-card"'), 4)
        desktop_nav = section(html, 'class="desktop-nav"', "</nav>")
        self.assertIn('<a href="/#plans">Beneficii</a>', desktop_nav)
        self.assertNotIn("Creează cont", desktop_nav)
        self.assertNotIn("Autentificare", desktop_nav)
        self.assertIn('id="plans"', html)
        # The marketing sections are wrapped so CSS can hide them below 1024px.
        self.assertLess(html.index('class="desktop-marketing"'), html.index("features-title"))

    def test_plans_are_presentation_only(self):
        html = self.client.get("/").content.decode()
        plans = section(html, 'class="landing-section landing-plans"')
        self.assertIn(">Free</h3>", plans)
        self.assertIn(">Pro</h3>", plans)
        self.assertIn("<strong>£9.99</strong><span>/ lună</span>", plans)
        self.assertIn('<button type="button" class="button plan-button" disabled aria-describedby="pro-coming-soon">'
                      "Alege Pro</button>", plans)
        self.assertIn("Pro va fi disponibil în curând", plans)
        self.assertIn('<p><a class="plans-start-link" href="#text">Poți începe și fără cont</a>, într-un mod limitat.</p>', plans)
        self.assertIn('<a class="button secondary plan-button" href="/accounts/signup/">Începe gratis</a>', plans)
        self.assertNotIn("checkout", html.lower())
        self.assertNotIn("stripe", html.lower())
        with override_settings(PRO_DISPLAY_PRICE="£12.50"):
            self.assertContains(self.client.get("/"), "<strong>£12.50</strong>")

    def test_cta_has_one_action(self):
        cta = section(self.client.get("/").content.decode(), 'class="landing-cta"')
        self.assertEqual(cta.count("<a "), 1)
        self.assertNotIn("<button", cta)
        self.assertIn('href="/accounts/signup/">Creează cont</a>', cta)
        self.assertNotIn("Vezi prețurile", cta)

    def test_signed_in_visitors_are_not_asked_to_create_an_account(self):
        User.objects.create_user(username="ana", password="test-password")
        self.client.login(username="ana", password="test-password")
        html = self.client.get("/").content.decode()
        cta = section(html, 'class="landing-cta"')
        self.assertNotIn("Creează cont", cta)
        self.assertIn('<a class="button cta-button" href="#text">Începe o corectare</a>', cta)
        self.assertNotIn("Începe gratis", html)
        self.assertIn('href="#text">Mergi la editor</a>', html)
        self.assertIn('<a href="/history/">Istoric</a>', section(html, 'class="site-footer"', "</footer>"))

    def test_pro_members_see_only_their_benefits(self):
        self.assertTrue(Group.objects.filter(name=PRO_GROUP).exists())  # Created by core.0001_pro_group.
        user = User.objects.create_user(username="pro", password="test-password")
        user.groups.add(Group.objects.get(name=PRO_GROUP))
        self.client.force_login(user)
        plans = section(self.client.get("/").content.decode(), 'class="landing-section landing-plans')
        self.assertIn('<h2 id="plans-title">Ești în planul potrivit.</h2>', plans)
        for hidden in ("Alege planul potrivit", "Poți începe și fără cont", 'class="plan-card', "<button", "Corectări limitate",
                       "Alege Pro", "£"):
            self.assertNotIn(hidden, plans)
        pro_features = [label for label, included in display_plans()[1]["features"] if included]
        self.assertEqual(plans.count('<li class="is-included">'), len(pro_features))
        for label in pro_features:
            self.assertIn(f"</span>{label}</li>", plans)

    def test_signed_in_free_users_still_see_the_plans(self):
        user = User.objects.create_user(username="free", password="test-password")
        self.client.force_login(user)
        self.assertContains(self.client.get("/"), '<h2 id="plans-title">Alege planul potrivit</h2>')

    def test_no_javascript_result_page_keeps_the_sections(self):
        with patch("apps.assistant.views.CorrectionService.correct", return_value=correction_result()):
            response = self.client.post("/assistant/correct/", {"text": correction_result().original_text,
                                                                "submission_token": "0b6f3c5e-8c1e-4b8f-9d43-1f0a6c1b2d3e"})
        self.assertContains(response, "<strong>£9.99</strong>")
        self.assertContains(response, 'class="feature-card', count=9)


class FooterAndPageTests(TestCase):
    def test_footer_on_every_page(self):
        year = timezone.localtime().year
        for path in ("/", "/confidentialitate/", "/termeni/", "/accounts/login/", "/accounts/signup/"):
            with self.subTest(path=path):
                footer = section(self.client.get(path).content.decode(), 'class="site-footer"', "</footer>")
                self.assertIn(f"© {year} Corect.uk", footer)
                self.assertIn('href="/confidentialitate/">Confidențialitate</a>', footer)
                self.assertIn('href="/termeni/">Termeni</a>', footer)
                self.assertNotIn("/history/", footer)
                self.assertNotIn('href="#"', footer)

    @override_settings(CONTACT_EMAIL="hello@example.com")
    def test_contact_page_when_configured(self):
        response = self.client.get("/contact/")
        self.assertContains(response, 'href="mailto:hello@example.com"')
        self.assertContains(response, "<h1", count=1)
        self.assertContains(response, 'href="/contact/">Contact</a>')

    @override_settings(CONTACT_EMAIL="")
    def test_contact_is_hidden_without_an_address(self):
        self.assertEqual(self.client.get("/contact/").status_code, 404)
        self.assertNotContains(self.client.get("/confidentialitate/"), 'href="/contact/"')

    def test_privacy_and_terms_pages(self):
        for path, title in (("/confidentialitate/", "Datele tale, pe scurt."), ("/termeni/", "Termeni de utilizare")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, f"<h1>{title}</h1>")
                self.assertContains(response, "<h1", count=1)
        with override_settings(ANALYTICS_VISITOR_COOKIE=False):
            self.assertContains(self.client.get("/confidentialitate/"), "Cookie-ul de statistici pentru vizitatori este dezactivat.")
