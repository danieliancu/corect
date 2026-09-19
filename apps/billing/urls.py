from django.urls import path

from . import views

urlpatterns = [
    path("checkout/", views.checkout, name="billing_checkout"),
    path("success/", views.success, name="billing_success"),
    path("portal/", views.portal, name="billing_portal"),
    path("webhook/stripe/", views.stripe_webhook, name="stripe_webhook"),
]
