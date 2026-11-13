"""ARC.10: `manage.py seed` builds the demo data set through the services, is idempotent and
reproducible, and `--reset` takes out the seed's data and nothing else.

Done when: reproducible `make seed`, used by the tests (the `demo_seed` fixture).
"""

import io
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from wagtail.models import Locale

from apps.accounts.models import User
from apps.catalog.models import Product, ProductTranslation, Variant
from apps.catalog.taxonomy import Category
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
)
from apps.cms.models import HomePage, LegalPage, StandardPage
from apps.cms.tests.factories import StandardPageFactory
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.events.models import EventPage
from apps.seed import data
from apps.seed.apps import check_not_allowed_on_live
from apps.seed.services import (
    TEST_CONTEXT,
    SeedConflict,
    SeedNotAllowed,
    reset,
    seed,
    seed_allowed,
    seed_vendors,
)
from apps.shipping.models import ShippingMethod, ShippingZone, SpecialAreaTreatment
from apps.tax.models import TaxRate
from apps.tax.services import current_tax_profile
from apps.vendors import engine as vendors_engine
from apps.vendors.models import ContractAcceptance, Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

SEEDED_MODELS = (
    Vendor,
    Product,
    ProductTranslation,
    Variant,
    Category,
    ShippingZone,
    ShippingMethod,
    SpecialAreaTreatment,
    TaxRate,
    StandardPage,
    LegalPage,
    EventPage,
    User,
    AuditLog,
)


def counts() -> dict[str, int]:
    return {model.__name__: model.objects.count() for model in SEEDED_MODELS}


def test_the_seed_builds_active_vendors_with_every_regime(demo_seed: ChangeSet) -> None:
    vendors = seed_vendors()
    assert [v.display_name for v in vendors] == [s.display_name for s in data.VENDORS]
    for vendor, spec in zip(vendors, data.VENDORS, strict=True):
        assert vendor.status == Vendor.Status.ACTIVE
        assert vendor.fiscal_regime == spec.fiscal_regime
        profile = current_tax_profile(vendor)
        assert (profile.regime, profile.exempt_reason) == (spec.vat_regime, spec.exempt_reason)
        assert profile.exempt_country == spec.exempt_country  # TAX.13
        profile.full_clean()
        assert ContractAcceptance.objects.filter(vendor=vendor).count() == 1
        assert {t.language for t in vendor.translations.all()} == {"en", "it"}
        assert (
            vendors_engine.dac7_missing(
                vendor.legal_identity(), vendors_engine.SEEDED_NO_TIN_COUNTRIES
            )
            == ()
        )  # VEN.23
    rf20 = vendors[-1]
    assert rf20.fiscal_regime == "RF20"
    assert rf20.cross_border_franchise_number.endswith("EX")


def test_seed_accounts_cannot_log_in(demo_seed: ChangeSet) -> None:
    users = User.objects.filter(email__endswith="@example.org")
    assert users.count() == len(data.VENDORS) + 1  # the owners and the seed operator
    for user in users:
        assert not user.has_usable_password()
    operator = User.objects.get(email=data.OPERATOR_EMAIL)
    assert not operator.is_active


def test_the_seed_operator_holds_the_platform_staff_permission(demo_seed: ChangeSet) -> None:
    """CORE.8: platform staff is a permission; `is_staff` alone sees no vendor's data."""
    operator = User.objects.get(email=data.OPERATOR_EMAIL)
    assert operator.user_permissions.filter(codename="view_all_vendors").exists()


def test_the_seed_builds_a_published_bilingual_catalog(demo_seed: ChangeSet) -> None:
    products = Product.objects.filter(vendor__in=seed_vendors())
    assert products.count() == sum(len(items) for items in data.PRODUCTS.values())
    assert set(products.values_list("status", flat=True)) == {Product.Status.PUBLISHED}
    for product in products.prefetch_related("translations"):
        assert product.category is not None
        assert product.terms.exists()
        assert product.price_net is not None
        assert isinstance(product.price_net, Decimal)
        assert product.price_net > 0
        texts = {t.language: t for t in product.translations.all()}
        assert set(texts) == {"en", "it"}
        assert {t.status for t in texts.values()} == {"published"}
        assert all(t.slug for t in texts.values())
    assert Variant.objects.filter(product__in=products).count() > 20


def test_the_seed_configures_shipping_with_island_surcharges(demo_seed: ChangeSet) -> None:
    for vendor in seed_vendors():
        assert ShippingZone.objects.filter(vendor=vendor).count() == 2
        assert ShippingMethod.objects.filter(vendor=vendor).count() == 3
    sicily = SpecialAreaTreatment.objects.filter(
        area__code="it-sicily", method__vendor__slug=data.VENDORS[0].slug
    )
    assert sicily.exists()
    assert {t.mode for t in sicily} == {"surcharge"}


def test_a_second_run_changes_nothing(demo_seed: ChangeSet) -> None:
    assert not demo_seed.is_empty
    before = counts()

    again = seed(ctx=TEST_CONTEXT)

    assert again.is_empty
    assert counts() == before


def test_a_dry_run_writes_nothing() -> None:
    before = counts()

    changes = seed(ctx=TEST_CONTEXT, dry_run=True)

    assert not changes.is_empty
    assert counts() == before
    assert not Vendor.objects.filter(slug=data.VENDORS[0].slug).exists()


