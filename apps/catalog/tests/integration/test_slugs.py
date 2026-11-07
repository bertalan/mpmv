"""CAT.5: slug regeneration, SlugHistory and the 301 from old slugs (spec 03 §3.2, Q-91).

Regression cases: a current `{title}-{lang}-{sku}` slug becomes history and answers a single 301
to the SKU-free slug; changing the title writes history; every old slug redirects in one hop.
"""

from collections.abc import Callable
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ProductTranslation, SlugHistory
from apps.catalog.services import (
    old_slug_redirect,
    rebuild_product_slugs,
    record_old_slugs,
    sync_product_slug,
)
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    SlugHistoryFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def slug_entries() -> "QuerySet[AuditLog]":
    return AuditLog.objects.filter(action="catalog.product.slug_changed")


def history(translation: ProductTranslation) -> list[str]:
    return sorted(
        SlugHistory.objects.filter(
            product=translation.product, language=translation.language
        ).values_list("slug", flat=True)
    )


# --- Generating and regenerating a slug


def test_a_first_slug_is_generated_without_history_or_audit() -> None:
    translation = ProductTranslationFactory.create(title="Leather Bag", language="en", slug="")
    changes = sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en"
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert history(translation) == []
    assert not slug_entries().exists()


def test_a_title_change_regenerates_the_slug_and_keeps_the_old_one() -> None:
    translation = ProductTranslationFactory.create(
        title="Leather Bag", language="en", slug="leather-bag-en"
    )
    translation.title = "Leather Travel Bag"
    translation.save()
    changes = sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "leather-travel-bag-en"
    assert history(translation) == ["leather-bag-en"]
    assert changes.summary() == {"create": 1, "update": 1, "delete": 0}
    entry = slug_entries().get()
    assert entry.before == {"language": "en", "slug": "leather-bag-en"}
    assert entry.after == {"language": "en", "slug": "leather-travel-bag-en"}
    assert entry.target_key == str(translation.product.public_id)


def test_an_unchanged_title_changes_nothing() -> None:
    translation = ProductTranslationFactory.create(
        title="Leather Bag", language="en", slug="leather-bag-en"
    )
    changes = sync_product_slug(translation, ctx=CTX)
    assert changes.is_empty
    assert history(translation) == []
    assert not AuditLog.objects.exists()


def test_dry_run_writes_nothing() -> None:
    translation = ProductTranslationFactory.create(
        title="New title", language="en", slug="leather-bag-en-sku-00001"
    )
    changes = sync_product_slug(translation, ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 1, "delete": 0}
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en-sku-00001"
    assert not SlugHistory.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_slug_taken_by_another_product_gets_a_suffix() -> None:
    ProductTranslationFactory.create(title="Leather Bag", language="en", slug="leather-bag-en")
    translation = ProductTranslationFactory.create(title="Leather Bag", language="en", slug="")
    sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en-2"


def test_the_same_slug_in_another_language_is_no_collision() -> None:
    ProductTranslationFactory.create(title="Bag", language="en", slug="bag-en")
    translation = ProductTranslationFactory.create(title="Bag", language="it", slug="")
    sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "bag-it"


def test_a_slug_in_another_products_history_is_not_reused() -> None:
    SlugHistoryFactory.create(language="en", slug="leather-bag-en")
    translation = ProductTranslationFactory.create(title="Leather Bag", language="en", slug="")
    sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en-2"


def test_going_back_to_an_old_title_reclaims_its_slug_without_a_loop() -> None:
    translation = ProductTranslationFactory.create(
        title="Leather Bag", language="en", slug="leather-bag-en"
    )
    translation.title = "Travel Bag"
    translation.save()
    sync_product_slug(translation, ctx=CTX)
    translation.title = "Leather Bag"
    translation.save()
    changes = sync_product_slug(translation, ctx=CTX)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en"
    assert history(translation) == ["travel-bag-en"]
    assert changes.summary() == {"create": 1, "update": 1, "delete": 1}
    assert old_slug_redirect("en", "leather-bag-en") is None


# --- Current SKU slugs loaded as history


def test_rebuild_turns_current_sku_slugs_into_history() -> None:
    product = ProductFactory.create(sku="SKU-00001")
    en = ProductTranslationFactory.create(
        product=product, language="en", title="Leather Bag", slug="leather-bag-en-sku-00001"
    )
    it = ProductTranslationFactory.create(
        product=product, language="it", title="Borsa in pelle", slug="borsa-in-pelle-it-sku-00001"
    )
    changes = rebuild_product_slugs(ctx=CTX)
    en.refresh_from_db()
    it.refresh_from_db()
    assert (en.slug, it.slug) == ("leather-bag-en", "borsa-in-pelle-it")
    assert "sku" not in en.slug
    assert "sku" not in it.slug
    assert history(en) == ["leather-bag-en-sku-00001"]
    assert history(it) == ["borsa-in-pelle-it-sku-00001"]
    assert changes.summary() == {"create": 2, "update": 2, "delete": 0}
    assert slug_entries().count() == 2


def test_rebuild_dry_run_writes_nothing() -> None:
    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en-sku-00001"
    )
    changes = rebuild_product_slugs(ctx=CTX, dry_run=True)
    assert not changes.is_empty
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en-sku-00001"
    assert not SlugHistory.objects.exists()


def test_rebuild_command_has_a_dry_run() -> None:
    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en-sku-00001"
    )
    out = StringIO()
    call_command("rebuild_product_slugs", "--dry-run", stdout=out)
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en-sku-00001"
    assert "dry run" in out.getvalue().lower()

    call_command("rebuild_product_slugs", stdout=StringIO())
    translation.refresh_from_db()
    assert translation.slug == "leather-bag-en"
    entry = slug_entries().get()
    assert entry.source == "command"


