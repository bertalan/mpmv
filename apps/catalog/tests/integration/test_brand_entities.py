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


@pytest.mark.django_db(transaction=True)
def test_the_migration_keeps_every_brand_public_and_fills_keys_idempotently() -> None:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    BrandFactory.create(name="Café & Co")
    BrandTranslationFactory.create(slug="kept", status=PUBLISHED)
    before = list(Brand.objects.order_by("pk").values_list("name", flat=True))

    executor = MigrationExecutor(connection)
    executor.migrate([("catalog", "0010_d25_product_slugs")])
    executor.loader.build_graph()
    executor.migrate([("catalog", "0011_brand_entities")])
    executor.loader.build_graph()
    executor.migrate([("catalog", "0010_d25_product_slugs")])
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    brands = Brand.objects.order_by("pk")
    assert list(brands.values_list("name", flat=True)) == before
    assert {b.status for b in brands} == {"approved"}
    assert brands.get(name="Café & Co").name_key == "cafe and co"
    assert brands.filter(translations__slug="kept").exists()


def test_a_logo_needs_an_alt_text() -> None:
    from apps.catalog.tests.factories import brand_logo_image

    brand = BrandFactory.build(logo=brand_logo_image(), logo_alt="")
    with pytest.raises(ValidationError) as caught:
        brand.full_clean()
    assert "logo_alt" in caught.value.message_dict


def test_a_legacy_near_duplicate_stays_editable_but_a_rename_into_one_is_refused() -> None:
    first = BrandFactory.create(name="Acme & Sons")
    legacy = BrandFactory.create(name="ACME and sons")  # saved without full_clean
    legacy.url = "https://acme.example/"
    legacy.full_clean()  # not renamed: allowed
    other = BrandFactory.create(name="Other")
    other.name = first.name.upper() + "!"
    with pytest.raises(ValidationError) as caught:
        other.full_clean()
    assert "name" in caught.value.message_dict


def test_only_approved_brands_are_offered_to_product_forms(staff_client: Client) -> None:
    approved = BrandFactory.create(name="Visible")
    pending = BrandFactory.create(name="Hidden", status=Brand.Status.PENDING)
    page = staff_client.get(reverse("admin:catalog_product_add")).content.decode()
    assert approved.name in page
    assert pending.name not in page
