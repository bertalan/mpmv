"""API v1 routes, included at /api/v1/ outside the language prefixes (mpmv.urls)."""

from django.urls import path, re_path

from .v1.api import api
from .v1.errors import not_found

urlpatterns = [
    path("", api.urls),
    re_path(r"", not_found),  # unknown API paths: a 404 problem, never the HTML error page
]
