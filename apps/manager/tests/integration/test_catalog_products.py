"""CAT.11: the Catalog section of the Manager platform space."""

from datetime import UTC, datetime

import pytest
from django.test import Client

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog import publishing
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db

LIST = "/manager/platform/catalog/products/"


def sign_in(client: Client, role: str, **kwargs: object) -> None:
    verified_login(client, UserFactory.create(platform_role=role), **kwargs)  # type: ignore[arg-type]


def detail(product: Product) -> str:
    return f"{LIST}{product.public_id}/"


@pytest.fixture(autouse=True)
def _no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})


@pytest.mark.parametrize(
    ("role", "code"),
    [("catalog", 200), ("owner", 200), ("finance", 404), ("support", 404), ("config", 404)],
)
def test_role_access(client: Client, role: str, code: int) -> None:
    product = ProductFactory.create(languages=["en"])
    sign_in(client, role)
    assert client.get(LIST).status_code == code
    assert client.get(detail(product)).status_code == code


def test_finance_and_support_cannot_change_a_product(client: Client) -> None:
    product = ProductFactory.create()
    for role in ("finance", "support"):
        sign_in(client, role)
        assert (
            client.post(
                LIST, {"action": "publish", "selected": [str(product.public_id)], "confirm": "1"}
            ).status_code
            == 404
        )
        assert (
            client.post(detail(product), {"sales_mode": "catalog_only", "confirm": "1"}).status_code
            == 404
        )
    product.refresh_from_db()
    assert product.status == "draft"


def test_the_owner_reads_but_cannot_write(client: Client) -> None:
    product = ProductFactory.create()
    sign_in(client, "owner")
    response = client.post(LIST, {"action": "publish", "selected": [str(product.public_id)]})
    assert response.status_code == 404


def test_filters(client: Client) -> None:
    a, b = VendorFactory.create(), VendorFactory.create()
    cat = CategoryFactory.create()
    p1 = ProductFactory.create(vendor=a, sku="ALPHA-1", category=cat, languages=["en"])
    p2 = ProductFactory.create(vendor=b, sku="BETA-2", status="published", languages=["en"])
    sign_in(client, "catalog")
    both = client.get(LIST).text
    assert "ALPHA-1" in both
    assert "BETA-2" in both
    by_vendor = client.get(LIST, {"vendor": str(a.public_id)}).text
    assert "ALPHA-1" in by_vendor
    assert "BETA-2" not in by_vendor
    by_status = client.get(LIST, {"status": "published"}).text
    assert "BETA-2" in by_status
    assert "ALPHA-1" not in by_status
    by_cat = client.get(LIST, {"category": str(cat.public_id)}).text
    assert "ALPHA-1" in by_cat
    assert "BETA-2" not in by_cat
    by_q = client.get(LIST, {"q": "beta"}).text
    assert "BETA-2" in by_q
    assert "ALPHA-1" not in by_q
    assert p1
    assert p2


def test_translation_status_is_correct_and_filterable(client: Client) -> None:
    other = other_language()
    done = ProductFactory.create(sku="DONE-1")
    for code in configured_languages():
        ProductTranslationFactory.create(product=done, language=code, status="published")
    partial = ProductFactory.create(sku="PART-1", languages=["en"])
    sign_in(client, "catalog")
    complete = client.get(LIST, {"translation": "complete"}).text
    assert "DONE-1" in complete
    assert "PART-1" not in complete
    incomplete = client.get(LIST, {"translation": "incomplete"}).text
    assert "PART-1" in incomplete
    assert "DONE-1" not in incomplete
    missing = client.get(LIST, {"translation": f"missing:{other}"}).text
    assert "PART-1" in missing
    assert "DONE-1" not in missing
    assert partial


def test_the_list_query_count_does_not_grow_with_rows(
    client: Client, django_assert_max_num_queries: object
) -> None:
    ProductFactory.create_batch(3, languages=["en"])
    sign_in(client, "catalog")
    client.get(LIST)  # warm caches
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as few:
        client.get(LIST)
    ProductFactory.create_batch(15, languages=["en"])
    with CaptureQueriesContext(connection) as many:
        client.get(LIST)
    # The shell's menu checks cost a fixed number of queries; the rows add none.
    assert len(many) <= len(few) + 1


def test_bulk_publish_previews_then_applies_with_audit(client: Client) -> None:
    products = ProductFactory.create_batch(2)
    ids = [str(p.public_id) for p in products]
    sign_in(client, "catalog")
    preview = client.post(LIST, {"action": "publish", "selected": ids})
    assert preview.status_code == 200
    assert 'name="confirm"' in preview.text
    assert set(Product.objects.values_list("status", flat=True)) == {"draft"}
    assert not AuditLog.objects.filter(action="catalog.product.status_changed").exists()
    done = client.post(LIST, {"action": "publish", "selected": ids, "confirm": "1"})
    assert done.status_code == 302
    assert set(Product.objects.values_list("status", flat=True)) == {"published"}
    assert AuditLog.objects.filter(action="catalog.product.status_changed").count() == 2


