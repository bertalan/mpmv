"""Wagtail hooks of the SEO app."""

from typing import Any

from django.http import HttpRequest
from wagtail import hooks
from wagtail.models import Page


@hooks.register("before_serve_page", order=100)  # type: ignore[untyped-decorator]
def mark_unrestricted_page(
    page: Page, request: HttpRequest, serve_args: Any, serve_kwargs: Any
) -> None:
    """Tell the page cache (SEO.6) this page has no view restriction on it or its ancestors.

    Runs after Wagtail's own restriction check (order 0): when that check answers with a login
    redirect or a password form, this hook never runs, so the request stays unmarked and the page
    cache does not store the response. A restricted page is never cached, whatever its state.
    """
    request._page_cache_unrestricted = not page.get_view_restrictions().exists()  # type: ignore[attr-defined]
