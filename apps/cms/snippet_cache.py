"""Cache generation of the reusable content snippets (CMS.14), the pattern of `footer_cache`.

Any page or fragment cache that renders a `content_snippet` block (SEO.6's anonymous page cache,
a fragment cache) puts `generation()` in its key. The generation moves when a snippet is
published, unpublished or deleted, which are the only moments the live text changes; a draft
save or a submission for moderation changes nothing a visitor sees.
"""

from typing import Any

from django.core.cache import cache

GENERATION_KEY = "cms:snippet:generation"


def generation() -> int:
    return int(cache.get_or_set(GENERATION_KEY, 1, None) or 1)


def fragment_key(site_id: int, language: str) -> str:
    return f"cms:snippet:{generation()}:{site_id}:{language}"


def drop_snippet_cache(**kwargs: Any) -> None:
    """Start a new generation of cached pages and fragments that show snippets."""
    cache.set(GENERATION_KEY, generation() + 1, None)