def test_reset_removes_only_the_seed_and_a_new_seed_restores_it() -> None:
    other_vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    other_product = ProductFactory.create(vendor=other_vendor, languages=["en"])
    other_category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=other_category, slug="other-things")
    seed(ctx=TEST_CONTEXT)
    seeded = seed_vendors()
    audit_entries = AuditLog.objects.count()

    reset(ctx=TEST_CONTEXT)

    assert not Product.objects.filter(vendor__in=seeded).exists()
    assert not ShippingZone.objects.filter(vendor__in=seeded).exists()
    assert not StandardPage.objects.filter(slug=data.HOW_IT_WORKS.slug["en"]).exists()
    assert not EventPage.objects.filter(slug__startswith="demo-").exists()
    assert not LegalPage.objects.filter(live=True, slug__startswith="demo-").exists()
    # Contract acceptances are append-only legal evidence (VEN.5): the vendors are closed.
    assert {v.status for v in seed_vendors()} == {Vendor.Status.CLOSED}
    assert AuditLog.objects.count() > audit_entries  # the reset is audited, nothing erased
    other_vendor.refresh_from_db()
    assert other_vendor.status == Vendor.Status.ACTIVE
    assert Product.objects.filter(pk=other_product.pk).exists()
    assert Category.objects.filter(pk=other_category.pk).exists()

    seed(ctx=TEST_CONTEXT)

    assert {v.status for v in seed_vendors()} == {Vendor.Status.ACTIVE}
    assert Product.objects.filter(vendor__in=seeded).count() == 50
    assert LegalPage.objects.filter(live=True, slug__startswith="demo-").count() == 10


def test_the_command_reports_the_changes_and_the_time() -> None:
    out = io.StringIO()

    call_command("seed", stdout=out)
    call_command("seed", stdout=out)

    lines = out.getvalue().splitlines()
    assert any("vendors.vendor" in line for line in lines)
    assert lines[-1].startswith("0 changes changed in ")


STOREFRONT = (
    ("/", "Made by independent makers"),
    ("/it/", "Fatto da artigiani indipendenti"),
    ("/de/", "Von unabhängigen Herstellern"),
    ("/product-category/demo-home-kitchen/", "Stoneware dinner plate"),
    ("/it/categoria-prodotto/demo-casa-e-cucina/", "Piatto piano in gres"),
    ("/material/wool/", "Merino crew-neck jumper"),
    ("/it/materiale/lana/", "Maglione girocollo in merino"),
    ("/brand/demo-brand-gamma/", "Essays on slow making"),
    ("/shop/vendor-d-outdoor/", "Steel camp lantern"),
    ("/it/shop/vendor-e-studio/", "Lampada da tavolo in ceramica"),
    ("/demo-how-it-works/", "How it works (demo)"),
    ("/it/demo-come-funziona/", "Come funziona (demo)"),
    ("/demo-terms-of-sale/", "DEMO PLACEHOLDER"),
    ("/it/demo-condizioni-di-vendita/", "SEGNAPOSTO"),
    ("/events/", "Makers fair (demo)"),
    ("/it/events/demo-fiera-artigiani/", "Fiera degli artigiani (demo)"),
)


def test_the_storefront_pages_show_the_seed(demo_seed: ChangeSet, client: Client) -> None:
    for path, text in STOREFRONT:
        response = client.get(path)
        assert response.status_code == 200, path
        assert text in response.content.decode(), path


def test_real_rows_with_generic_keys_survive_a_seed_and_a_reset() -> None:
    """M2: the seed never adopts, and its reset never deletes, a row it did not create."""
    garden = CategoryFactory.create()
    CategoryTranslationFactory.create(category=garden, slug="garden")
    home = HomePage.objects.get(locale=Locale.get_default(), depth=2)
    how = StandardPageFactory.create(parent=home, title="How it works", slug="how-it-works")

    seed(ctx=TEST_CONTEXT)
    reset(ctx=TEST_CONTEXT)

    assert Category.objects.filter(pk=garden.pk).exists()
    assert StandardPage.objects.filter(pk=how.pk).exists()


def test_the_seed_never_adopts_a_real_vendor_with_its_slug() -> None:
    real = VendorFactory.create(display_name=data.VENDORS[0].display_name)
    assert real.slug == data.VENDORS[0].slug

    with pytest.raises(SeedConflict):
        seed(ctx=TEST_CONTEXT)

    assert seed_vendors() == []
    reset(ctx=TEST_CONTEXT)
    assert Vendor.objects.filter(pk=real.pk).exists()


@pytest.mark.parametrize(
    ("allow", "live_data", "allowed"),
    [(True, False, True), (False, False, False), (True, True, False), (False, True, False)],
)
def test_the_seed_runs_only_where_allowed_and_never_on_live_data(
    allow: bool, live_data: bool, allowed: bool
) -> None:
    with override_settings(ALLOW_SEED=allow, LIVE_DATA=live_data):
        assert seed_allowed() is allowed
        errors = [m.id for m in check_not_allowed_on_live()]
    assert errors == (["seed.E001"] if allow and live_data else [])


@pytest.mark.parametrize(("allow", "live_data"), [(False, False), (True, True)])
def test_seed_and_reset_refuse_to_run_where_not_allowed(allow: bool, live_data: bool) -> None:
    with override_settings(ALLOW_SEED=allow, LIVE_DATA=live_data):
        with pytest.raises(SeedNotAllowed):
            seed(ctx=TEST_CONTEXT)
        with pytest.raises(SeedNotAllowed):
            reset(ctx=TEST_CONTEXT)
        with pytest.raises(CommandError, match="never runs on LIVE"):
            call_command("seed")
        with pytest.raises(CommandError, match="never runs on LIVE"):
            call_command("seed", "--reset")
    assert not Vendor.objects.exists()
