"""Test-only URLconf: plain Django views protected by `require_api_key`."""

from django.http import HttpRequest, JsonResponse
from django.urls import path

from apps.api.auth import api_client_of, require_api_key


@require_api_key("catalog:write")
def catalog_write(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"client": api_client_of(request).name})


urlpatterns = [path("protected/catalog", catalog_write, name="protected-catalog")]
