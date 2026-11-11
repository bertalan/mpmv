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

    The hook reads `get_view_restrictions()` itself and does not rely on Wagtail's restriction
    check, which is the `on_serve_page` wrapper that runs after every `before_serve_page` hook
    (wagtail/views.py). A restricted page is therefore marked `False`, and the middleware stores
    only pages marked `True`; a request this hook never reached stays unmarked and is not stored.
    """
    request._page_cache_unrestricted = not page.get_view_restrictions().exists()  # type: ignore[attr-defined]
