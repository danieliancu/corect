"""Plans: who is anonymous, Free or Pro, and how the plans are shown on the homepage.

Pro is a paid Stripe subscription whose state the webhooks keep (apps/billing), or the manual "Pro" group below. The
daily „Vreau să sune natural!” allowance of each tier is enforced (NATURALIZE_DAILY_LIMITS, apps/assistant/services/
quota.py) and the Pro-only features by apps/core/entitlements.py. Prices come from settings (PRO_DISPLAY_PRICE,
PRO_PROMO_ENABLED, PRO_PROMO_PRICE); templates never contain a price or a limit.
"""
from django.conf import settings

from apps.core.text import romanian_count

# MANUAL OVERRIDE: staff add a user to this group in /admin/ to give Pro without a subscription (support, testing,
# partners). It never creates or changes a Stripe subscription. Every plan decision goes through is_pro() and tier_for().
PRO_GROUP = "Pro"
PAID, MANUAL = "paid", "manual"
# Free accounts see this many days of history; older entries are kept and reappear with Pro (apps/learning/views.py).
FREE_HISTORY_DAYS = 30
# Plan tiers. The same keys name the per-tier values in NATURALIZE_DAILY_LIMITS and VOICE_DAILY_GUARDRAILS.
ANONYMOUS, FREE, PRO = "anonymous", "free", "pro"
TIERS = (ANONYMOUS, FREE, PRO)


def pro_source(user):
    """"paid" (an entitled Stripe subscription), "manual" (the Pro group) or "" — worked out once per request object."""
    if not user.is_authenticated:
        return ""
    cached = getattr(user, "_corect_pro_source", None)
    if cached is None:
        from apps.billing.services import has_entitled_subscription  # billing imports plans for its messages
        cached = PAID if has_entitled_subscription(user) else (
            MANUAL if user.groups.filter(name=PRO_GROUP).exists() else "")
        user._corect_pro_source = cached
    return cached


def is_pro(user):
    return bool(pro_source(user))


def forget_plan(user):
    """After a subscription change in the same request (checkout return, deletion), read the plan again."""
    try:
        del user._corect_pro_source  # delattr reaches the real user through request.user's lazy wrapper
    except AttributeError:
        pass


def tier_for(user):
    """The one place that decides a request's plan tier."""
    if not user.is_authenticated:
        return ANONYMOUS
    return PRO if is_pro(user) else FREE


def daily_uses(tier):
    """„20 de naturalizări pe zi”, from the configured limit."""
    return f"{romanian_count(settings.NATURALIZE_DAILY_LIMITS[tier], 'naturalizare', 'naturalizări')} pe zi"


def daily_requests(tier):
    """„20 de cereri pe zi” for the plan cards, from the same configured limit."""
    return f"{romanian_count(settings.NATURALIZE_DAILY_LIMITS[tier], 'cerere', 'cereri')} pe zi"


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
         "daily": daily_requests(FREE),
         "features": ((daily_requests(FREE).capitalize(), True), ("Voce în timp real", True), ("British TextToSpeech", True),
                      (f"Istoric: ultimele {FREE_HISTORY_DAYS} de zile", True), ("Progres", False),
                      ("Categorii de greșeli", False), ("Practice", False),
                      ("Exerciții bazate pe greșelile tale", False), ("Statistici de evoluție", False))},
        # The card reads "Cereri nelimitate (Fair Use)"; the Fair Use ceiling (daily_uses(PRO)) is still enforced by the
        # quota and stated in the Terms (section 13), which the card's Fair Use link opens.
        {"key": "pro", "name": "Pro", "subtitle": "Pentru cei care vor să facă progrese serioase", "price": pro_price,
         "original_price": pro_original_price, "period": "/ lună", "icon": "icons/crown.html", "recommended": True,
         "fair_use": True, "daily": "Cereri nelimitate (Fair Use)",
         "features": (("Cereri nelimitate (Fair Use)", True), ("Voce în timp real", True),
                      ("British TextToSpeech", True), ("Istoric complet", True), ("Progres", True),
                      ("Categorii de greșeli", True), ("Practice", True), ("Exerciții bazate pe greșelile tale", True),
                      ("Statistici de evoluție", True))},
    )