def test_old_slugs_are_recorded_as_history() -> None:
    translation = ProductTranslationFactory.create(language="it", title="Borsa", slug="borsa-it")
    changes = record_old_slugs(
        translation.product,
        "it",
        ["borsa-it-sku-1", "borsa-vecchia-it-sku-1", "borsa-it", "borsa-it-sku-1"],
        ctx=CTX,
    )
    # The current slug and the repeated one are skipped.
    assert changes.summary() == {"create": 2, "update": 0, "delete": 0}
    assert history(translation) == ["borsa-it-sku-1", "borsa-vecchia-it-sku-1"]


def test_an_old_slug_of_another_product_stops_the_load() -> None:
    ProductTranslationFactory.create(language="en", title="Hat", slug="hat-en")
    translation = ProductTranslationFactory.create(language="en", title="Bag", slug="bag-en")
    with pytest.raises(ValidationError, match="hat-en"):
        record_old_slugs(translation.product, "en", ["bag-en-sku-1", "hat-en"], ctx=CTX)
    assert not SlugHistory.objects.exists()


# --- The 301 from old slugs


def test_an_old_slug_resolves_to_the_current_path() -> None:
    ProductTranslationFactory.create(
        language="it", title="Borsa in pelle", slug="borsa-in-pelle-it-sku-00001"
    )
    rebuild_product_slugs(ctx=CTX)
    assert old_slug_redirect("it", "borsa-in-pelle-it-sku-00001") == (
        "/it/prodotto/borsa-in-pelle-it/"
    )
    assert old_slug_redirect("en", "borsa-in-pelle-it-sku-00001") is None
    assert old_slug_redirect("it", "unknown-it") is None


def test_an_old_slug_of_a_product_without_a_current_slug_does_not_redirect() -> None:
    entry = SlugHistoryFactory.create(language="en", slug="gone-en-sku-1")
    assert not entry.product.translations.exists()
    assert old_slug_redirect("en", "gone-en-sku-1") is None


def test_a_current_sku_slug_answers_301_to_the_new_slug(client: Client) -> None:
    ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en-sku-00001"
    )
    rebuild_product_slugs(ctx=CTX)
    response = client.get("/product/leather-bag-en-sku-00001/")
    assert response.status_code == 301
    assert response["Location"] == "/product/leather-bag-en/"


def test_the_italian_old_slug_redirects_under_the_italian_prefix(client: Client) -> None:
    ProductTranslationFactory.create(
        language="it", title="Borsa in pelle", slug="borsa-in-pelle-it-sku-00001"
    )
    rebuild_product_slugs(ctx=CTX)
    response = client.get("/it/prodotto/borsa-in-pelle-it-sku-00001/?utm_source=feed")
    assert response.status_code == 301
    assert response["Location"] == "/it/prodotto/borsa-in-pelle-it/?utm_source=feed"


def test_every_old_slug_redirects_in_a_single_hop(client: Client) -> None:
    translation = ProductTranslationFactory.create(
        language="en", title="Leather Bag", slug="leather-bag-en-sku-00001"
    )
    for title in ("Leather Bag", "Leather Travel Bag", "Big Leather Travel Bag"):
        translation.title = title
        translation.save()
        sync_product_slug(translation, ctx=CTX)
    for old in ("leather-bag-en-sku-00001", "leather-bag-en", "leather-travel-bag-en"):
        response = client.get(f"/product/{old}/")
        assert response.status_code == 301
        assert response["Location"] == "/product/big-leather-travel-bag-en/"


def test_unknown_product_slugs_stay_404(client: Client) -> None:
    ProductTranslationFactory.create(language="en", title="Leather Bag", slug="leather-bag-en")
    assert client.get("/product/no-such-product-en/").status_code == 404
    # An English old slug is not found under the Italian prefix.
    SlugHistoryFactory.create(language="en", slug="old-bag-en")
    assert client.get("/it/prodotto/old-bag-en/").status_code == 404


# --- The admin keeps the slug derived from the title


MfaLogin = Callable[[Client, User], Client]


def test_the_admin_regenerates_the_slug_on_a_title_change(
    client: Client, superuser: User, mfa_login: MfaLogin
) -> None:
    staff = mfa_login(client, superuser)
    product = ProductFactory.create(sku="BAG-01")
    translation = ProductTranslationFactory.create(
        product=product, language="en", title="Leather Bag", slug="leather-bag-en-bag-01"
    )
    data = {
        "vendor": str(product.vendor.pk),
        "sku": product.sku,
        "kind": product.kind,
        "sales_mode": product.sales_mode,
        "fiscal_category": product.fiscal_category,
        "price_net": str(product.price_net),
        "stock_policy": product.stock_policy,
        "stock_qty": str(product.stock_qty),
        "stock_status": product.stock_status,
        "weight_kg": str(product.weight_kg),
        "translations-TOTAL_FORMS": "1",
        "translations-INITIAL_FORMS": "1",
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
        "translations-0-id": str(translation.pk),
        "translations-0-product": str(product.pk),
        "translations-0-language": "en",
        "translations-0-status": translation.status,
        "translations-0-title": "Leather Travel Bag",
        "variants-TOTAL_FORMS": "0",
        "variants-INITIAL_FORMS": "0",
        "variants-MIN_NUM_FORMS": "0",
        "variants-MAX_NUM_FORMS": "1000",
    }
    response = staff.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, response.content.decode()
    translation.refresh_from_db()
    assert translation.slug == "leather-travel-bag-en"
    assert history(translation) == ["leather-bag-en-bag-01"]
    assert slug_entries().get().source == "admin"
