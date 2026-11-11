"""Public compliance routes, mounted inside `i18n_patterns` before the CMS catch-all (CMP.5)."""

from django.urls import path

from . import views

urlpatterns = [
    path("contact-points/", views.contact_points, name="dsa_contact_points"),
    path("report/", views.notice_form, name="dsa_notice"),
    path("report/received/", views.notice_received, name="dsa_notice_received"),
    # CMP.6: the online withdrawal function and the pre-contractual information
    path("withdrawal/", views.withdrawal_start, name="withdrawal"),
    path("withdrawal/confirm/", views.withdrawal_confirm, name="withdrawal_confirm"),
    path("withdrawal/done/", views.withdrawal_done, name="withdrawal_done"),
    path("consumer-information/", views.consumer_information, name="consumer_information"),
]
