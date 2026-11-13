"""Consent settings page (SEO.5), mounted in `i18n_patterns` so it is shown in the visitor's
language. The save endpoint has no language prefix: see `save_urls`."""

from django.urls import path

from . import views

urlpatterns = [
    path("cookie-settings/", views.consent_preferences, name="consent_preferences"),
]
