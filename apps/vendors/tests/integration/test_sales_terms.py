"""VEN.35: a seller's versioned sales terms (D-40, counsel L-1 to L-6)."""

import secrets
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import terms_services as ts
from apps.vendors.models import SalesTermsInForce, SalesTermsVersion, Vendor
from apps.vendors.tests.factories import (
    SalesTermsInForceFactory,
    SalesTermsVersionFactory,
    VendorFactory,
    VendorMemberFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="user:test", source="vendor")


def values(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "body": f"Terms {secrets.token_hex(4)}",
        "clauses": [{"article": "7", "title": "Limitation of liability"}],
        "governing_law": "IT",
        "b2b_court": "",
        "delivery_days": 4,
        "return_cost_bearer": "",
        "return_cost_info": "",
    }
    return base | over


def current(vendor: Vendor) -> SalesTermsVersion:
    version = ts.version_in_force(vendor)
    assert version is not None
    return version


def owner_of(vendor: Vendor) -> Any:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def test_a_version_without_the_clause_list_cannot_be_published() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError):
        ts.publish_sales_terms(vendor, values(clauses=[]), actor=owner_of(vendor), ctx=CTX)
    assert not SalesTermsVersion.objects.exists()


def test_a_publish_creates_a_new_version_and_leaves_the_old_one_intact() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    ts.publish_sales_terms(vendor, values(body="First"), actor=actor, ctx=CTX)
    v1 = SalesTermsVersion.objects.get()
    ts.publish_sales_terms(vendor, values(body="Second"), actor=actor, ctx=CTX)
    v1.refresh_from_db()
    assert v1.body == "First"
    assert v1.number == 1
    assert SalesTermsVersion.objects.filter(vendor=vendor).count() == 2
    in_force = ts.version_in_force(vendor)
    assert in_force is not None
    assert in_force.number == 2
    assert in_force.body == "Second"
    assert SalesTermsInForce.objects.get(vendor=vendor).version_id == in_force.pk


