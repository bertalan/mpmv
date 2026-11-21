"""The customer's "My garage" page, mounted at `/account/garage/` without a language prefix
(FIT.5 2/3), like the other pages of the customer area. Every action is a POST; the profile in a
path is looked up among the signed-in customer's own."""

from django.urls import path

from . import garage_views as views

urlpatterns = [
    path("", views.garage_page, name="garage_account"),
    path("reset/", views.reset, name="garage_reset"),
    path("<uuid:public_id>/activate/", views.activate, name="garage_profile_activate"),
    path("<uuid:public_id>/rename/", views.rename, name="garage_profile_rename"),
    path("<uuid:public_id>/duplicate/", views.duplicate, name="garage_profile_duplicate"),
    path("<uuid:public_id>/delete/", views.delete, name="garage_profile_delete"),
]
