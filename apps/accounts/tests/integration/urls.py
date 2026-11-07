"""Test-only URLconf: the project's routes plus test views behind the account decorators.

- A stand-in registration view behind `protect_registration` (CORE.5). No registration view
  exists yet; the one that comes with customer accounts uses this decorator.
- Views protected by `step_up_required` (CORE.4).
"""

from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.accounts.decorators import protect_registration, step_up_required
from mpmv.urls import urlpatterns as project_urlpatterns


@protect_registration
def register(request: HttpRequest) -> HttpResponse:
    return HttpResponse("form" if request.method == "GET" else "created")


@step_up_required()
def sensitive(request: HttpRequest) -> HttpResponse:
    return HttpResponse("sensitive")


@step_up_required(max_age=30)
def very_sensitive(request: HttpRequest) -> HttpResponse:
    return HttpResponse("very sensitive")


urlpatterns = [
    path("register/", register, name="test-register"),
    path("test/sensitive/", sensitive, name="test-sensitive"),
    path("test/very-sensitive/", very_sensitive, name="test-very-sensitive"),
    *project_urlpatterns,
]
