"""Public pages served by core until the CMS takes over the home page (CMS.1)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Placeholder storefront home, one per language prefix (I18N.1)."""
    return render(request, "core/home.html")
