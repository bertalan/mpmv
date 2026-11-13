"""The contextual help endpoint (ARC.32), mounted without a language prefix."""

from django.urls import path

from . import help_views

urlpatterns = [path("entry/", help_views.help_entry, name="help_entry")]
