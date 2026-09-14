"""Plans shown on the homepage.

Presentation only: payments, subscriptions and plan limits are not implemented yet, and nothing here is enforced.
Real entitlements can later be attached to each plan by its key. Prices come from settings (PRO_DISPLAY_PRICE,
PRO_PROMO_ENABLED, PRO_PROMO_PRICE); templates never contain a price.
"""
from django.conf import settings

# Staff add a user to this group in /admin/ to mark them as Pro (created by core.0001_pro_group). It only changes
# what the homepage plans section shows; when subscriptions exist, is_pro() should read them instead.
PRO_GROUP = "Pro"


def is_pro(user):
    return user.is_authenticated and user.groups.filter(name=PRO_GROUP).exists()


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
         "features": (("Corectări limitate", True), ("Traduceri limitate", True), ("Voce în timp real limitată", True),
                      ("British TTS limitat", True), ("Istoric limitat", True), ("Progres", False),
                      ("Categorii de greșeli", False), ("Practice", False), ("Exerciții bazate pe greșelile tale", False),
                      ("Statistici de evoluție", False))},
        # "Nelimitat" means no numeric cap on normal personal use; the Fair Use section of the Terms covers abuse only.
        {"key": "pro", "name": "Pro", "subtitle": "Pentru cei care vor să progreseze serios", "price": pro_price,
         "original_price": pro_original_price, "period": "/ lună", "icon": "icons/crown.html", "recommended": True,
         "fair_use": True,
         "features": (("Corectări nelimitate", True), ("Traduceri nelimitate", True),
                      ("Voce în timp real nelimitată", True), ("British TTS nelimitat", True),
                      ("Istoric complet", True), ("Progres", True), ("Categorii de greșeli", True), ("Practice", True),
                      ("Exerciții bazate pe greșelile tale", True), ("Statistici de evoluție", True))},
    )
