"""The help pages for customers and sellers (ARC.37), before the CMS catch-all."""

from django.urls import path

from . import help_views

urlpatterns = [
    path("help-centre/", help_views.customer_help, name="customer_help"),
    # Before the customer pattern, which would also match "sellers".
    path("help-centre/sellers/", help_views.vendor_help, name="vendor_help"),
    path(
        "help-centre/sellers/<slug:section>/",
        help_views.vendor_help_section,
        name="vendor_help_section",
    ),
    path(
        "help-centre/<slug:section>/",
        help_views.customer_help_section,
        name="customer_help_section",
    ),
]
