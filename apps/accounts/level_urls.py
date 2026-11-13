"""Passkey window routes (D-32, CORE.25), mounted under /account/level/."""

from django.urls import path

from . import level_views

app_name = "levels"

urlpatterns = [
    path("options/", level_views.options, name="options"),
    path("window/", level_views.window, name="window"),
    path("once/options/", level_views.once_options, name="once_options"),
    path("once/", level_views.once, name="once"),
    path("close/", level_views.close, name="close"),
]
