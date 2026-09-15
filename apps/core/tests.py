from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.html import escape

from apps.accounts.models import LegalAcceptance
from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.tests.examples import correction_result
from apps.core.checks import legal_identity_configured
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.core.plans import PRO_GROUP, display_plans

FEATURES = {
    "Corectare inteligentă": "Corectează greșelile reale fără să schimbe inutil felul în care te exprimi.",
    "Versiune nativă": "Vezi cum ar formula aceeași idee, natural, un vorbitor din UK.",
    "Traducere RO ↔ EN": "Traduce rapid și clar între română și engleză.",
    "Scrii sau dictezi": "Vorbești natural, iar textul e pregătit instant pentru corectare sau traducere.",
    "Pronunție britanică": "Ascultă corectarea și varianta nativă cu pronunție britanică.",
    "Istoric": "Revii oricând la corectările și traducerile tale.",
    "Categorii de greșeli": "Vezi unde greșești cel mai des.",
    "Progres": "Urmărești cum evoluează engleza ta în timp.",
    "Practice": "Exersezi exact zonele în care ai nevoie de ajutor.",
    "Exerciții bazate pe greșelile tale": "Înveți din greșelile pe care le faci tu, nu din exemple generice.",
    "Statistici de evoluție": "Vezi ce s-a îmbunătățit și ce greșeli încă se repetă.",
    "Explicații în română": "Înțelegi rapid de ce ai greșit și cum poți spune mai natural.",
}
PRO_FEATURES = ("Categorii de greșeli", "Progres", "Practice", "Exerciții bazate pe greșelile tale", "Statistici de evoluție")
FAIR_USE = ('<p class="plan-fair-use">Se aplică politica de utilizare rezonabilă '
            '(<a href="/termeni/#fair-use">Fair Use</a>).</p>')


def section(html, marker, end="</section>"):
    start = html.index(marker)
    return html[start:html.index(end, start)]


