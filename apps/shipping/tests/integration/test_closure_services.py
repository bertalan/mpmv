"""VEN.20 slice 2: the shipping side of vendor closures (spec 04, cases 10 and 12)."""

import datetime as dt
from unittest import mock

import pytest
from django.template import Context, Template
from django.utils import translation

from apps.shipping import services
from apps.shipping.engine import ClosedPeriod, ClosureNotice
from apps.vendors.tests.factories import VendorClosureFactory, VendorFactory

pytestmark = pytest.mark.django_db

D = dt.date
TODAY = D(2027, 7, 22)


def test_closed_periods_per_vendor_with_one_query_and_empty_for_none() -> None:
    a, b, c = VendorFactory.create_batch(3)
    VendorClosureFactory.create(vendor=a, start_date=D(2027, 8, 1), end_date=D(2027, 8, 15))
    VendorClosureFactory.create(
        vendor=a, start_date=D(2026, 12, 24), end_date=D(2027, 1, 6), repeats_yearly=True
    )
    VendorClosureFactory.create(vendor=b, start_date=D(2027, 9, 1), end_date=D(2027, 9, 2))
    result = services.vendor_closed_periods([a.pk, b.pk, c.pk], D(2027, 7, 22), D(2028, 1, 20))
    assert result[a.pk] == (
        ClosedPeriod(D(2027, 8, 1), D(2027, 8, 15)),
        ClosedPeriod(D(2027, 12, 24), D(2028, 1, 6)),
    )
    assert result[b.pk] == (ClosedPeriod(D(2027, 9, 1), D(2027, 9, 2)),)
    assert result[c.pk] == ()


def test_case_10_the_notice_follows_each_closures_own_lead_time() -> None:
    near, far, other = VendorFactory.create_batch(3)
    VendorClosureFactory.create(
        vendor=near, start_date=D(2027, 8, 1), end_date=D(2027, 8, 31), notice_days=14
    )
    VendorClosureFactory.create(
        vendor=far, start_date=D(2027, 8, 11), end_date=D(2027, 8, 31), notice_days=14
    )
    assert services.closure_notice(near.pk, TODAY) == ClosureNotice(
        D(2027, 8, 1), D(2027, 8, 31), D(2027, 9, 1)
    )
    assert services.closure_notice(far.pk, TODAY) is None
    assert services.closure_notice(other.pk, TODAY) is None


def _render(vendor_id: int, language: str = "en") -> str:
    template = Template("{% load dispatch %}{% closure_notice vendor_id %}")
    with translation.override(language):
        return template.render(Context({"vendor_id": vendor_id}))


def test_the_tag_renders_a_neutral_region_in_every_language() -> None:
    vendor = VendorFactory.create()
    VendorClosureFactory.create(
        vendor=vendor, start_date=D(2027, 8, 1), end_date=D(2027, 8, 31), notice_days=14
    )
    texts: set[str] = set()
    with mock.patch("apps.shipping.templatetags.dispatch.business_today", return_value=TODAY):
        for language in ("en", "it", "de", "fr", "es"):
            html = _render(vendor.pk, language)
            assert 'role="region"' in html
            assert "<h2" in html
            texts.add(html)
        english = _render(vendor.pk)
    assert len(texts) == 5
    with mock.patch("apps.shipping.templatetags.dispatch.business_today", return_value=TODAY):
        assert "chiuso dal" in _render(vendor.pk, "it")
        assert "geschlossen" in _render(vendor.pk, "de")
        assert "fermée" in _render(vendor.pk, "fr")
        assert "cerrada" in _render(vendor.pk, "es")
    assert "closed from" in english
    assert "2027" in english


def test_the_tag_renders_nothing_without_a_notice() -> None:
    vendor = VendorFactory.create()
    with mock.patch("apps.shipping.templatetags.dispatch.business_today", return_value=TODAY):
        assert _render(vendor.pk).strip() == ""
        assert _render(0).strip() == ""


def test_a_one_day_closure_reads_as_one_day() -> None:
    vendor = VendorFactory.create()
    VendorClosureFactory.create(
        vendor=vendor, start_date=D(2027, 8, 2), end_date=D(2027, 8, 2), notice_days=14
    )
    with mock.patch("apps.shipping.templatetags.dispatch.business_today", return_value=TODAY):
        assert "closed on" in _render(vendor.pk)
