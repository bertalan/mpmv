"""The one-time link of a cart prepared by support (ORD.32), mounted at `/account/cart-draft/`
without a language prefix. The token is in the GET path, so the request logs redact it
(`mpmv.log_redaction`); the POST carries it in its body."""

from django.urls import path

from . import customer_views as views

urlpatterns = [
    path("continue/", views.draft_link_continue, name="staff_draft_link_continue"),
    path("<str:token>/", views.draft_link, name="staff_draft_link"),
]
