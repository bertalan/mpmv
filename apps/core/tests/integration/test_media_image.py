"""I18N.3: `MediaImage` is Wagtail's image model; its renditions are stored once per filter."""

import pytest
from django.db import IntegrityError
from wagtail.images import get_image_model

from apps.core.models import MediaImage, MediaImageRendition
from apps.core.tests.factories import MediaImageFactory, MediaImageRenditionFactory

pytestmark = pytest.mark.django_db


def test_wagtail_renditions_of_a_media_image_are_stored_once_per_filter() -> None:
    image = MediaImageFactory.create()
    assert get_image_model() is MediaImage

    first = image.get_rendition("width-32")
    again = image.get_rendition("width-32")

    assert isinstance(first, MediaImageRendition)
    assert first.pk == again.pk
    assert first.width == 32


def test_a_second_rendition_with_the_same_filter_is_refused() -> None:
    rendition = MediaImageRenditionFactory.create(filter_spec="width-20")
    with pytest.raises(IntegrityError):
        MediaImageRenditionFactory.create(image=rendition.image, filter_spec="width-20")
