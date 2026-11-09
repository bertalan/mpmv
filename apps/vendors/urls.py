"""Vendor claim routes (VEN.24), mounted without a language prefix under /vendor/claim/."""

from django.urls import path

from . import views

app_name = "vendor_claim"

urlpatterns = [
    path("<str:vendor_key>/", views.claim, name="claim"),
]