class LandingPageTests(TestCase):
    def test_homepage_marketing_sections_for_anonymous_visitors(self):
        html = self.client.get("/").content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('<h1 id="hero-title">Corectează-ți engleza. Vorbește natural.</h1>', html)
        self.assertEqual(html.count('class="feature-card'), 12)
        for title, description in FEATURES.items():
            self.assertIn(f"<h3>{title}</h3><p>{description}</p>", html)
        # Exactly the five features not included in Free carry a Pro ribbon.
        self.assertEqual(html.count('class="pro-ribbon"'), 5)
        for title in FEATURES:
            card = html[html.rindex("<li", 0, html.index(f"<h3>{title}</h3>")):html.index(f"<h3>{title}</h3>")]
            self.assertEqual('class="feature-card is-pro"' in card, title in PRO_FEATURES, title)
        self.assertNotIn("Pentru administratori", html)
        self.assertNotIn('href="/admin/', html)
        for heading in ("Tot ce primești în Corect.uk", "Cum funcționează", "Alege planul potrivit"):
            self.assertIn(f">{heading}</h2>", html)
        self.assertEqual(html.count('class="step-card"'), 4)
        desktop_nav = section(html, 'class="desktop-nav"', "</nav>")
        self.assertIn('<a href="/#plans">Beneficii</a>', desktop_nav)
        self.assertNotIn("Creează cont", desktop_nav)
        # The marketing sections are wrapped so CSS can hide them below 1024px.
        self.assertLess(html.index('class="desktop-marketing"'), html.index("features-title"))
        self.assertNotIn("{#", html)  # No template comment leaks into the page.

    def test_pro_promotional_price_and_fair_use(self):
        plans = section(self.client.get("/").content.decode(), 'class="landing-section landing-plans"')
        self.assertIn(">Free</h3>", plans)
        self.assertIn('<s class="plan-price-original"><span class="sr-only">Preț normal: </span>£9.99</s>'
                      '<strong><span class="sr-only">Preț actual: </span>£4.99</strong><span class="plan-period">/ lună</span>',
                      plans)
        self.assertEqual(plans.count(FAIR_USE), 1)  # Under the Pro features only.
        self.assertLess(plans.index("Statistici de evoluție</li>", plans.index("plan-pro")), plans.index(FAIR_USE))
        for absent in ("Launch", "OFF", "%"):
            self.assertNotIn(absent, plans)
        self.assertIn('<button type="button" class="button plan-button" disabled aria-describedby="pro-coming-soon">'
                      "Alege Pro</button>", plans)
        self.assertIn('<a class="button secondary plan-button" href="/accounts/signup/">Începe gratis</a>', plans)
        self.assertNotIn("checkout", plans.lower())
        with override_settings(PRO_PROMO_ENABLED=False):
            plans = section(self.client.get("/").content.decode(), 'class="landing-section landing-plans"')
        self.assertIn('<strong>£9.99</strong><span class="plan-period">/ lună</span>', plans)
        self.assertNotIn("<s ", plans)
        self.assertNotIn("£4.99", plans)
        with override_settings(PRO_DISPLAY_PRICE="£12.50", PRO_PROMO_PRICE="£6.25"):
            plans = section(self.client.get("/").content.decode(), 'class="landing-section landing-plans"')
        self.assertIn("</span>£12.50</s>", plans)
        self.assertIn("</span>£6.25</strong>", plans)

    def test_terms_have_the_fair_use_section(self):
        response = self.client.get("/termeni/")
        self.assertContains(response, '<h2 id="fair-use">')
        self.assertContains(response, "Nimic din acești Termeni nu limitează drepturile pe care consumatorii le au în mod "
                                      "obligatoriu potrivit legii.")

    def test_cta_has_one_action(self):
        cta = section(self.client.get("/").content.decode(), 'class="landing-cta"')
        self.assertEqual(cta.count("<a "), 1)
        self.assertNotIn("<button", cta)
        self.assertIn('href="/accounts/signup/">Creează cont</a>', cta)

    def test_signed_in_visitors_are_not_asked_to_create_an_account(self):
        User.objects.create_user(username="ana", password="test-password")
        self.client.login(username="ana", password="test-password")
        html = self.client.get("/").content.decode()
        cta = section(html, 'class="landing-cta"')
        self.assertNotIn("Creează cont", cta)
        self.assertIn('<a class="button cta-button" href="#text">Începe o corectare</a>', cta)
        self.assertIn('href="#text">Mergi la editor</a>', html)
        self.assertIn('<a href="/history/">Istoric</a>', section(html, 'class="site-footer"', "</footer>"))

    def test_pro_members_see_only_their_benefits(self):
        self.assertTrue(Group.objects.filter(name=PRO_GROUP).exists())  # Created by core.0001_pro_group.
        user = User.objects.create_user(username="pro", password="test-password")
        user.groups.add(Group.objects.get(name=PRO_GROUP))
        self.client.force_login(user)
        plans = section(self.client.get("/").content.decode(), 'class="landing-section landing-plans')
        self.assertIn('<h2 id="plans-title">Ești în planul potrivit.</h2>', plans)
        for hidden in ("Alege planul potrivit", 'class="plan-card', "<button", "Corectări limitate", "£"):
            self.assertNotIn(hidden, plans)
        pro_features = [label for label, included in display_plans()[1]["features"] if included]
        self.assertEqual(plans.count('<li class="is-included">'), len(pro_features))
        self.assertIn(FAIR_USE, plans)

    def test_no_javascript_result_page_keeps_the_sections(self):
        with patch("apps.assistant.views.CorrectionService.correct", return_value=correction_result()):
            response = self.client.post("/assistant/correct/", {"text": correction_result().original_text,
                                                                "submission_token": uuid4()})
        self.assertContains(response, "</span>£4.99</strong>")
        self.assertContains(response, 'class="feature-card', count=12)


