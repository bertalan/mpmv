"""SEO.15 follow-up: the search vendors group filters, orders and cuts in the database.

The group shows and matches only the public name of a vendor (a natural person's legal name is
never shown). The results equal those of the Python reference rule, and the number of queries does
not grow with the number of vendors."""

from typing import Any

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.catalog import search_page
from apps.vendors.models import Vendor
from apps.vendors.seller_name import public_name
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def make(name: str, **kwargs: Any) -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE, display_name=name, **kwargs)


def reference(query: str, limit: int) -> list[tuple[int, str]]:
    """The previous implementation: every vendor checked in Python."""
    rows = [
        (v.pk, shown)
        for v in Vendor.objects.filter(status=Vendor.Status.ACTIVE).order_by("display_name", "pk")
        if (shown := public_name(v)) and query.casefold() in shown.casefold()
    ]
    return rows[:limit]


def shown(query: str, limit: int = 5) -> list[tuple[int, str]]:
    group = search_page.vendors_group(RequestFactory().get("/"), query, "en", limit)
    return [(item.ref or 0, item.title) for item in group.items]


def test_results_equal_the_python_rule_for_every_kind_of_vendor() -> None:
    make("Zeta Ceramics", legal_form=Vendor.LegalForm.SRL, legal_name="Zeta Ceramics Srl")
    make(
        "Zeta Mario Rossi",
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        legal_name="Mario Rossi",
        first_name="Mario",
        last_name="Rossi",
    )
    make(
        "Zeta Atelier",  # a sole trader with a shop name: shown
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        legal_name="Anna Bianchi",
        first_name="Anna",
        last_name="Bianchi",
    )
    make(
        "Bianchi  Anna",  # only the legal name, in the other order: hidden
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
        legal_name="Anna Bianchi",
        first_name="Anna",
        last_name="Bianchi",
    )
    make("Zeta Unspecified", legal_form="", legal_name="Zeta Unspecified")
    make("Other Shop")
    for query in ("zeta", "ZETA", "bianchi", "rossi", "other", "nothing"):
        assert shown(query) == reference(query, 5), query
    names = [name for _, name in shown("bianchi")]
    assert names == []  # the person's own name is never matched or shown


def test_a_hidden_vendor_does_not_use_up_the_limit() -> None:
    for _ in range(4):
        make(
            "Aaa Mario Rossi",
            legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
            legal_name="Aaa Mario Rossi",
        )
    make("Aaa Shop", legal_form=Vendor.LegalForm.SRL)
    group = search_page.vendors_group(RequestFactory().get("/"), "aaa", "en", 1)
    assert [item.title for item in group.items] == ["Aaa Shop"]
    assert not group.more


def test_the_query_count_is_the_same_for_three_and_thirty_vendors() -> None:
    def count_queries() -> int:
        with CaptureQueriesContext(connection) as queries:
            shown("shop")
        return len(queries)

    for number in range(3):
        make(f"Shop {number:02d}", legal_form=Vendor.LegalForm.SRL)
    few = count_queries()
    for number in range(3, 30):
        make(f"Shop {number:02d}", legal_form=Vendor.LegalForm.SRL)
    assert count_queries() == few
