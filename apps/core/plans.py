"""Plans shown on the homepage.

Presentation only: payments, subscriptions and plan limits are not implemented yet, and nothing here is enforced.
Real entitlements can later be attached to each plan by its key.
"""
from django.conf import settings

# Staff add a user to this group in /admin/ to mark them as Pro (created by core.0001_pro_group). It only changes
# what the homepage plans section shows; when subscriptions exist, is_pro() should read them instead.
PRO_GROUP = "Pro"


def is_pro(user):
    return user.is_authenticated and user.groups.filter(name=PRO_GROUP).exists()


def display_plans():
    return (
        {"key": "free", "name": "Free", "subtitle": "Pentru testare și utilizare ocazională", "price": "£0", "period": "",
         "icon": "icons/user.html", "recommended": False,
         "features": (("Corectări limitate", True), ("Traduceri limitate", True), ("Voce în timp real limitată", True),
                      ("British TTS limitat", True), ("Istoric limitat", True), ("Progres", False),
                      ("Categorii de greșeli", False), ("Practice", False), ("Exerciții bazate pe greșelile tale", False),
                      ("Statistici de evoluție", False))},
        {"key": "pro", "name": "Pro", "subtitle": "Pentru cei care vor să progreseze serios",
         "price": settings.PRO_DISPLAY_PRICE, "period": "/ lună", "icon": "icons/crown.html", "recommended": True,
         "features": (("Corectări nelimitate", True), ("Traduceri nelimitate", True),
                      ("Voce în timp real nelimitată", True), ("British TTS nelimitat", True),
                      ("Istoric complet", True), ("Progres", True), ("Categorii de greșeli", True), ("Practice", True),
                      ("Exerciții bazate pe greșelile tale", True), ("Statistici de evoluție", True))},
    )
