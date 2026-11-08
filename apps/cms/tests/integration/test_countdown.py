"""CMS.9: the launch countdown block on CMS pages (spec 10 §10.2).

Done when (server side): "Without JavaScript the block shows the absolute date and the CTA;
after the target the server shows the expired text and link; no `aria-live` on the counter".
The target is entered in the operator's time zone and stored in UTC; the page renders the
absolute date in that zone with its name. The page's Cache-Control never lets a cache keep an
active countdown past its target. The CTA and the expired link default to the chosen product or
category.
"""

import datetime as dt
import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone
from django.utils.cache import get_max_age

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Category, Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.blocks import LaunchCountdownBlock
from apps.cms.models import HomePage, StandardPage
from apps.cms.services import SeedReport
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


def _home() -> HomePage:
    home: HomePage = HomePage.objects.get(locale__language_code="en")
    return home


def _countdown(target: dt.datetime, **fields: Any) -> tuple[str, dict[str, Any]]:
    value: dict[str, Any] = {
        "target": target,
        "heading": "The touring jacket",
        "body": "Made in small batches.",
        "product": None,
        "category": None,
        "cta_label": "Get a reminder",
        "cta_link": "https://example.com/remind/",
        "expired_text": "",
        "expired_link": "https://example.com/shop/jacket/",
    }
    value.update(fields)
    return ("countdown", value)


def _publish(page: Any, *body: tuple[str, Any]) -> Any:
    page.body = list(body)
    page.save_revision().publish()
    return Client().get(page.url)


def _block_html(html: str) -> str:
    found = re.search(r'<div class="launch-countdown.*?\n</div>', html, re.DOTALL)
    assert found, "no launch countdown on the page"
    return found.group(0)


@pytest.fixture
def product() -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    item: Product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=item, language="en", title="Touring jacket", slug="touring-jacket-en"
    )
    return item


@pytest.fixture
def category() -> Category:
    parent = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=parent, language="en", name="Clothing", slug="clothing", status=PUBLISHED
    )
    child: Category = CategoryFactory.create(parent=parent)
    CategoryTranslationFactory.create(
        category=child, language="en", name="Jackets", slug="jackets", status=PUBLISHED
    )
    return child


def test_the_block_is_offered_in_home_and_standard_page_bodies() -> None:
    for model in (HomePage, StandardPage):
        assert isinstance(
            model.body.field.stream_block.child_blocks["countdown"], LaunchCountdownBlock
        )


def test_before_the_target_the_page_shows_the_absolute_date_and_the_cta(
    seeded: SeedReport,
) -> None:
    target = dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC)  # 10:00 in Rome (CET)
    response = _publish(_home(), _countdown(target))
    block = _block_html(response.content.decode())

    assert "data-countdown" in block
    assert 'data-target="2030-12-01T09:00:00Z"' in block
    time = re.search(r"<time datetime=\"([^\"]+)\">([^<]+)</time>", block)
    assert time, block
    assert dt.datetime.fromisoformat(time.group(1)) == target
    assert "10:00" in time.group(2) or "10 a.m." in time.group(2)
    assert "CET" in time.group(2)
    assert '<a class="button" href="https://example.com/remind/">Get a reminder</a>' in block
    assert "Made in small batches." in block
    # The counter and the pause button wait for the module; the expired part is hidden.
    assert re.search(r"<button[^>]*data-countdown-pause[^>]*hidden", block)
    assert re.search(r"<[^>]*data-countdown-expired[^>]*hidden", block)
    assert "Available now" in block
    assert "aria-live" not in block


def test_from_the_target_on_the_server_renders_the_expired_text_and_link(
    seeded: SeedReport,
) -> None:
    target = timezone.now() - dt.timedelta(minutes=1)
    response = _publish(_home(), _countdown(target, expired_text="On sale now"))
    block = _block_html(response.content.decode())

    assert "data-countdown" not in block.replace("data-countdown-expired", "")
    assert "<time" not in block
    assert "Get a reminder" not in block
    assert "On sale now" in block
    assert 'href="https://example.com/shop/jacket/"' in block
    assert not re.search(r"<[^>]*data-countdown-expired[^>]*hidden", block)
    assert "launch-countdown--expired" in block


def test_the_page_cache_never_keeps_an_active_countdown_past_its_target(
    seeded: SeedReport,
) -> None:
    target = timezone.now() + dt.timedelta(minutes=10)
    later = timezone.now() + dt.timedelta(days=3)
    response = _publish(_home(), _countdown(later), _countdown(target))

    max_age = get_max_age(response)
    assert max_age is not None
    assert 0 < max_age <= 600


def test_an_expired_countdown_does_not_cap_the_cache(seeded: SeedReport) -> None:
    response = _publish(_home(), _countdown(timezone.now() - dt.timedelta(minutes=1)))
    assert get_max_age(response) is None


def test_a_logged_in_page_with_a_countdown_is_cached_privately_only(
    seeded: SeedReport, client: Client
) -> None:
    page = _home()
    page.body = [_countdown(timezone.now() + dt.timedelta(hours=1))]
    page.save_revision().publish()
    client.force_login(UserFactory.create())
    response = client.get(page.url)
    assert "private" in response["Cache-Control"]


def test_the_cta_and_the_expired_link_default_to_the_category_or_the_product(
    seeded: SeedReport, product: Product, category: Category
) -> None:
    future = timezone.now() + dt.timedelta(days=1)
    past = timezone.now() - dt.timedelta(days=1)
    upcoming = _block_html(
        _publish(_home(), _countdown(future, category=category, cta_link="")).content.decode()
    )
    assert 'href="/product-category/clothing/jackets/"' in upcoming

    expired = _block_html(
        _publish(_home(), _countdown(past, product=product, expired_link="")).content.decode()
    )
    assert 'href="/product/touring-jacket-en/"' in expired


def test_a_countdown_links_to_a_product_or_a_category_not_both(
    seeded: SeedReport, product: Product, category: Category
) -> None:
    block = LaunchCountdownBlock()
    value = block.to_python(
        {
            "target": "2030-12-01T09:00:00+00:00",
            "heading": "Launch",
            "product": product.pk,
            "category": category.pk,
            "cta_label": "",
        }
    )
    with pytest.raises(ValidationError):
        block.clean(value)


def test_a_cta_label_needs_a_link(seeded: SeedReport) -> None:
    block = LaunchCountdownBlock()
    value = block.to_python(
        {"target": "2030-12-01T09:00:00+00:00", "heading": "Launch", "cta_label": "Remind me"}
    )
    with pytest.raises(ValidationError):
        block.clean(value)


def test_the_target_is_entered_in_the_operator_time_zone_and_stored_in_utc() -> None:
    target_block = LaunchCountdownBlock().child_blocks["target"]
    # 1 December: Rome is on CET (UTC+1); 1 July: on CEST (UTC+2).
    assert target_block.clean("2030-12-01 10:00") == dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC)
    assert target_block.clean("2030-07-01 10:00") == dt.datetime(2030, 7, 1, 8, 0, tzinfo=dt.UTC)
    # The editor sees the stored value again in the operator's time zone.
    shown = target_block.get_form_state(dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC))
    assert shown.startswith("2030-12-01 10:00")
    stored = target_block.get_prep_value(dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC))
    assert dt.datetime.fromisoformat(stored) == dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC)
