"""A page's own `noindex` field keeps it out of the sitemaps and llms.txt (THM.14 showcase)."""

from types import SimpleNamespace
from typing import Any, cast

from apps.seo.sitemaps import page_is_noindex


def test_a_page_with_its_own_noindex_is_left_out() -> None:
    assert page_is_noindex(cast(Any, SimpleNamespace(noindex=True)))


def test_a_page_without_the_field_or_with_it_off_is_listed() -> None:
    assert not page_is_noindex(cast(Any, SimpleNamespace()))
    assert not page_is_noindex(cast(Any, SimpleNamespace(noindex=False)))
