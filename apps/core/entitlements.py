"""Feature entitlements, decided in one place. Until billing exists every signed-in user has every learning feature;
set PRO_ENTITLEMENTS_ENFORCED=true to restrict the Pro features to Pro accounts (apps/core/plans.py:is_pro)."""
from django.conf import settings

from .plans import is_pro

PRO_FEATURES = {"learning_dashboard", "personalised_practice", "progress_insights", "mistake_categories"}


def has_feature(user, feature):
    if not user.is_authenticated:
        return False
    if feature not in PRO_FEATURES or not settings.PRO_ENTITLEMENTS_ENFORCED:
        return True
    return is_pro(user)
