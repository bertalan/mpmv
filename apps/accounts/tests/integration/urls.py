"""Test-only URLconf: a stand-in registration view behind `protect_registration` (CORE.5).

No registration view exists yet; the one that comes with customer accounts uses this decorator.
"""

from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.accounts.decorators import protect_registration


@protect_registration
def register(request: HttpRequest) -> HttpResponse:
    return HttpResponse("form" if request.method == "GET" else "created")


urlpatterns = [path("register/", register, name="test-register")]