def test_a_stored_version_cannot_be_updated_or_deleted() -> None:
    vendor = VendorFactory.create()
    ts.publish_sales_terms(vendor, values(), actor=owner_of(vendor), ctx=CTX)
    version = SalesTermsVersion.objects.get()
    with pytest.raises(Exception, match="append-only"), transaction.atomic():
        SalesTermsVersion.objects.filter(pk=version.pk).update(body="edited")
    with (
        pytest.raises(Exception, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM vendors_salestermsversion")


def test_a_publish_writes_an_audit_entry_and_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    changes = ts.publish_sales_terms(vendor, values(), actor=actor, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert not SalesTermsVersion.objects.exists()
    assert not AuditLog.objects.filter(action=ts.PUBLISHED).exists()
    ts.publish_sales_terms(vendor, values(), actor=actor, ctx=CTX)
    entry = AuditLog.objects.get(action=ts.PUBLISHED, target_model="vendors.salestermsversion")
    assert entry.vendor_key == str(vendor.public_id)


def test_only_an_owner_of_the_vendor_publishes() -> None:
    vendor = VendorFactory.create()
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    with pytest.raises(PermissionDenied):
        ts.publish_sales_terms(vendor, values(), actor=staff, ctx=CTX)
    stranger = UserFactory.create()
    with pytest.raises(PermissionDenied):
        ts.publish_sales_terms(vendor, values(), actor=stranger, ctx=CTX)


def test_an_empty_return_cost_setting_means_the_seller_pays() -> None:
    vendor = VendorFactory.create()
    ts.publish_sales_terms(vendor, values(), actor=owner_of(vendor), ctx=CTX)
    assert ts.recap(current(vendor), buyer_is_business=False).consumer_pays_return is False


def test_the_consumer_pays_the_return_only_with_terms_and_information() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    ts.publish_sales_terms(
        vendor,
        values(return_cost_bearer="consumer", return_cost_info="The buyer pays the carrier."),
        actor=actor,
        ctx=CTX,
    )
    assert ts.recap(current(vendor), buyer_is_business=False).consumer_pays_return
    ts.publish_sales_terms(
        vendor, values(return_cost_bearer="consumer", return_cost_info=""), actor=actor, ctx=CTX
    )
    assert not ts.recap(current(vendor), buyer_is_business=False).consumer_pays_return


def test_the_b2b_court_is_never_shown_to_a_consumer() -> None:
    vendor = VendorFactory.create(registered_city="Bergamo")
    ts.publish_sales_terms(vendor, values(b2b_court="Milano"), actor=owner_of(vendor), ctx=CTX)
    version = current(vendor)
    consumer = ts.recap(version, buyer_is_business=False)
    assert consumer.b2b_court is None
    assert consumer.consumer_court_rule == "residence"
    assert ts.recap(version, buyer_is_business=True).b2b_court == "Milano"


def test_an_empty_b2b_court_falls_back_to_the_registered_office() -> None:
    vendor = VendorFactory.create(registered_city="Bergamo")
    ts.publish_sales_terms(vendor, values(b2b_court=""), actor=owner_of(vendor), ctx=CTX)
    assert ts.recap(current(vendor), buyer_is_business=True).b2b_court == "Bergamo"


def test_no_refund_handling_fee_field_exists() -> None:
    names = {f.name for f in SalesTermsVersion._meta.get_fields()}
    assert not [n for n in names if "fee" in n or "handling" in n or "refund" in n]


def test_the_version_in_force_is_readable_without_an_order_and_a_superseded_one_is_not() -> None:
    vendor = VendorFactory.create()
    actor = owner_of(vendor)
    ts.publish_sales_terms(vendor, values(body="Old text"), actor=actor, ctx=CTX)
    old = current(vendor)
    old_link = ts.terms_url(old)
    ts.publish_sales_terms(vendor, values(body="New text"), actor=actor, ctx=CTX)
    new = current(vendor)
    client = Client()
    response = client.get(ts.terms_url(new))
    assert response.status_code == 200
    assert b"New text" in response.content
    assert client.get(ts.terms_url(new)).status_code == 200  # multi-use
    assert client.get(old_link).status_code == 404
    assert b"Old text" not in client.get(old_link).content
    assert client.get(reverse("vendor_terms:read", args=["tampered"])).status_code == 404


def test_every_read_is_logged_and_a_denied_one_too() -> None:
    vendor = VendorFactory.create()
    ts.publish_sales_terms(vendor, values(), actor=owner_of(vendor), ctx=CTX)
    link = ts.terms_url(current(vendor))
    Client().get(link)
    Client().get(reverse("vendor_terms:read", args=["tampered"]))
    reads = AuditLog.objects.filter(action=ts.READ)
    results = [(e.after or {})["result"] for e in reads.order_by("id")]
    assert results == ["ok", "denied"]
    assert "@" not in str(list(reads.values_list("actor_label", flat=True)))


def test_a_vendor_without_terms_has_none_in_force() -> None:
    assert ts.version_in_force(VendorFactory.create()) is None
    assert ts.can_sell_by_purchase(VendorFactory.create()) is False


def test_the_vendor_area_form_publishes_a_version_behind_a_session_level() -> None:
    from apps.accounts import levels
    from apps.accounts.tests.mfa import verified_login

    member = VendorMemberFactory.create(role="owner")
    client = Client()
    url = reverse("manager:shop:sales_terms")
    data = {
        "body": "Terms from the form",
        "clauses": "7 | Limitation of liability",
        "governing_law": "IT",
        "b2b_court": "",
        "delivery_days": "3",
        "return_cost_bearer": "",
        "return_cost_info": "",
    }
    verified_login(client, member.user, level=levels.NO_WINDOW)
    assert client.get(url).status_code == 200
    assert client.post(url, data).status_code == 403  # group 2: needs the passkey window
    assert not SalesTermsVersion.objects.exists()
    verified_login(client, member.user, level=levels.PASSKEY_WINDOW)
    invalid = client.post(url, data | {"clauses": ""})
    assert invalid.status_code == 200
    assert not SalesTermsVersion.objects.exists()
    assert client.post(url, data).status_code == 302
    version = SalesTermsVersion.objects.get()
    assert version.vendor_id == member.vendor_id
    assert version.clauses[0]["article"] == "7"
    page = client.get(url)
    assert b"Terms from the form" in page.content


def test_staff_of_the_vendor_cannot_open_the_sales_terms_form() -> None:
    member = VendorMemberFactory.create(role="staff")
    client = Client()
    from apps.accounts.tests.mfa import verified_login

    verified_login(client, member.user)
    assert client.get(reverse("manager:shop:sales_terms")).status_code == 404


def test_the_factory_makes_a_version_in_force_and_one_that_is_not() -> None:
    vendor = VendorFactory.create()
    first = SalesTermsVersionFactory.create(vendor=vendor)
    assert ts.version_in_force(vendor) == first
    second = SalesTermsVersionFactory.create(vendor=vendor, in_force=False)
    assert second.number == 2
    assert ts.version_in_force(vendor) == first


def test_the_pointer_factory_points_a_vendor_at_a_version() -> None:
    pointer = SalesTermsInForceFactory.create()
    assert ts.version_in_force(pointer.vendor) == pointer.version
