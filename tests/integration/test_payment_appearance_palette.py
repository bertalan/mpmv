"""THM.31: the payment page hands Stripe the site palette, with the same fallback rule as the
palette stylesheet (spec 14 §14.12). Cross-app (orders, payments, themes), so it lives here: the
app contracts forbid payments tests from importing themes."""

from typing import Any

import pytest
from django.test import Client
from wagtail.models import Site

from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.integration.test_order_payment import stripe  # noqa: F401 (fixture)
from apps.payments.tests.integration.test_payment_page_appearance import appearance, page
from apps.themes.models import BrandPalette
from apps.themes.services import clear_caches
from apps.themes.tests.factories import ThemeSettingsFactory

pytestmark = pytest.mark.django_db
RIDE_RED = "#C40417"


def choose_palette(slug: str, **overrides: str) -> BrandPalette:
    palette = BrandPalette.objects.get(slug=slug)
    if overrides:  # a stored row that no longer passes (written directly, bypassing `clean`)
        palette.pk = None
        palette.slug = slug + "-stale"
        for role, value in overrides.items():
            setattr(palette, role, value)
        palette.save()
    site = Site.objects.get(is_default_site=True)
    ThemeSettingsFactory.create(site=site, theme="ride", palette=palette)
    clear_caches()
    return palette


@pytest.mark.parametrize("slug", ["blue", "petrol"])
def test_the_payment_page_carries_the_site_palette(
    client: Client,
    settings: Any,
    stripe: FakeStripeClient,  # noqa: F811
    slug: str,
) -> None:
    stored = choose_palette(slug)
    value = appearance(page(client, settings, "ride", stripe))
    assert value["variables"]["colorPrimary"] == stored.primary != RIDE_RED
    assert value["rules"][".Input"]["border"] == f"1px solid {stored.control_border}"
    assert value["rules"][".Input:focus"]["outline"] == f"2px solid {stored.focus}"


def test_a_failing_stored_palette_falls_back_to_the_theme_colours(
    client: Client,
    settings: Any,
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    stored = choose_palette("blue", primary="#FF6B6B")  # white text on it fails AA
    assert stored.violations()
    value = appearance(page(client, settings, "ride", stripe))
    assert value["variables"]["colorPrimary"] == RIDE_RED
