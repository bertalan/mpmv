"""CMS.8: image credits on pages, the review list and the chooser (spec 10 §10.2).

Where the licence requires attribution, the credit goes in the image's `<figcaption>`; images
with no caption area (heroes, banners) are credited in the page's "Image credits" disclosure.
CMS.2's hero block declares `credit_in_disclosure = True` in its Meta; a page type lists its
own uncaptioned image fields in `credit_disclosure_image_fields`.
"""

from typing import Any

import pytest
from django.test import Client
from wagtail import blocks, hooks
from wagtail.blocks import StreamValue
from wagtail.images.blocks import ImageChooserBlock

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import StandardPage
from apps.cms.services import SeedReport, disclosure_images_in_stream, page_image_credits
from apps.cms.tests.factories import StandardPageFactory
from apps.core.models import ImageLicence, MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


def _cc_by(author: str, name: str = "by") -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        licence=ImageLicence.objects.get(code="CC-BY-4.0"),
        author=author,
        source_url="https://example.com/source",
        file__filename=f"{name}.png",
    )
    return image


def _cc0(author: str) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        licence=ImageLicence.objects.get(code="CC0-1.0"),
        author=author,
        source_url="https://example.com/source",
    )
    return image


def _published(**kwargs: Any) -> StandardPage:
    page: StandardPage = StandardPageFactory.create(live=False, **kwargs)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _html(client: Client, page: StandardPage) -> str:
    return client.get(page.url).content.decode()


def test_a_cc_by_image_shows_its_credit_in_the_caption(seeded: SeedReport, client: Client) -> None:
    page = _published(
        slug="lake",
        image=_cc_by("Ada Lovelace"),
        image_alt_text="A lake",
        body=[("figure", {"image": _cc_by("Grace Hopper"), "alt_text": "Hills", "caption": ""})],
    )
    html = _html(client, page)

    assert html.count('class="image-credit"') == 2
    assert "Ada Lovelace" in html
    assert "Grace Hopper" in html
    assert 'href="https://creativecommons.org/licenses/by/4.0/"' in html
    assert 'class="image-credits"' not in html  # captioned images are not in the disclosure


def test_a_cc0_image_shows_no_credit(seeded: SeedReport, client: Client) -> None:
    page = _published(slug="meadow", image=_cc0("Bob Example"), image_alt_text="A meadow")
    html = _html(client, page)

    assert 'class="image-credit"' not in html
    assert "Bob Example" not in html
    assert "<figcaption>" not in html


def test_a_cc_by_hero_is_credited_in_the_page_credits_disclosure(
    seeded: SeedReport, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The main image stands in for a hero: a field the page renders without a caption area.
    monkeypatch.setattr(StandardPage, "credit_disclosure_image_fields", ("image",), raising=False)
    page = _published(slug="hero", image=_cc_by("Ada Lovelace"), image_alt_text="A hero")

    credits = page_image_credits(page)
    html = _html(client, page)

    assert [credit.text for credit in credits] == ["Ada Lovelace"]
    assert '<details class="image-credits">' in html
    assert "Image credits" in html


def test_a_cc0_hero_adds_nothing_to_the_disclosure(
    seeded: SeedReport, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(StandardPage, "credit_disclosure_image_fields", ("image",), raising=False)
    page = _published(slug="hero-cc0", image=_cc0("Bob"), image_alt_text="A hero")

    assert page_image_credits(page) == []
    assert 'class="image-credits"' not in _html(client, page)


class _HeroLikeBlock(blocks.StructBlock):  # type: ignore[misc]
    image = ImageChooserBlock()

    class Meta:
        credit_in_disclosure = True


def test_blocks_marked_for_the_disclosure_are_collected_from_streamfields(
    seeded: SeedReport,
) -> None:
    hero, figure = _cc_by("Ada", "hero"), _cc_by("Grace", "figure")
    stream = blocks.StreamBlock(
        [
            ("hero", _HeroLikeBlock()),
            ("figure", blocks.StructBlock([("image", ImageChooserBlock())])),
        ]
    )
    value = StreamValue(stream, [("hero", {"image": hero}), ("figure", {"image": figure})])

    assert list(disclosure_images_in_stream(value)) == [hero]


def test_the_review_list_shows_images_without_metadata(client: Client) -> None:
    legacy = MediaImageFactory.create(title="Legacy lake", licence=None, author="")
    MediaImageFactory.create(title="Classified hills")
    verified_login(client, UserFactory.create(superuser=True))

    response = client.get("/admin/images/review/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "Legacy lake" in html
    assert "Classified hills" not in html
    assert f"/admin/images/{legacy.pk}/" in html


def test_unclassified_images_cannot_be_chosen_for_new_content() -> None:
    legacy = MediaImageFactory.create(licence=None, author="")
    classified = MediaImageFactory.create()
    images = MediaImage.objects.all()
    for hook in hooks.get_hooks("construct_image_chooser_queryset"):
        images = hook(images, None)

    assert classified in images
    assert legacy not in images
