"""Thin views around Wagtail's page serving (CMS.1)."""

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from wagtail.views import serve


def home(request: HttpRequest) -> HttpResponseBase:
    """The active language's home page (`/`, `/it/`, `/de/`), served by Wagtail."""
    response: HttpResponseBase = serve(request, "")
    return response
