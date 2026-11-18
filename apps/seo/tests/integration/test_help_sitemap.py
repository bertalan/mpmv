"""ARC.37: the seller guide pages are in the sitemap, per language, with their own file."""

import pytest
from django.conf import settings
from django.test import RequestFactory

from apps.cms import engine, help_guide, help_sync
from apps.seo import sitemaps

pytestmark = pytest.mark.django_db

LANGS = tuple(code for code, _name in settings.LANGUAGES)


def test_the_sitemap_lists_the_seller_pages_with_their_own_file() -> None:
    help_sync.sync_help()
    request = RequestFactory().get("/")
    for lang in LANGS:
        urls = [entry.loc for entry in sitemaps.cms_entries(lang, request)]
        for section in engine.GUIDE_SECTIONS["vendor"]:
            path = help_guide.section_path(section, lang, "vendor")
            assert any(url.endswith(path) for url in urls), (lang, section)
