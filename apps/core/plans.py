"""Plans: who is anonymous, Free or Pro, and how the plans are shown on the homepage.

The daily „Vreau să sune natural!” allowance of each tier is enforced (NATURALIZE_DAILY_LIMITS in settings, applied by
apps/assistant/services/quota.py); payments and subscriptions are not implemented yet. Prices come from settings
(PRO_DISPLAY_PRICE, PRO_PROMO_ENABLED, PRO_PROMO_PRICE); templates never contain a price or a limit.
"""
from django.conf import settings

from apps.core.text import romanian_count

# Staff add a user to this group in /admin/ to mark them as Pro (created by core.0001_pro_group). When subscriptions
# exist, is_pro() should read them instead; every plan decision goes through is_pro() and tier_for().
PRO_GROUP = "Pro"
# Plan tiers. The same keys name the per-tier values in NATURALIZE_DAILY_LIMITS and VOICE_DAILY_GUARDRAILS.
ANONYMOUS, FREE, PRO = "anonymous", "free", "pro"
TIERS = (ANONYMOUS, FREE, PRO)


def is_pro(user):
    return user.is_authenticated and user.groups.filter(name=PRO_GROUP).exists()


def tier_for(user):
    """The one place that decides a request's plan tier."""
    if not user.is_authenticated:
        return ANONYMOUS
    return PRO if is_pro(user) else FREE


def daily_uses(tier):
    """„20 de naturalizări pe zi”, from the configured limit."""
    return f"{romanian_count(settings.NATURALIZE_DAILY_LIMITS[tier], 'naturalizare', 'naturalizări')} pe zi"


def pro_prices():
    """(price shown as the monthly price, normal price shown struck through or "" when there is no promotion)."""
    if settings.PRO_PROMO_ENABLED and settings.PRO_PROMO_PRICE:
        return settings.PRO_PROMO_PRICE, settings.PRO_DISPLAY_PRICE
    return settings.PRO_DISPLAY_PRICE, ""


def display_plans():
    pro_price, pro_original_price = pro_prices()
    return (
        {"key": "free", "name": "Free", "subtitle": "Pentru testare și utilizare ocazională", "price": "£0",
         "original_price": "", "period": "", "icon": "icons/user.html", "recommended": False, "fair_use": False,
         "daily": daily_uses(FREE),
         "features": ((daily_uses(FREE).capitalize(), True), ("Voce în timp real", True), ("British TextToSpeech", True),
                      ("Istoric limitat", True), ("Progres", False), ("Categorii de greșeli", False), ("Practice", False),
                      ("Exerciții bazate pe greșelile tale", False), ("Statistici de evoluție", False))},
        # The card reads "Cereri nelimitate (Fair Use)"; the Fair Use ceiling (daily_uses(PRO)) is still enforced by the
        # quota and stated in the Terms (section 13), which the card's Fair Use link opens.
        {"key": "pro", "name": "Pro", "subtitle": "Pentru cei care vor să progreseze serios", "price": pro_price,
         "original_price": pro_original_price, "period": "/ lună", "icon": "icons/crown.html", "recommended": True,
         "fair_use": True, "daily": "Cereri nelimitate (Fair Use)",
         "features": (("Cereri nelimitate (Fair Use)", True), ("Voce în timp real", True),
                      ("British TextToSpeech", True), ("Istoric complet", True), ("Progres", True),
                      ("Categorii de greșeli", True), ("Practice", True), ("Exerciții bazate pe greșelile tale", True),
                      ("Statistici de evoluție", True))},
    )
