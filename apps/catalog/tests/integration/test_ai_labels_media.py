"""CMP.8 follow-up (#1097): a product page also honours the AI origin of the library image.

One image file, one truth: an image flagged on the library is labelled on a product page even
when the product's own `ProductImage.ai_origin` was left at `none`.
"""

import pytest
from django.apps import apps as django_apps
from django.test import Client
from wagtail.models import Site

from apps.catalog import ai_labels, media
from apps.catalog.tests.integration.test_ai_labels import CTX, add_image, live
from apps.core.i18n import primary_language
from tests.i18n import product_url

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def test_the_library_flag_labels_the_product_page(client: Client) -> None:
    product = live("mug")
    row = add_image(product, "red")
    media.set_media_ai_origin(row.image, ai_labels.AI_GENERATED, ctx=CTX)
    html = client.get(product_url(primary_language(), "mug")).content.decode()
    assert 'data-ai-origin="generated"' in html


def test_the_stronger_origin_wins() -> None:
    assert ai_labels.effective("none", "none") == "none"
    assert ai_labels.effective("edited", "none") == "edited"
    assert ai_labels.effective("edited", "generated") == "generated"
    assert ai_labels.effective() == "none"
