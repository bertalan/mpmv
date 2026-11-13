"""SEO.6: the Manager keeps its CSRF-protected language switcher while the page cache is on."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import caches

from .test_manager_fixes import (  # noqa: F401  (re-run main's own tests with the cache on)
    test_every_form_in_the_manager_shell_carries_a_csrf_token,
    test_the_language_switcher_posts_through_a_real_csrf_check,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def page_cache_on(settings: Any) -> Iterator[None]:
    settings.PAGE_CACHE_ENABLED = True
    caches["pagecache"].clear()
    yield
    caches["pagecache"].clear()
