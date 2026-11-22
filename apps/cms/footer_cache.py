"""The cached footer content (CMS.17): one answer per site and language, dropped when a footer
setting, column or profile changes, or when a page is published or unpublished (the links
show page titles and only live pages)."""

from typing import Any

from django.core.cache import cache

GENERATION_KEY = "cms:footer:generation"
FOOTER_CACHE_SECONDS = 3600


def _generation() -> int:
    return int(cache.get_or_set(GENERATION_KEY, 1, None) or 1)


def footer_cache_key(site_id: int, language: str) -> str:
    return f"cms:footer:{_generation()}:{site_id}:{language}"


def drop_footer_cache(**kwargs: Any) -> None:
    """Start a new generation of cached footers."""
    cache.set(GENERATION_KEY, _generation() + 1, None)
