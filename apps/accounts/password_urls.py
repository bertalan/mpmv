"""The account's password change (CORE.28), mounted at /account/password/ without a prefix."""

from django.urls import path

from . import password_views

app_name = "password"

urlpatterns = [
    path("change/", password_views.change, name="change"),
]
