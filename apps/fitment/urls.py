"""Public garage routes, mounted inside `i18n_patterns` (FIT.6)."""

from django.urls import path

from . import views

urlpatterns = [
    path("garage/", views.choose_garage, name="garage_choose"),
    path("garage/apply/", views.apply_garage, name="garage_apply"),
    path("garage/dismiss/", views.dismiss_garage_line, name="garage_line_dismiss"),
    path("garage/merge/", views.merge_garage, name="garage_merge"),
    path("garage/save/", views.save_vehicle, name="garage_save"),
    path("garage/remove/", views.remove_vehicle, name="garage_remove"),
    path("garage/forget/", views.forget_garage, name="garage_forget"),
]
