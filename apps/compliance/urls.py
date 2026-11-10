"""Public compliance routes, mounted inside `i18n_patterns` before the CMS catch-all (CMP.5)."""

from django.urls import path

from . import views

urlpatterns = [
    path("contact-points/", views.contact_points, name="dsa_contact_points"),
]
