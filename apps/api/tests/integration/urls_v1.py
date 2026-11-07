"""Test-only URLconf: the real project routes plus the test API v1 at /api/v1-test/."""

from django.urls import path

from mpmv.urls import urlpatterns as project_urlpatterns

from .v1_api import test_api

urlpatterns = [path("api/v1-test/", test_api.urls), *project_urlpatterns]
