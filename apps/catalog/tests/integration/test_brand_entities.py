"""CAT.20 (1/3): Brand as a linked entity: status, logo, official URL, sameAs, Wikidata ID and an
optional Manufacturer; a pending brand is not public; the data migration keeps every brand."""

from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Brand, Manufacturer
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    ManufacturerFactory,
    ProductFactory,
)
from apps.core.translations import PUBLISHED

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def test_a_brand_is_approved_unless_proposed() -> None:
    assert BrandFactory.create().status == Brand.Status.APPROVED
    assert BrandFactory.create(status=Brand.Status.PENDING).status == "pending"


def test_name_key_is_filled_on_save_and_follows_renames() -> None:
    brand = BrandFactory.create(name="Café & Co")
    assert brand.name_key == "cafe and co"
    brand.name = "Acme"
    brand.save()
    brand.refresh_from_db()
    assert brand.name_key == "acme"


def test_a_spelling_variant_of_an_existing_brand_is_refused() -> None:
    BrandFactory.create(name="Acme & Sons")
    with pytest.raises(ValidationError) as caught:
        BrandFactory.build(name="ACME and sons").full_clean()
    assert "name" in caught.value.message_dict


def test_an_invalid_wikidata_id_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        BrandFactory.build(wikidata_id="q42").full_clean()
    assert "wikidata_id" in caught.value.message_dict


def test_the_wikidata_id_is_unique_when_set() -> None:
    BrandFactory.create(wikidata_id="Q42")
    BrandFactory.create(wikidata_id="")
    BrandFactory.create(wikidata_id="")
    with pytest.raises(ValidationError) as caught:
        BrandFactory.build(wikidata_id="Q42").full_clean()
    assert "wikidata_id" in caught.value.message_dict


@pytest.mark.parametrize("field", ["url", "same_as"])
def test_a_non_https_link_is_refused(field: str) -> None:
    value = (
        "http://example.com" if field == "url" else ["https://ok.example/", "http://bad.example"]
    )
    with pytest.raises(ValidationError) as caught:
        BrandFactory.build(**{field: value}).full_clean()
    assert field in caught.value.message_dict


def test_manufacturer_links_are_validated_too() -> None:
    with pytest.raises(ValidationError) as caught:
        ManufacturerFactory.build(url="ftp://x.example", same_as=["http://y.example"]).full_clean()
    assert {"url", "same_as"} <= set(caught.value.message_dict)
    ManufacturerFactory.build(country="IT").full_clean()


def test_a_brand_has_an_optional_manufacturer_kept_when_deleted() -> None:
    maker = ManufacturerFactory.create(legal_name="Acme S.p.A.")
    brand = BrandFactory.create(manufacturer=maker)
    assert list(maker.brands.all()) == [brand]
    maker.delete()
    brand.refresh_from_db()
    assert brand.manufacturer is None
    assert Manufacturer.objects.count() == 0


def test_a_brand_logo_is_optional() -> None:
    assert BrandFactory.create().logo is None


def test_a_pending_brand_has_no_public_page(client: Client) -> None:
    brand = BrandFactory.create(status=Brand.Status.PENDING)
    BrandTranslationFactory.create(brand=brand, slug="proposed", status=PUBLISHED)
    ProductFactory.create(brand=brand)
    assert client.get("/brand/proposed/").status_code == 404
    brand.status = Brand.Status.APPROVED
    brand.save()
    assert client.get("/brand/proposed/").status_code == 200


def test_a_rejected_brand_has_no_public_page(client: Client) -> None:
    brand = BrandFactory.create(status=Brand.Status.REJECTED)
    BrandTranslationFactory.create(brand=brand, slug="nope", status=PUBLISHED)
    assert client.get("/brand/nope/").status_code == 404


def test_staff_admin_edits_the_new_fields(staff_client: Client) -> None:
    maker = ManufacturerFactory.create()
    brand = BrandFactory.create()
    url = reverse("admin:catalog_brand_change", args=[brand.pk])
    page = staff_client.get(url)
    assert page.status_code == 200
    for name in ("status", "wikidata_id", "same_as", "manufacturer", "logo"):
        assert f'name="{name}"' in page.content.decode()
    assert (
        staff_client.get(reverse("admin:catalog_manufacturer_change", args=[maker.pk])).status_code
        == 200
    )
