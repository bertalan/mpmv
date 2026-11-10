"""CMP.3, owner decision 2026-11-09 (Q2): no sale into a country without warnings in one of its
languages (GPSR art. 19(c)).

The product stays published and purchasable for the other countries. The product page and the
checkout show a notice for a blocked destination, with links to the warnings in the languages
that exist; the vendor (and staff) see which country languages are missing. The checkout asks
through `apps.catalog.selling`, so cart and orders never import compliance.
"""

from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User
from apps.catalog.selling import sale_blockers
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import engine, services
from apps.compliance.tests.factories import GpsrSafetyTextFactory
from apps.core.countries import OFFICIAL_LANGUAGES

pytestmark = pytest.mark.django_db


def _country_speaking(language: str, *, only: bool = True) -> str:
    """A country of the table whose languages are `language` (alone, when `only`)."""
    for country, languages in sorted(OFFICIAL_LANGUAGES.items()):
        if (languages == (language,)) if only else (language in languages):
            return country
    raise AssertionError(f"no country speaks {language}")


GERMAN = _country_speaking("de", only=False)
FRENCH_ONLY = _country_speaking("fr")
GERMAN_ONLY = _country_speaking("de")


def test_a_product_without_warnings_is_sold_everywhere() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(
        product=product, language="en", warnings="", safety_information="x"
    )
    assert services.warnings_sale_check(product, FRENCH_ONLY).allowed
    assert services.warnings_language_gaps(product) == []
    assert sale_blockers(product, FRENCH_ONLY) == {}


def test_warnings_in_another_language_block_only_that_country() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    blocked = services.warnings_sale_check(product, FRENCH_ONLY)
    assert not blocked.allowed
    assert blocked.reason == engine.WARNINGS_LANGUAGE_MISSING
    assert blocked.available_languages == ("de",)
    assert services.warnings_sale_check(product, GERMAN_ONLY).allowed
    # The product stays published: the block is per destination, not a publication blocker.
    status = product.status
    product.refresh_from_db()
    assert product.status == status
    assert set(sale_blockers(product, FRENCH_ONLY)) == {"gpsr_warnings"}
    assert sale_blockers(product, GERMAN_ONLY) == {}


def test_an_invalid_destination_is_a_validation_error() -> None:
    product = ProductFactory.create()
    with pytest.raises(ValidationError):
        services.warnings_sale_check(product, "not a country")


def test_the_gaps_tell_which_languages_are_missing_and_which_can_be_added() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    gaps = {gap.country: gap for gap in services.warnings_language_gaps(product)}
    assert FRENCH_ONLY in gaps
    assert GERMAN_ONLY not in gaps
    gap = gaps[FRENCH_ONLY]
    assert gap.languages == ("fr",)
    assert gap.offered == ("fr",)  # French is a configured language: the vendor can add it
    unoffered = [g for g in gaps.values() if not g.offered]
    assert unoffered, "countries whose languages the platform does not offer are listed too"


def test_vendor_gaps_list_only_products_with_gaps() -> None:
    blocked = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=blocked, language="de", warnings="Achtung.")
    ProductFactory.create(vendor=blocked.vendor)  # no warnings
    other_vendor = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=other_vendor, language="de", warnings="Achtung.")
    rows = services.vendor_warnings_language_gaps(blocked.vendor)
    assert [product.pk for product, _gaps in rows] == [blocked.pk]


def _render_notice(product: object, country: str, path: str = "/p/") -> str:
    request = RequestFactory().get(path)
    template = Template(
        "{% load compliance_tags %}{% product_destination_notice product country %}"
    )
    return template.render(Context({"product": product, "country": country, "request": request}))


def test_the_notice_explains_the_block_and_links_the_available_warnings() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    with translation.override("en"):
        html = _render_notice(product, FRENCH_ONLY)
    assert 'role="status"' in html or 'role="alert"' in html
    assert FRENCH_ONLY in html
    assert "cannot be shipped" in html
    assert 'hreflang="de"' in html
    assert "#product-safety-texts" in html
    with translation.override("en"):
        assert _render_notice(product, GERMAN).strip() == ""


def test_the_safety_block_has_the_anchor_the_notice_links_to() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="en", warnings="Careful.")
    with translation.override("en"):
        html = Template("{% load compliance_tags %}{% product_safety product %}").render(
            Context({"product": product})
        )
    assert 'id="product-safety-texts"' in html


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_staff_see_the_missing_languages_on_the_warnings_page(staff_client: Client) -> None:
    text = GpsrSafetyTextFactory.create(language="de", warnings="Achtung.")
    response = staff_client.get(reverse("admin:compliance_gpsrsafetytext_change", args=[text.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Sale blocked into" in body
    assert FRENCH_ONLY in body
