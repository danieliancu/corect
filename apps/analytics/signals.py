from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .services.visitors import link_visitor


@receiver(user_logged_in, dispatch_uid="analytics_link_visitor_on_login")
def link_visitor_on_login(sender, request, user, **kwargs):
    # Signup links first with via="signup", so this only converts visitors signing in to an existing account.
    link_visitor(request, user, via="login")
