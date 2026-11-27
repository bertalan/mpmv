"""ING.8: the VAT rate of a supplier price comes from the tax engine, not from ingest."""

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.utils import timezone

from apps.catalog.tests.factories import ProductFactory
from apps.core.validity import VatRateUnavailable
from apps.ingest import sync

pytestmark = pytest.mark.django_db


@pytest.mark.usefixtures("consumer_prices")
def test_the_resolver_returns_the_engine_rate_as_a_fraction() -> None:
    product = ProductFactory.create()
    rate = sync.vat_rate_resolver()(product, timezone.now())
    assert rate == Decimal("0.22")  # the IT standard rate the fixture seeds (vendor country IT)


def test_the_stale_age_is_a_named_setting() -> None:
    assert sync.stale_max_age() == dt.timedelta(hours=settings.INGEST_STALE_MAX_AGE_HOURS)


def test_without_an_operator_or_rate_the_resolver_fails_closed() -> None:
    product = ProductFactory.create()
    with pytest.raises(VatRateUnavailable):
        sync.vat_rate_resolver()(product, timezone.now())
