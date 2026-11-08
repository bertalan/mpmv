"""Geocoding routes (EVT.12), included at /geocoding/ outside the language prefixes."""

from django.urls import URLPattern, path

from .views import find_coordinates

urlpatterns: list[URLPattern] = [
    path("find/", find_coordinates, name="geocoding_find"),
]
