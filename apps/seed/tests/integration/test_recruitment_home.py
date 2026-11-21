"""CMS.19: the demo home is a vendor-recruitment landing with English example content, and the
home of every other language is an untranslated alias that shows it without `hreflang`.

Languages come from `configured_languages()`: the module also runs with `MPMV_LANGUAGES=it,de`,
where the English text is the primary home's placeholder copy (ARC.38).
"""

import re

import pytest
from django.apps import apps
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale, Site

from apps.cms.models import HomePage, StandardPage
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.seed import data
from apps.seed.services import TEST_CONTEXT, seed
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db

HREFLANG = re.compile(r'hreflang="([^"]+)"')
SECTIONS = (
    data.LANDING_HERO[0],
    "Why sell here",
    "How it works",
    "Example fee, set by the operator",
    data.LANDING_QUOTES_HEADING,
    "Vendor A",
    data.LANDING_FAQ_HEADING,
    data.LANDING_CTA[0],
)


@pytest.fixture
def indexed(demo_seed: ChangeSet) -> None:
    """The site allows indexing: only then does a page print `hreflang` at all."""
    SeoSettings = apps.get_model("seo", "SeoSettings")
    SeoSettings.objects.update_or_create(
        site=Site.objects.get(is_default_site=True), defaults={"allow_indexing": True}
    )
    cache.clear()


def source_home() -> HomePage:
    home: HomePage = HomePage.objects.get(locale=Locale.get_default(), depth=2)
    return home


def test_the_seed_builds_the_landing_from_existing_blocks(demo_seed: ChangeSet) -> None:
    kinds = [block.block_type for block in source_home().body]
    assert kinds == ["hero", *["paragraph"] * 4, "strip", "faq", "cta", "cta"]


def test_the_home_shows_every_section_and_links_to_apply_and_the_shop(
    demo_seed: ChangeSet, client: Client
) -> None:
    html = client.get("/").content.decode()
    for text in SECTIONS:
        assert text in html, text
    assert f'{reverse("vendors:apply")}"' in html
    shop = StandardPage.objects.get(locale=Locale.get_default(), slug=data.SHOP.slug["en"])
    assert f'href="{shop.url}"' in html
    assert client.get(shop.url).status_code == 200


def test_a_second_run_changes_nothing(demo_seed: ChangeSet) -> None:
    before = [
        (page.pk, page.alias_of_id, page.body.get_prep_value())
        for page in HomePage.objects.order_by("pk")
    ]
    assert seed(ctx=TEST_CONTEXT).is_empty
    after = [
        (page.pk, page.alias_of_id, page.body.get_prep_value())
        for page in HomePage.objects.order_by("pk")
    ]
    assert after == before


def test_another_language_shows_the_english_landing_without_its_hreflang(
    indexed: None, client: Client
) -> None:
    other = other_language()
    copy = HomePage.objects.get(locale__language_code=other, depth=2)
    assert copy.alias_of_id == source_home().pk

    html = client.get(f"{language_prefix(other)}/").content.decode()
    assert data.LANDING_HERO[0] in html
    assert other not in HREFLANG.findall(html)
    assert other not in HREFLANG.findall(client.get("/").content.decode())
    assert primary_language() in HREFLANG.findall(html)
