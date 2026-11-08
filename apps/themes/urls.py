"""Routes of the themes app: the staff-only styleguide page (THM.4, spec 14 §14.9)."""

from django.urls import path

from . import views

app_name = "themes"

urlpatterns = [
    path("", views.styleguide, name="styleguide"),
]
