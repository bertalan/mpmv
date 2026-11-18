"""Map tile routes (EVT.14), included at /maps/ outside the language prefixes."""

from django.urls import URLPattern, re_path

from .views import tile

urlpatterns: list[URLPattern] = [
    # Any segment reaches the view, which answers 404 for everything but canonical integers.
    re_path(r"^tiles/(?P<z>[^/]+)/(?P<x>[^/]+)/(?P<y>[^/]+)\.png$", tile, name="map_tile"),
]
