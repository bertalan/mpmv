"""Session level routes (D-32, CORE.25), mounted under /account/level/."""

from django.urls import path

from . import level_views

app_name = "levels"

urlpatterns = [
    path("options/", level_views.options, name="options"),
    path("operational/", level_views.operational, name="operational"),
    path("once/options/", level_views.once_options, name="once_options"),
    path("once/", level_views.once, name="once"),
    path("end/", level_views.end, name="end"),
]
