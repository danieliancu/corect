"""Feature entitlements, decided in one place from the plan (apps/core/plans.py:is_pro). The pages behind these features
show a short "Disponibil în Pro" screen to Free accounts (pro_feature below); the data they read keeps being recorded, so
it is all there after an upgrade."""
from functools import wraps

from django.shortcuts import render

from .plans import is_pro

LEARNING_DASHBOARD, PERSONALISED_PRACTICE = "learning_dashboard", "personalised_practice"
PROGRESS_INSIGHTS, MISTAKE_CATEGORIES, FULL_HISTORY = "progress_insights", "mistake_categories", "full_history"
PRO_FEATURES = {LEARNING_DASHBOARD, PERSONALISED_PRACTICE, PROGRESS_INSIGHTS, MISTAKE_CATEGORIES, FULL_HISTORY}
# What the "Disponibil în Pro" screen says each feature brings.
FEATURE_PITCH = {
    LEARNING_DASHBOARD: "Panoul cu ce să exersezi azi, progresul și tipologiile de greșeli",
    PERSONALISED_PRACTICE: "Exerciții create din greșelile tale",
    PROGRESS_INSIGHTS: "Progresul tău și statisticile de evoluție",
    MISTAKE_CATEGORIES: "Greșelile tale, pe categorii și tipologii",
    FULL_HISTORY: "Tot istoricul tău, nu doar ultimele 30 de zile",
}


def has_feature(user, feature):
    if not user.is_authenticated:
        return False
    return feature not in PRO_FEATURES or is_pro(user)


def upgrade_response(request, feature, section=""):
    """The "Disponibil în Pro" screen shown instead of a Pro page."""
    return render(request, "core/pro_required.html",
                  {"feature_pitch": FEATURE_PITCH.get(feature, ""), "learning_section": section})


def pro_feature(feature, section=""):
    """For a view behind login_required: Free accounts get the upgrade screen instead of the page."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if has_feature(request.user, feature):
                return view(request, *args, **kwargs)
            return upgrade_response(request, feature, section)
        return wrapped
    return decorator
