from django.urls import path

from . import views

app_name = "analytics"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users/", views.users_report, name="users"),
    path("users/<int:pk>/", views.user_detail, name="user"),
    path("visitors/", views.visitors_report, name="visitors"),
    path("visitors/<uuid:pk>/", views.visitor_detail, name="visitor"),
]
