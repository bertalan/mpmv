"""Public link to the sales terms in force (VEN.35), mounted without a language prefix."""

from django.urls import path

from . import terms_views

app_name = "vendor_terms"

urlpatterns = [
    path("<str:token>/pdf/", terms_views.download, name="pdf"),
    path("<str:token>/", terms_views.read, name="read"),
]
