from allauth.account import views as allauth_views
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts import views as accounts
from apps.analytics import public_views as analytics_public
from apps.assistant import views as assistant
from apps.assistant import voice_views as voice
from apps.core import health, seo
from apps.core import views as core
from apps.learning import views as learning

urlpatterns = [
    path("", core.home, name="home"),
    path("healthz", health.healthz, name="healthz"),
    path("readyz", health.readyz, name="readyz"),
    path("robots.txt", seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap_xml"),
    path("about/", core.about_page, name="about"),
    # The page's earlier address, still linked from outside: a permanent redirect, keeping any query string.
    path("despre/", RedirectView.as_view(pattern_name="about", permanent=True, query_string=True)),
    path("confidentialitate/", core.privacy_page, name="privacy"),
    path("termeni/", core.terms_page, name="terms"),
    path("contact/", core.contact_page, name="contact"),
    path("cookie-uri/", core.consent_page, name="consent"),
    path("analytics/event/", analytics_public.funnel_event, name="funnel_event"),
    path("naturalize/", assistant.naturalize, name="naturalize"),
    path("assistant/realtime-transcription/session/", voice.start_realtime_transcription, name="realtime_session"),
    path("assistant/realtime-transcription/finish/", voice.finish_realtime_transcription, name="realtime_finish"),
    path("assistant/transcribe/", voice.transcribe_recording, name="transcribe"),
    path("assistant/speech/", voice.speak, name="speech"),
    # django-allauth handles signing up, in and out, addresses, passwords and Google (apps/accounts/adapters.py). The
    # short names used across the site point at the same views.
    path("accounts/signup/", allauth_views.signup, name="signup"),
    path("accounts/login/", allauth_views.login, name="login"),
    path("accounts/logout/", allauth_views.logout, name="logout"),
    path("accounts/profile/", accounts.profile, name="profile"),
    path("accounts/verification/resend/", accounts.resend_verification, name="resend_verification"),
    path("accounts/", include("allauth.urls")),
    path("billing/", include("apps.billing.urls")),
    path("history/", learning.history, name="history"),
    path("history/<int:pk>/", learning.history_detail, name="history_detail"),
    path("mistakes/", learning.mistakes, name="mistakes"),
    path("mistakes/<slug:category>/", learning.mistake_category, name="mistake_category"),
    path("progress/", learning.progress, name="progress"),
    path("practice/", learning.practice, name="practice"),
    path("learn/", learning.dashboard, name="learn_dashboard"),
    path("learn/practice/start/", learning.practice_start, name="learn_start"),
    path("learn/practice/<uuid:pk>/", learning.practice_session, name="learn_session"),
    # Staff-only analytics; listed before the admin so /admin/analytics/ is the dashboard.
    path("admin/analytics/", include("apps.analytics.urls")),
    path("admin/", admin.site.urls),
]