class ConsentTests(TestCase):
    def setUp(self):
        patch("apps.assistant.views.CorrectionService.correct", return_value=correction_result()).start()
        self.addCleanup(patch.stopall)

    def correct(self):
        return self.client.post("/assistant/correct/", {"text": correction_result().original_text,
                                                        "submission_token": uuid4()})

    def html(self, path="/"):
        return self.client.get(path).content.decode()

    def settings_form(self, **data):
        return self.client.post("/cookie-uri/", {"next": "/", **data})

    def acknowledge(self):
        return self.client.post("/cookie-uri/", {"next": "/", "acknowledge": "1"})

    def test_first_visit_shows_a_discreet_notice_that_blocks_nothing(self):
        html = self.html()
        bar = section(html, 'class="consent-bar"')
        self.assertIn('<p>Folosind Corect.uk, accepți <a href="/termeni/">Termenii</a> și confirmi că ai cel puțin 16 ani. '
                      'Vezi cum folosim datele în <a href="/confidentialitate/">Politica de confidențialitate</a>.</p>', bar)
        self.assertIn('<button class="button small" type="submit">Am înțeles</button>', bar)
        self.assertNotIn('type="checkbox"', bar)
        self.assertNotIn("Folosim cookie-uri", bar)  # No cookie sentence in the bar.
        self.assertNotIn("accept_legal", html)
        footer = section(html, 'class="site-footer"', "</footer>")
        self.assertIn('<a href="/cookie-uri/" data-consent-open>Setări cookie-uri</a>', footer)
        # Nothing is refused before "Am înțeles", and anonymous analytics is on by default.
        response = self.correct()
        self.assertContains(response, escape(correction_result().corrected_text))
        self.assertIn(VISITOR_COOKIE, response.cookies)
        self.assertEqual(UsageEvent.objects.get().visitor, AnonymousVisitor.objects.get())

    def test_legal_pages_have_no_consent_form_in_the_page(self):
        for path in ("/termeni/", "/confidentialitate/"):
            with self.subTest(path=path):
                main = section(self.html(path), '<main id="main">', "</main>")
                self.assertNotIn("consent-form", main)

    def test_am_inteles_hides_the_notice_and_keeps_analytics_on(self):
        response = self.acknowledge()
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        cookie = response.cookies[CONSENT_COOKIE]
        self.assertEqual(cookie.value, consent_cookie_value(True))
        self.assertEqual((cookie["max-age"], cookie["samesite"]), (365 * 24 * 60 * 60, "Lax"))
        self.assertNotIn('class="consent-bar"', self.html())
        self.assertIn(VISITOR_COOKIE, self.correct().cookies)

    def test_switching_analytics_off_and_on(self):
        self.assertIn('name="allow_analytics" checked', section(self.html(), 'id="consent-dialog"', "</dialog>"))
        self.assertIn(VISITOR_COOKIE, self.correct().cookies)
        visitor = AnonymousVisitor.objects.get()
        response = self.settings_form()  # Unticked: switched off.
        self.assertEqual(response.cookies[VISITOR_COOKIE]["max-age"], 0)
        self.assertEqual(response.cookies[CONSENT_COOKIE].value, consent_cookie_value(False))
        self.assertNotIn('class="consent-bar"', self.html())  # Saving the settings also dismisses the notice.
        self.assertNotIn('name="allow_analytics" checked', section(self.html(), 'id="consent-dialog"', "</dialog>"))
        response = self.correct()
        self.assertNotIn(VISITOR_COOKIE, response.cookies)  # Never regenerated while off.
        self.assertEqual(list(AnonymousVisitor.objects.all()), [visitor])
        self.assertIsNone(UsageEvent.objects.latest("pk").visitor)
        self.assertEqual(self.acknowledge().cookies[CONSENT_COOKIE].value, consent_cookie_value(False))  # Choice kept.
        self.settings_form(allow_analytics="on")
        self.assertEqual(self.client.cookies[CONSENT_COOKIE].value, consent_cookie_value(True))
        self.assertIn(VISITOR_COOKIE, self.correct().cookies)

    def test_cookie_settings_page_works_without_javascript(self):
        response = self.client.get("/cookie-uri/?next=/termeni/")
        self.assertContains(response, "<h1>Setări cookie-uri</h1>")
        self.assertContains(response, 'name="next" value="/termeni/"')

    def test_new_document_versions_show_the_notice_again(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(False)
        self.assertNotIn('class="consent-bar"', self.html())
        with override_settings(TERMS_VERSION="2099-01-01"):
            self.assertIn('class="consent-bar"', self.html())
            self.assertEqual(self.acknowledge().cookies[CONSENT_COOKIE].value, "2099-01-01|2026-09-14.2|0")

    def test_signed_in_users_are_recorded_against_their_account(self):
        user = User.objects.create_user("ana", password="test-password")
        self.client.force_login(user)
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)
        # A browser acknowledgement does not count for the account.
        self.assertIn('class="consent-bar"', self.html())
        self.acknowledge()
        acceptance = LegalAcceptance.objects.get(user=user)
        self.assertEqual((acceptance.terms_version, acceptance.privacy_version, acceptance.source),
                         ("2026-09-14", "2026-09-14.2", "visit"))
        self.assertNotIn('class="consent-bar"', self.html())
        with override_settings(PRIVACY_VERSION="2099-01-01"):
            self.assertIn('class="consent-bar"', self.html())

    def test_unsafe_next_is_ignored(self):
        response = self.client.post("/cookie-uri/", {"next": "https://evil.example/", "acknowledge": "1"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)


class FooterAndPageTests(TestCase):
    def test_footer_on_every_page(self):
        year = timezone.localtime().year
        for path in ("/", "/confidentialitate/", "/termeni/", "/accounts/login/", "/accounts/signup/"):
            with self.subTest(path=path):
                footer = section(self.client.get(path).content.decode(), 'class="site-footer"', "</footer>")
                self.assertIn(f"© {year} Corect.uk", footer)
                for link in ('href="/confidentialitate/">Confidențialitate</a>', 'href="/termeni/">Termeni</a>',
                             'href="/contact/">Contact</a>', 'href="/cookie-uri/" data-consent-open>Setări cookie-uri</a>'):
                    self.assertIn(link, footer)
                self.assertIn("Corect.uk este un nume comercial operat de Test Operator.", footer)
                self.assertNotIn("1 Test Street", footer)  # No postal address in the footer.
                self.assertNotIn("/history/", footer)
                self.assertNotIn('href="#"', footer)

    def test_contact_page_shows_only_configured_details(self):
        response = self.client.get("/contact/")
        self.assertContains(response, "Pentru întrebări despre cont, date personale, serviciu sau reclamații")
        self.assertContains(response, "<dt>Operator</dt><dd>Test Operator</dd>")
        self.assertContains(response, 'href="mailto:contact@example.com"')
        self.assertContains(response, "<dt>Adresă de corespondență</dt><dd>1 Test Street, London</dd>")
        self.assertNotContains(response, "Număr de înregistrare")
        self.assertNotContains(response, "Cod TVA")
        self.assertContains(response, "<h1", count=1)

    @override_settings(LEGAL_OPERATOR_TYPE="company", LEGAL_OPERATOR_NAME="Corect Learning Ltd", COMPANY_NUMBER="12345678",
                       VAT_NUMBER="GB123456789")
    def test_a_company_operator_is_identified_everywhere_from_settings(self):
        statement = "Corect.uk este operat de Corect Learning Ltd, companie înregistrată în Anglia și Țara Galilor cu numărul 12345678."
        for path in ("/", "/termeni/", "/confidentialitate/"):
            self.assertContains(self.client.get(path), statement)
        response = self.client.get("/contact/")
        self.assertContains(response, "<dt>Număr de înregistrare</dt><dd>12345678</dd>")
        self.assertContains(response, "<dt>Cod TVA (VAT)</dt><dd>GB123456789</dd>")

    @override_settings(LEGAL_OPERATOR_NAME="", CONTACT_EMAIL="", LEGAL_SERVICE_ADDRESS="")
    def test_blank_identity_shows_no_empty_labels(self):
        self.assertEqual(self.client.get("/contact/").status_code, 404)
        for path in ("/", "/termeni/", "/confidentialitate/"):
            html = self.client.get(path).content.decode()
            self.assertNotIn("operat de", html)
            self.assertNotIn('href="/contact/"', html)
            self.assertNotIn("mailto:", html)

    def test_privacy_notice_reflects_the_real_data_flow(self):
        response = self.client.get("/confidentialitate/")
        self.assertContains(response, "<h1>Politica de confidențialitate</h1>")
        self.assertContains(response, "<h1", count=1)
        for text in ("Executarea contractului cu tine", "Interes legitim", "Te poți opune oricând, oprindu-l din Setări cookie-uri",
                     "<code>store=False</code>", "Asta nu înseamnă că OpenAI nu păstrează nimic",
                     "browserul tău trimite sunetul direct către OpenAI", "<code>corect_consent</code>",
                     "<code>sessionid</code>", "<code>csrftoken</code>", "<code>corect_visitor_id</code>",
                     "Identificatorul este activ implicit", "7 zile", "14 zile",
                     "https://ico.org.uk/make-a-complaint/", "Nu ai nevoie de acordul nostru",
                     "dreptul de acces", "portabilitate", "Perioada nu este încă stabilită",
                     "Corect.uk este un nume comercial operat de Test Operator."):
            self.assertContains(response, text)
        self.assertNotContains(response, "OpenAI nu stochează nimic")
        with override_settings(ANALYTICS_VISITOR_COOKIE=False):
            self.assertContains(self.client.get("/confidentialitate/"), "Cookie-ul de statistici pentru vizitatori este dezactivat.")

    def test_terms_cover_the_required_topics(self):
        response = self.client.get("/termeni/")
        self.assertContains(response, "<h1>Termeni de utilizare</h1>")
        for anchor in ("operator", "acceptance", "age", "accounts", "educational", "ai", "content",
                       "intellectual-property", "acceptable-use", "free", "pro", "fair-use", "suspension",
                       "availability", "changes", "consumer-rights", "law", "complaints"):
            self.assertContains(response, f'<h2 id="{anchor}">')
        for text in ("cel puțin 16 ani", "nu garantează rezultate la examene, angajare, rezultate în proceduri de imigrare",
                     "legea din Anglia și Țara Galilor", "Versiunea 2026-09-14"):
            self.assertContains(response, text)


class LegalCheckTests(TestCase):
    @override_settings(DEBUG=False, LEGAL_OPERATOR_NAME="", LEGAL_SERVICE_ADDRESS=" ", CONTACT_EMAIL="")
    def test_production_refuses_blank_legal_identity(self):
        errors = legal_identity_configured(None)
        self.assertEqual([error.id for error in errors], ["core.E001", "core.E002", "core.E003"])
        self.assertIn("LEGAL_OPERATOR_NAME", errors[0].msg)

    @override_settings(DEBUG=True, LEGAL_OPERATOR_NAME="", LEGAL_SERVICE_ADDRESS="", CONTACT_EMAIL="")
    def test_local_development_is_not_blocked(self):
        self.assertEqual(legal_identity_configured(None), [])

    @override_settings(DEBUG=False)
    def test_configured_identity_passes(self):
        self.assertEqual(legal_identity_configured(None), [])