@pytest.mark.parametrize(("action", "status"), [("unpublish", "draft"), ("retire", "retired")])
def test_other_bulk_actions(client: Client, action: str, status: str) -> None:
    product = ProductFactory.create(status="published")
    sign_in(client, "catalog")
    client.post(LIST, {"action": action, "selected": [str(product.public_id)], "confirm": "1"})
    product.refresh_from_db()
    assert product.status == status


def test_a_blocked_publication_is_shown_and_changes_nothing(client: Client) -> None:
    publishing.register_publication_check("t", lambda p: ["blocked"])
    product = ProductFactory.create()
    sign_in(client, "catalog")
    response = client.post(
        LIST, {"action": "publish", "selected": [str(product.public_id)], "confirm": "1"}
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.status == "draft"


def test_bulk_writes_need_the_session_level(client: Client) -> None:
    product = ProductFactory.create()
    sign_in(client, "catalog", level=levels.NO_WINDOW)
    response = client.post(
        LIST, {"action": "publish", "selected": [str(product.public_id)], "confirm": "1"}
    )
    assert response.status_code == 403
    product.refresh_from_db()
    assert product.status == "draft"


def test_the_preview_shows_translation_status_per_language(client: Client) -> None:
    product = ProductFactory.create(languages=["en"])
    sign_in(client, "catalog")
    response = client.get(detail(product))
    assert response.status_code == 200
    assert product.sku in response.text
    assert other_language() in response.text


def test_staff_edit_audited_fields_with_dry_run_then_confirm(client: Client) -> None:
    product = ProductFactory.create(sales_mode="buy")
    sign_in(client, "catalog")
    data = {"sales_mode": "catalog_only", "fiscal_category": product.fiscal_category}
    preview = client.post(detail(product), data)
    assert preview.status_code == 200
    assert 'name="confirm"' in preview.text
    product.refresh_from_db()
    assert product.sales_mode == "buy"
    done = client.post(detail(product), {**data, "confirm": "1"})
    assert done.status_code == 302
    product.refresh_from_db()
    assert product.sales_mode == "catalog_only"
    assert AuditLog.objects.filter(action="catalog.product.updated").exists()


def test_the_detail_edit_needs_the_session_level(client: Client) -> None:
    """`catalogue_edit` is an everyday action by default; once the operator makes it sensitive,
    a session with no passkey window is refused and nothing changes."""
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        session_policy={"actions": {"sensitive": ["catalogue_edit"]}},
    )
    product = ProductFactory.create(sales_mode="buy")
    sign_in(client, "catalog", level=levels.NO_WINDOW)
    data = {
        "sales_mode": "catalog_only",
        "fiscal_category": product.fiscal_category,
        "confirm": "1",
    }
    assert client.post(detail(product), data).status_code == 403
    product.refresh_from_db()
    assert product.sales_mode == "buy"
    assert not AuditLog.objects.filter(action="catalog.product.updated").exists()


def test_the_owner_cannot_post_on_the_detail_page(client: Client) -> None:
    product = ProductFactory.create(sales_mode="buy")
    sign_in(client, "owner")
    data = {
        "sales_mode": "catalog_only",
        "fiscal_category": product.fiscal_category,
        "confirm": "1",
    }
    assert client.post(detail(product), data).status_code == 404
    product.refresh_from_db()
    assert product.sales_mode == "buy"


def test_a_fiscal_category_change_is_audited_with_old_and_new_value(client: Client) -> None:
    product = ProductFactory.create(fiscal_category="standard")
    sign_in(client, "catalog")
    data = {"sales_mode": product.sales_mode, "fiscal_category": "reduced", "confirm": "1"}
    assert client.post(detail(product), data).status_code == 302
    product.refresh_from_db()
    assert product.fiscal_category == "reduced"
    row = AuditLog.objects.get(action="catalog.product.updated")
    assert row.before is not None
    assert row.after is not None
    assert row.before["fiscal_category"] == "standard"
    assert row.after["fiscal_category"] == "reduced"


def test_staff_set_the_product_role_override_with_an_audit_entry(client: Client) -> None:
    product = ProductFactory.create(sales_mode="buy")
    sign_in(client, "catalog")
    data = {
        "sales_mode": product.sales_mode,
        "fiscal_category": product.fiscal_category,
        "vendor_role": "reseller",
        "confirm": "1",
    }
    assert client.post(detail(product), data).status_code == 302
    product.refresh_from_db()
    assert product.vendor_role == "reseller"
    entry = AuditLog.objects.get(action="catalog.product.updated")
    assert entry.after is not None
    assert entry.after["vendor_role"] == "reseller"
    data["vendor_role"] = "maker"
    assert client.post(detail(product), data).status_code == 200  # refused by the form
