"""CMP.4 (1/2), owner decisions D-29 and D-30: a customer's data export and the erasure that
anonymises the account, never touches an order snapshot except the move to
`restricted_tax_only`, and completes only when the last open order closes."""

import datetime as dt
import json
from decimal import Decimal

import pytest

from apps.accounts.models import PasskeyDevice, User
from apps.accounts.tests.factories import UserFactory
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.compliance import gdpr
from apps.compliance.models import ErasureRequest
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.orders.services import LineInput, VendorOrderInput
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
PLACED = dt.datetime(2027, 3, 15, 10, 0, tzinfo=dt.UTC)
COMMISSION = {"rule_id": "default", "version": 1, "percent": "10.00", "base": "goods"}


def place(user: User | None, *, state: str = "closed", email: str = "") -> Order:
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
        state="completed" if state in {"closed", "refunded"} else "pending",
    )
    order, _ = order_services.create_order(
        email=email or (user.email if user else "guest@example.com"),
        language="it",
        billing_address={"line1": "1 Via Test", "city": "Roma"},
        billing_country="IT",
        shipping_address={"line1": "2 Via Test", "city": "Milano"},
        shipping_country="IT",
        customer=user,
        customer_name="Mario Rossi",
        customer_vat_number="IT12345678901",
        vendor_orders=[sub],
        ctx=CTX,
        state=state,
        legacy_wp_order_id=None if state == "checkout" else 9000 + Order.objects.count(),
        now=PLACED,
    )
    assert order is not None
    return order


def dump(user: User) -> str:
    return json.dumps(gdpr.export_customer_data(user, ctx=CTX), default=str)


# --- export -----------------------------------------------------------------------------------


def test_the_export_holds_every_model_with_the_customers_personal_data() -> None:
    user = UserFactory.create(full_name="Mario Rossi", email="mario@example.com")
    CartLineFactory.create(cart=CartFactory.create(user=user, session_key=""))
    order = place(user)
    OutboxEmail.objects.create(
        recipient=user.email, template_key="order_confirmation", language="it", context="{}"
    )
    vendor = VendorFactory.create()
    VendorMemberFactory.create(vendor=vendor, user=user, role="staff")

    data = gdpr.export_customer_data(user, ctx=CTX)

    assert data["profile"]["email"] == "mario@example.com"
    assert data["profile"]["full_name"] == "Mario Rossi"
    assert len(data["carts"]) == 1
    assert len(data["carts"][0]["lines"]) == 1
    [exported_order] = data["orders"]
    assert exported_order["number"] == order.number
    assert exported_order["customer_name"] == "Mario Rossi"
    assert exported_order["billing_address"]["city"] == "Roma"
    assert exported_order["vendor_orders"][0]["lines"][0]["title"] == "Cup"
    assert data["vendor_memberships"][0]["role"] == "staff"
    assert data["emails"][0]["template_key"] == "order_confirmation"
    assert {entry["action"] for entry in data["audit_entries"]} >= set()  # key exists
    assert data["format_version"] == 1


def test_the_export_is_json_serialisable_and_has_no_secrets() -> None:
    user = UserFactory.create()
    text = gdpr.export_customer_json(user, ctx=CTX)
    assert json.loads(text)["profile"]["email"] == user.email
    assert "password" not in text
    assert user.password not in text


def test_the_export_never_includes_another_customers_data() -> None:
    mine = UserFactory.create(email="mine@example.com")
    other = UserFactory.create(email="other@example.com", full_name="Zed Other")
    place(mine)
    place(other)
    CartLineFactory.create(cart=CartFactory.create(user=other, session_key=""))
    OutboxEmail.objects.create(
        recipient=other.email, template_key="order_confirmation", language="en", context="{}"
    )
    text = dump(mine)
    assert "other@example.com" not in text
    assert "Zed Other" not in text
    assert len(json.loads(text)["orders"]) == 1
    assert json.loads(text)["carts"] == []


def test_an_export_is_audited_without_personal_data() -> None:
    user = UserFactory.create(email="audited@example.com", full_name="Ada Lovelace")
    place(user)
    gdpr.export_customer_data(user, ctx=CTX)
    entry = AuditLog.objects.get(action="compliance.gdpr_export")
    assert entry.target_key == str(user.public_id)
    blob = json.dumps([entry.before, entry.after])
    assert "audited@example.com" not in blob
    assert "Ada Lovelace" not in blob
    assert entry.after["orders"] == 1


def test_the_export_of_an_anonymised_account_shows_the_placeholders() -> None:
    user = UserFactory.create(email="gone@example.com")
    gdpr.anonymise_customer(user, ctx=CTX)
    assert "gone@example.com" not in dump(User.objects.get(pk=user.pk))


# --- anonymisation ----------------------------------------------------------------------------


