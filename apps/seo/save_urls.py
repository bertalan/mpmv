"""The consent save endpoint (SEO.5): one URL for the banner on every page, no language prefix."""

from django.urls import path

from . import views

urlpatterns = [
    path("consent/", views.consent_save, name="consent_save"),
]
