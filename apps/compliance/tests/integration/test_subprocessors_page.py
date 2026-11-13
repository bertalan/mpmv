"""CMP.4 (2/2): the public page lists the sub-processors (spec 11, record of processing)."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.compliance import subprocessors
from apps.core import indexing

pytestmark = pytest.mark.django_db


def test_the_page_is_public_and_lists_every_entry() -> None:
    response = Client().get(reverse("subprocessors"))
    assert response.status_code == 200
    html = response.content.decode()
    for entry in subprocessors.public_register():
        assert entry.name in html


def test_the_page_is_cacheable_and_indexable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Since SEO.8 the whole site is noindex until its SeoSettings allow indexing. compliance sits
    # below seo, so the site's switch is stood in for by the core policy hook: the page itself
    # must add no noindex of its own.
    monkeypatch.setattr(indexing, "_site_policy", lambda request: True)
    response = Client().get(reverse("subprocessors"))
    assert "max-age" in response.headers["Cache-Control"]
    assert b'content="noindex' not in response.content


def test_the_page_is_read_only() -> None:
    assert Client().post(reverse("subprocessors")).status_code == 405
