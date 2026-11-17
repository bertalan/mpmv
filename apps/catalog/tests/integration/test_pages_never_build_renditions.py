"""CAT.4 follow-up: a page render never creates a rendition (the job
`catalog.render_product_image` does); a missing rendition falls back to the original image."""

import re

import pytest
from django.db import connection
from django.template import Context, Template
from django.test.utils import CaptureQueriesContext

from apps.catalog import media
from apps.catalog.tests.factories import ProductFactory, ProductImageFactory
from apps.core import images
from apps.core.models import MediaImage, MediaImageRendition
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db

TAG = '{% load responsive_images %}{% responsive_image image "content" alt="A lake" %}'


def _photo() -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        file__filename="lake.png", file__width=2000, file__height=1500
    )
    return image


def _render(image: MediaImage) -> str:
    return Template(TAG).render(Context({"image": image}))


def test_a_page_with_no_renditions_creates_none_and_shows_the_original() -> None:
    image = _photo()
    before = MediaImageRendition.objects.count()

    html = _render(image)

    assert MediaImageRendition.objects.count() == before == 0
    assert "<source" not in html
    assert html.startswith("<picture>")
    assert f'src="{image.file.url}"' in html
    assert 'width="2000"' in html
    assert 'height="1500"' in html
    assert 'alt="A lake"' in html
    assert 'loading="lazy"' in html


def test_with_renditions_present_the_picture_has_sources_and_srcset() -> None:
    image = _photo()
    media.render_renditions(image.pk)
    before = MediaImageRendition.objects.count()
    image = MediaImage.objects.get(pk=image.pk)

    html = _render(image)

    assert MediaImageRendition.objects.count() == before
    assert re.findall(r'<source [^>]*type="([^"]+)"', html) == ["image/avif", "image/webp"]
    assert re.search(r'<img [^>]*srcset="\S+\.jpg \d+w', html)
    assert image.file.url not in html


def test_only_the_formats_that_exist_get_a_source() -> None:
    image = _photo()
    image.get_renditions(*images.picture_specs("content", formats=("webp", "jpeg")))
    image = MediaImage.objects.get(pk=image.pk)

    html = _render(image)

    assert re.findall(r'<source [^>]*type="([^"]+)"', html) == ["image/webp"]
    assert re.search(r'<img [^>]*srcset="\S+\.jpg', html)


def test_without_the_fallback_format_the_original_is_shown() -> None:
    image = _photo()
    image.get_renditions(*images.picture_specs("content", formats=("webp",)))
    image = MediaImage.objects.get(pk=image.pk)

    html = _render(image)

    assert "<source" not in html
    assert f'src="{image.file.url}"' in html


def test_the_page_query_count_does_not_grow_with_the_images() -> None:
    from apps.catalog import product_pages

    def queries(count: int) -> int:
        product = ProductFactory.create()
        for _ in range(count):
            ProductImageFactory.create(product=product, image=_photo())
        template = Template(
            "{% load responsive_images %}{% for item in gallery %}"
            '{% responsive_image item.image "content" alt=item.alt %}{% endfor %}'
        )
        with CaptureQueriesContext(connection) as captured:
            shown = product_pages.gallery(product, "en")
            template.render(Context({"gallery": shown}))
        assert len(shown) == count
        return len(captured)

    assert queries(6) == queries(2)
