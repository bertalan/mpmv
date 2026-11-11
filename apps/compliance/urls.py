"""Public compliance routes, mounted inside `i18n_patterns` before the CMS catch-all (CMP.5)."""

from django.urls import path

from . import views

urlpatterns = [
    path("contact-points/", views.contact_points, name="dsa_contact_points"),
    path("privacy/sub-processors/", views.subprocessors, name="subprocessors"),
    path("report/", views.notice_form, name="dsa_notice"),
    path("report/received/", views.notice_received, name="dsa_notice_received"),
]