def test_erasure_anonymises_the_profile_and_deletes_the_non_fiscal_data() -> None:
    user = UserFactory.create(email="mario@example.com", full_name="Mario Rossi")
    CartLineFactory.create(cart=CartFactory.create(user=user, session_key=""))
    PasskeyDevice.objects.create(
        user=user, name="phone", credential_id="cred-1", public_key=b"k", sign_count=0
    )
    OutboxEmail.objects.create(
        recipient=user.email,
        template_key="order_confirmation",
        language="it",
        context='{"name": "Mario"}',
        status="sent",
    )
    gdpr.anonymise_customer(user, ctx=CTX)

    user.refresh_from_db()
    assert user.email != "mario@example.com"
    assert user.email.endswith("@anonymised.invalid")
    assert user.full_name == ""
    assert not user.is_active
    assert not user.has_usable_password()
    assert user.carts.count() == 0
    assert PasskeyDevice.objects.filter(user=user).count() == 0
    mail = OutboxEmail.objects.get()
    assert mail.recipient != "mario@example.com"
    assert "Mario" not in mail.context
    assert ErasureRequest.objects.get(user=user).completed_at is not None


def test_erasure_keeps_the_fiscal_snapshot_and_restricts_concluded_orders() -> None:
    user = UserFactory.create()
    first, second = place(user), place(user)
    before = {
        o.pk: (
            o.customer_name,
            o.customer_vat_number,
            o.email,
            o.billing_address,
            o.grand_total,
            o.tax_retention_until,
        )
        for o in (first, second)
    }

    gdpr.anonymise_customer(user, ctx=CTX)

    for order in Order.objects.filter(pk__in=before):
        assert order.privacy_status == "restricted_tax_only"
        assert (
            order.customer_name,
            order.customer_vat_number,
            order.email,
            order.billing_address,
            order.grand_total,
            order.tax_retention_until,
        ) == before[order.pk]


def test_erasure_is_idempotent_and_irreversible() -> None:
    user = UserFactory.create(email="mario@example.com")
    place(user)
    gdpr.anonymise_customer(user, ctx=CTX)
    user.refresh_from_db()
    snapshot = (user.email, user.full_name, user.password, user.is_active)
    audit_rows = AuditLog.objects.count()

    changeset = gdpr.anonymise_customer(user, ctx=CTX)

    user.refresh_from_db()
    assert changeset.is_empty
    assert (user.email, user.full_name, user.password, user.is_active) == snapshot
    assert AuditLog.objects.count() == audit_rows
    assert ErasureRequest.objects.filter(user=user).count() == 1


def test_erasure_with_an_open_order_is_accepted_and_completes_when_it_closes() -> None:
    user = UserFactory.create(email="mario@example.com")
    closed = place(user)
    open_order = place(user, state="checkout")

    gdpr.anonymise_customer(user, ctx=CTX)

    user.refresh_from_db()
    assert not user.is_active
    assert not user.has_usable_password()
    assert user.email == "mario@example.com"  # the profile waits for the open order
    assert Order.objects.get(pk=closed.pk).privacy_status == "restricted_tax_only"
    request = ErasureRequest.objects.get(user=user)
    assert request.completed_at is None

    # still open: a rerun changes nothing
    assert gdpr.anonymise_customer(user, ctx=CTX).is_empty

    Order.objects.filter(pk=open_order.pk).update(state="cancelled")
    gdpr.anonymise_customer(user, ctx=CTX)
    user.refresh_from_db()
    assert user.email.endswith("@anonymised.invalid")
    request.refresh_from_db()
    assert request.completed_at is not None
    assert Order.objects.get(pk=open_order.pk).privacy_status == "restricted_tax_only"


def test_erasure_dry_run_lists_the_changes_and_writes_nothing() -> None:
    user = UserFactory.create(email="mario@example.com")
    order = place(user)
    CartLineFactory.create(cart=CartFactory.create(user=user, session_key=""))
    audit_rows = AuditLog.objects.count()

    changeset = gdpr.anonymise_customer(user, ctx=CTX, dry_run=True)

    models = {change.model for change in changeset.changes}
    assert {"accounts.user", "cart.cart", "orders.order"} <= models
    user.refresh_from_db()
    assert user.email == "mario@example.com"
    assert user.is_active
    assert Order.objects.get(pk=order.pk).privacy_status == "active"
    assert AuditLog.objects.count() == audit_rows
    assert not ErasureRequest.objects.exists()


def test_erasure_never_touches_another_customer() -> None:
    mine, other = UserFactory.create(), UserFactory.create(email="other@example.com")
    other_order = place(other)
    CartLineFactory.create(cart=CartFactory.create(user=other, session_key=""))
    gdpr.anonymise_customer(mine, ctx=CTX)
    other.refresh_from_db()
    assert other.email == "other@example.com"
    assert other.is_active
    assert other.carts.count() == 1
    assert Order.objects.get(pk=other_order.pk).privacy_status == "active"


def test_erasure_is_audited_without_personal_data() -> None:
    user = UserFactory.create(email="audited@example.com", full_name="Ada Lovelace")
    place(user)
    gdpr.anonymise_customer(user, ctx=CTX)
    entries = AuditLog.objects.filter(action="compliance.gdpr_erasure")
    assert entries.exists()
    blob = json.dumps([[e.before, e.after, e.target_key] for e in entries])
    assert "audited@example.com" not in blob
    assert "Ada Lovelace" not in blob


def test_a_user_with_vendor_ties_is_not_erased_here() -> None:
    user = UserFactory.create()
    VendorMemberFactory.create(vendor=VendorFactory.create(), user=user, role="owner")
    with pytest.raises(gdpr.VendorLinkedUser):
        gdpr.anonymise_customer(user, ctx=CTX)
    user.refresh_from_db()
    assert user.is_active
