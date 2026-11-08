"""Secure action link routes (CORE.15), mounted without a language prefix under /account/link/."""

from django.urls import path

from . import action_link_views

app_name = "action_link"

urlpatterns = [
    path("continue/", action_link_views.continue_link, name="continue"),
    path("<str:token>/", action_link_views.open_link, name="open"),
]
