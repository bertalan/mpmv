"""Root URLconf."""

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import path


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for the container runtime and uptime checks."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("django-admin/", admin.site.urls),
]
