"""ARC.10 + VEN.35 + ORD.17: the demo seed publishes every vendor's sales terms and the customer
Terms of sale page's onerous clauses, so the checkout legal step runs on realistic data."""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale

from apps.cart.session import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.cms.models import LegalPage, LegalPageVersion
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.seed import data
from apps.seed.services import TEST_CONTEXT, reset, seed, seed_vendors
from apps.vendors.models import SalesTermsInForce, SalesTermsVersion
from apps.vendors.terms_services import version_in_force
from tests.i18n import other_language, translated

pytestmark = pytest.mark.django_db


def clauses_in(language: str) -> list[dict[str, str]]:
    return data.PLATFORM_TERMS_CLAUSES.get(language) or data.PLATFORM_TERMS_CLAUSES["en"]


def shown(article: str, titles: dict[str, str]) -> str:
    """A clause as the checkout lists it, in the primary language (English text as fallback)."""
    language = primary_language()
    title = titles.get(language) or titles["en"]
    template = translated("Article %(article)s: %(title)s", language)
    return template % {"article": article, "title": title}


def test_every_demo_vendor_has_terms_in_force_with_the_expected_clauses(
    demo_seed: ChangeSet,
) -> None:
    for vendor, spec in zip(seed_vendors(), data.VENDORS, strict=True):
        expected = data.SALES_TERMS[spec.letter]
        version = version_in_force(vendor)
        assert version is not None, vendor.display_name
        assert version.number == 1
        assert version.clauses == expected.values(primary_language())["clauses"]
        assert version.clauses  # a version without clauses cannot be published
        assert str(version.governing_law) == expected.governing_law
        assert version.b2b_court == expected.b2b_court
        assert version.b2b_court == vendor.registered_city
        assert version.delivery_days == expected.delivery_days
        assert "Condizioni generali" in version.body
        assert "General terms" in version.body
    assert SalesTermsVersion.objects.count() == len(data.VENDORS)
    laws = {str(v.governing_law) for v in SalesTermsVersion.objects.all()}
    assert laws == {"IT", "DE", "FR"}


def test_the_customer_terms_pages_list_onerous_clauses_in_both_languages(
    demo_seed: ChangeSet,
) -> None:
    # The seed has Italian text next to its English one: a page exists in every configured
    # language that has its own clauses, and the primary page holds the fallback otherwise.
    languages = [primary_language()]
    if other_language() in data.PLATFORM_TERMS_CLAUSES:
        languages.append(other_language())
    for language in languages:
        clauses = clauses_in(language)
        locale = Locale.objects.get(language_code=language)
        page = LegalPage.objects.get(kind="terms", locale=locale, slug__startswith="demo")
        assert page.live
        assert page.onerous_clauses == clauses
        latest = page.versions.order_by("-number").first()
        assert latest is not None
        assert latest.onerous_clauses == clauses


def test_a_second_seed_is_a_no_op_and_reset_keeps_the_terms(demo_seed: ChangeSet) -> None:
    versions = SalesTermsVersion.objects.count()
    page_versions = LegalPageVersion.objects.count()
    assert not seed(ctx=TEST_CONTEXT).changes
    assert SalesTermsVersion.objects.count() == versions
    assert LegalPageVersion.objects.count() == page_versions
    reset(ctx=TEST_CONTEXT)
    assert SalesTermsVersion.objects.count() == versions
    assert SalesTermsInForce.objects.count() == len(data.VENDORS)
    seed(ctx=TEST_CONTEXT)
    assert SalesTermsVersion.objects.count() == versions
    assert set(SalesTermsVersion.objects.values_list("number", flat=True)) == {1}


def test_a_dry_run_reports_the_terms_and_writes_nothing(db: None) -> None:
    out = io.StringIO()
    call_command("seed", "--dry-run", stdout=out)
    assert SalesTermsVersion.objects.count() == 0
    assert "salestermsversion" in out.getvalue().lower()


def test_a_business_buyer_sees_box_2_with_the_clauses_of_the_italian_parties(
    demo_seed: ChangeSet, client: Client, settings: Any
) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    cart = CartFactory.create()
    session = client.session
    session[GUEST_CART_SESSION_KEY] = cart.session_key
    session.save()
    vendors = seed_vendors()
    for vendor in (vendors[0], vendors[3]):  # A is Italian, D is German
        product = Product.objects.filter(
            vendor=vendor, status=Product.Status.PUBLISHED, fiscal_category="standard"
        ).first()
        assert product is not None
        CartLineFactory.create(
            cart=cart, product=product, quantity=1, added_price_net=product.price_net
        )
    details = {
        "email": "buyer@example.com",
        "full_name": "Mario Rossi",
        "line1": "1 Via Test",
        "postcode": "00100",
        "city": "Roma",
        "country": "IT",
        "is_business": "on",
        "company": "Esempio S.r.l.",
        "vat_id": "IT00000000000",
    }
    assert client.post(reverse("orders:checkout"), details).status_code == 302
    html = client.get(reverse("orders:review")).content.decode()
    assert "accept-onerous" in html
    vendor_a = dict(data.SALES_TERMS["A"].clauses)
    arbitration = dict(data.SALES_TERMS["D"].clauses)["10"]
    assert shown("7", vendor_a["7"]) in html  # vendor A
    assert shown("12", vendor_a["12"]) in html
    platform = {c["article"]: c["title"] for c in clauses_in(primary_language())}
    assert shown("9", {primary_language(): platform["9"]}) in html  # the platform terms page
    assert shown("14", {primary_language(): platform["14"]}) in html
    assert shown("10", arbitration) not in html  # vendor D is not an Italian party
    assert "S.r.l.." not in html
