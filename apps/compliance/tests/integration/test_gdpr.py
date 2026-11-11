"""CMP.4 (1/2), owner decisions D-29 and D-30: a customer's data export and the erasure that
anonymises the account, never touches an order snapshot except the move to
`restricted_tax_only`, and completes only when the last open order closes."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session

from apps.accounts.models import PasskeyDevice, User
from apps.accounts.tests.factories import UserFactory
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.compliance import gdpr
from apps.compliance.models import ErasureRequest
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail, OutboxEmailEvent
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.orders.services import LineInput, VendorOrderInput
from apps.orders.tests.factories import unchecked
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
PLACED = dt.datetime(2027, 3, 15, 10, 0, tzinfo=dt.UTC)
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
}


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
        legacy_wp_order_id=None
        if state in {"checkout", "awaiting_payment"}
        else 9000 + Order.objects.count(),
        paid_at=None if state in {"checkout", "awaiting_payment", "cancelled"} else PLACED,
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
    assert (entry.after or {})["orders"] == 1


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
    open_order = place(user, state="in_fulfillment")

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

    order_services.transition_order(open_order, "fulfilled", ctx=CTX)
    order_services.transition_order(open_order, "closed", ctx=CTX)
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


# --- privacy review of #256 --------------------------------------------------------------------


def test_erasure_cancels_queued_and_retrying_mail_and_clears_every_row() -> None:
    user = UserFactory.create(email="mario@example.com")
    rows = [
        OutboxEmail.objects.create(
            recipient=user.email,
            template_key="order_confirmation",
            language="it",
            context='{"name": "Mario"}',
            status=status,
        )
        for status in ("queued", "retrying", "sent", "failed")
    ]
    place(user, state="in_fulfillment")  # pending path: the cancel must not wait for completion

    gdpr.anonymise_customer(user, ctx=CTX)

    for row in OutboxEmail.objects.filter(pk__in=[r.pk for r in rows]):
        assert row.recipient != "mario@example.com"
        assert row.context == ""
        assert row.status in OutboxEmail.FINAL
    assert OutboxEmail.objects.filter(status="suppressed").count() == 2
    assert OutboxEmailEvent.objects.filter(status="suppressed").count() == 2


def test_an_abandoned_never_paid_order_does_not_block_the_erasure() -> None:
    user = UserFactory.create(email="mario@example.com")
    place(user, state="checkout")
    old = place(user, state="awaiting_payment")
    long_ago = PLACED - dt.timedelta(days=45)
    with unchecked():
        Order.objects.filter(pk=old.pk).update(created_at=long_ago)

    gdpr.anonymise_customer(user, ctx=CTX)

    user.refresh_from_db()
    assert user.email.endswith("@anonymised.invalid")
    assert ErasureRequest.objects.get(user=user).completed_at is not None
    for order in Order.objects.filter(customer=user):
        assert order.privacy_status == "anonymized"
        assert order.customer_name == "ANONYMISED"


def test_erasure_purges_the_users_sessions_and_keeps_other_sessions() -> None:
    user, other = UserFactory.create(), UserFactory.create()
    keys = []
    for owner in (user, other):
        store = SessionStore()
        store["_auth_user_id"] = str(owner.pk)
        store.create()
        keys.append(store.session_key)
    gdpr.anonymise_customer(user, ctx=CTX)
    assert not Session.objects.filter(session_key=keys[0]).exists()
    assert Session.objects.filter(session_key=keys[1]).exists()


def test_erasure_strips_staff_flags_groups_and_permissions() -> None:
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.create(name="ops"))
    user.is_superuser = True
    user.save()
    gdpr.anonymise_customer(user, ctx=CTX)
    user.refresh_from_db()
    assert not user.is_staff
    assert not user.is_superuser
    assert user.groups.count() == 0
    assert user.user_permissions.count() == 0


def test_an_address_in_the_placeholder_domain_does_not_fake_an_erasure() -> None:
    user = UserFactory.create(email="someone@anonymised.invalid", full_name="Real Person")
    gdpr.anonymise_customer(user, ctx=CTX)
    user.refresh_from_db()
    assert user.full_name == ""
    assert user.email != "someone@anonymised.invalid"


def test_a_pending_rerun_writes_no_audit_row() -> None:
    user = UserFactory.create()
    place(user, state="in_fulfillment")
    gdpr.anonymise_customer(user, ctx=CTX)
    rows = AuditLog.objects.count()
    assert gdpr.anonymise_customer(user, ctx=CTX).is_empty
    assert AuditLog.objects.count() == rows


def test_every_model_pointing_at_the_user_has_an_export_and_erasure_decision() -> None:
    """A new foreign key to the user model must be handled by the export and the erasure (or
    listed here with the reason it is not personal data): fails until someone decides."""
    handled = {
        "cart.cart": "exported, deleted",
        "orders.order": "exported, restricted or anonymised through ORD.2",
        "accounts.passkeydevice": "exported by name, deleted",
        "accounts.recoverycodedevice": "exported by name, deleted",
        "accounts.actionlinkuse": "digests and keys only; the user row stays as the tombstone",
        "accounts.actionlinkissue": "digests and keys only; the user row stays as the tombstone",
        "compliance.erasurerequest": "the request itself, no personal data",
        "vendors.vendormember": "vendor-linked users are refused (Q-16, CMP.4 2/2)",
        "vendors.contractacceptance": "vendor-linked users are refused (Q-16, CMP.4 2/2)",
        "vendors.selfcertification": "vendor-linked users are refused (Q-16, CMP.4 2/2)",
        "vendors.vendorapplication": "applicant, purged by its own retention job",
        "vendors.vendorclaim": "vendor side, CMP.4 2/2",
        "events.eventpage": "staff moderator reference, not the customer's data",
        "shipping.shippingconfigversion": "staff author reference",
        "tax.vendortaxprofile": "vendor side, CMP.4 2/2",
        "accounts.user_groups": "stripped on erasure",
        "accounts.user_user_permissions": "stripped on erasure",
        "tax.taxregimechangerequest": "vendor side, CMP.4 2/2",
        "core.mediaimage": "staff upload reference (Wagtail owner field)",
    }
    handled |= {
        f"cms.{name}": "staff page owner (Wagtail), not the customer's data"
        for name in [
            "blogindexpage",
            "blogpostpage",
            "homepage",
            "legalpage",
            "standardpage",
            "termlandingpage",
            "vendorlandingpage",
            "vendorrecruitmentpage",
        ]
    }
    handled |= {
        "events.eventindexpage": "staff page owner (Wagtail), not the customer's data",
    }
    found = set()
    for model in django_apps.get_models(include_auto_created=True):
        if not model.__module__.startswith("apps."):
            continue
        for field in model._meta.get_fields():
            target = field.related_model
            if (
                field.concrete
                and (field.many_to_one or field.one_to_one)
                and isinstance(target, type)
                and target._meta.label == settings.AUTH_USER_MODEL
            ):
                found.add(model._meta.label_lower)
    assert sorted(found - set(handled)) == [], "decide export and erasure for these models"


def test_a_recent_awaiting_payment_order_may_still_be_captured_so_the_erasure_waits() -> None:
    user = UserFactory.create(email="mario@example.com")
    recent = place(user, state="awaiting_payment")
    gdpr.anonymise_customer(user, ctx=CTX, now=PLACED + dt.timedelta(days=1))
    user.refresh_from_db()
    assert not user.is_active  # blocked at once
    assert user.email == "mario@example.com"  # not anonymised yet
    assert Order.objects.get(pk=recent.pk).state == "awaiting_payment"
    assert Order.objects.get(pk=recent.pk).privacy_status == "active"
    assert ErasureRequest.objects.get(user=user).completed_at is None

    # past the payment window a rerun cancels and anonymises it, and completes
    gdpr.anonymise_customer(user, ctx=CTX, now=PLACED + dt.timedelta(days=45))
    user.refresh_from_db()
    assert user.email.endswith("@anonymised.invalid")
    assert Order.objects.get(pk=recent.pk).privacy_status == "anonymized"
