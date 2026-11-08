"""CMS.2 (part 1/2): the home page built from hero, CTA, FAQ and rich text blocks.

Done when: "An editor builds the home page without code". The blocks render on the public page
under the strict CSP, a hero image keeps its alt text, a link to a page of the site follows the
reader's language, and wagtail-localize offers the block texts for translation and the hero
image for a per-locale override (spec 09 §9.2, spec 10 "Images per locale").
"""

import json
import re

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from wagtail_localize.models import OverridableSegment, StringSegment, TranslationSource

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.blocks import HeroBlock, LinkBlock
from apps.cms.models import HomePage, StandardPage
from apps.cms.services import SeedReport
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation on commit; a test transaction never commits."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _home(code: str = "en") -> HomePage:
    home: HomePage = HomePage.objects.get(locale__language_code=code)
    return home


def _about(code: str = "en") -> StandardPage:
    about: StandardPage = StandardPage.objects.get(
        locale__language_code=code, depth=_home(code).depth + 1
    )
    return about


@pytest.fixture
def banner() -> MediaImage:
    image: MediaImage = MediaImageFactory.create(title="Banner", file__filename="banner.png")
    return image


@pytest.fixture
def home(seeded: SeedReport, banner: MediaImage) -> HomePage:
    """The EN home page with every part-1 block, published."""
    page = _home()
    page.body = [
        (
            "hero",
            {
                "heading": "Handmade goods",
                "text": "From small workshops.",
                "image": banner,
                "alt_text": "Hands shaping clay",
                "links": [{"page": _about(), "url": "", "label": "Our story"}],
            },
        ),
        ("paragraph", "<p>Welcome to the shop.</p>"),
        (
            "cta",
            {
                "heading": "Sell with us",
                "text": "",
                "link": {"page": None, "url": "https://example.com/apply/", "label": "Apply"},
            },
        ),
        (
            "faq",
            {
                "heading": "Questions",
                "items": [{"question": "Do you ship abroad?", "answer": "<p>Yes.</p>"}],
            },
        ),
    ]
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_the_home_page_renders_its_blocks_in_order(client: Client, home: HomePage) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    positions = [
        html.index(text)
        for text in (
            "Handmade goods",
            "Welcome to the shop.",
            "Sell with us",
            "Do you ship abroad?",
        )
    ]
    assert positions == sorted(positions)
    assert re.search(r'<img[^>]+alt="Hands shaping clay"', html)
    assert f'href="{_about().url}"' in html
    assert ">Our story</a>" in html
    assert '<a href="https://example.com/apply/"' in html
    assert "<details" in html
    assert "style=" not in html
    ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert [json.loads(data)["@type"] for data in ld] == ["FAQPage"]


def test_a_hero_image_needs_alt_text(banner: MediaImage) -> None:
    block = HeroBlock()
    value = block.to_python(
        {"heading": "Welcome", "text": "", "image": banner.pk, "alt_text": "", "links": []}
    )
    with pytest.raises(ValidationError):
        block.clean(value)


def test_a_link_cannot_point_to_a_page_and_a_url(seeded: SeedReport) -> None:
    block = LinkBlock()
    value = block.to_python({"page": _about().pk, "url": "https://example.com/", "label": "About"})
    with pytest.raises(ValidationError):
        block.clean(value)


def test_a_link_to_a_page_points_to_its_url(seeded: SeedReport) -> None:
    block = LinkBlock()
    value = block.clean(block.to_python({"page": _about().pk, "url": "", "label": "About"}))
    assert value.href() == _about().url


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


def test_block_texts_are_translatable_and_the_hero_image_is_overridable(home: HomePage) -> None:
    source, _created = TranslationSource.get_or_create_from_instance(home)
    strings = set(
        StringSegment.objects.filter(source=source).values_list("string__data", flat=True)
    )
    assert {
        "Handmade goods",
        "From small workshops.",
        "Hands shaping clay",
        "Our story",
        "Sell with us",
        "Apply",
        "Questions",
        "Do you ship abroad?",
    } <= strings
    hero_id = next(block.id for block in home.body if block.block_type == "hero")
    paths = set(
        OverridableSegment.objects.filter(source=source).values_list("context__path", flat=True)
    )
    assert f"body.{hero_id}.image" in paths


def test_a_link_to_a_page_leads_to_its_translation(
    client: Client, home: HomePage, editor: User
) -> None:
    """The home pages are translations of each other; the IT home links to the IT About page."""
    italian_about = _about("it")
    assert italian_about.translation_key == _about().translation_key
    italian_home = _home("it")
    assert italian_home.translation_key == home.translation_key

    italian_home.body = home.body.raw_data
    italian_home.save_revision().publish()

    html = client.get(italian_home.url).content.decode()
    assert f'href="{italian_about.url}"' in html
    assert italian_about.url != _about().url


def test_the_editor_offers_the_blocks_on_the_home_page(
    client: Client, home: HomePage, editor: User
) -> None:
    verified_login(client, editor)
    response = client.get(reverse("wagtailadmin_pages:edit", args=(home.pk,)))
    assert response.status_code == 200
    html = response.content.decode()
    for label in ("Hero", "Call to action", "FAQ", "Rich text"):
        assert label in html, label
