"""The customer help pages (ARC.37), inside `i18n_patterns` before the CMS catch-all."""

from django.urls import path

from . import help_views

urlpatterns = [
    path("help/", help_views.customer_help, name="customer_help"),
    path("help/<slug:section>/", help_views.customer_help_section, name="customer_help_section"),
]
