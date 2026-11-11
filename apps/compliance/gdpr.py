"""Operational GDPR for customers (CMP.4 1/2, owner decisions D-29 and D-30, spec 11).

`export_customer_data` collects the customer's own data (access and portability, GDPR articles
15 and 20). `anonymise_customer` is the account erasure: it blocks the login, deletes the data
under no retention duty (carts, second factors, email contexts) and moves concluded orders to
`restricted_tax_only` through the ORD.2 scrub service (`orders.services.restrict_order`), which
is the only thing that ever touches an order. The fiscal snapshot is never rewritten here: it
stays until its 10 years end (CMP.29 retention job). With an open order the request is accepted
at once and the profile is anonymised when the last one closes (rerun the same call; CMP.4 2/2
schedules it). Both services are idempotent, take `dry_run`, and write `AuditLog` entries that
hold no personal data (public ids, counts and flags only). Vendor members are out of scope here
(Q-16, CMP.4 2/2).
"""

import datetime as dt
import json
from typing import Any

from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django_otp import devices_for_user

from apps.accounts.models import ElevationGrant, User
from apps.cart.models import Cart
from apps.core.audit import AuditContext, record
from apps.core.changeset import Change, ChangeSet
from apps.core.models import AuditLog, OutboxEmail, OutboxEmailEvent
from apps.core.services import audit_changes
from apps.orders import engine
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.vendors.models import ContractAcceptance, SelfCertification, VendorMember

from .models import ErasureRequest

EXPORT_FORMAT_VERSION = 1
ANONYMISED_DOMAIN = "anonymised.invalid"
# Orders that still have a payment, shipment, return or withdrawal window ahead (D-30).
CONCLUDED_ORDER_STATES = frozenset({"closed", "cancelled", "refunded"})
# Never paid and still in checkout: not "open" for the erasure; cancelled, then anonymised at
# once (D-30). `paid_at` is set by the database the first time an order is paid.
_NEVER_PAID_LIVE_STATES = ("checkout", "quote_flow", "awaiting_payment")

ACTION_EXPORT = "compliance.gdpr_export"
ACTION_ERASURE = "compliance.gdpr_erasure"


class VendorLinkedUser(ValueError):
    """The user is tied to a vendor (membership, contract, certification): the vendor-owner
    erasure (Q-16, restriction instead of deletion) handles these accounts."""


class _DryRunRollback(Exception):
    pass


def is_anonymised(user: User) -> bool:
    """True once the erasure of this account completed (the request row says so, not the
    address: anyone may register an address in the placeholder domain)."""
    return ErasureRequest.objects.filter(user=user, completed_at__isnull=False).exists()


def _tombstone_email(user: User) -> str:
    return f"erased-{user.public_id}@{ANONYMISED_DOMAIN}"


def _orders(user: User) -> QuerySet[Order]:
    return Order.objects.filter(customer=user)


def _has_vendor_ties(user: User) -> bool:
    return (
        VendorMember.objects.filter(user=user).exists()
        or ContractAcceptance.objects.filter(user=user).exists()
        or SelfCertification.objects.filter(user=user).exists()
    )


# --- export -----------------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _export_order(order: Order) -> dict[str, Any]:
    return {
        "number": order.number,
        "placed_at": _iso(order.created_at),
        "state": order.state,
        "privacy_status": order.privacy_status,
        "tax_retention_until": _iso(order.tax_retention_until),
        "customer_name": order.customer_name,
        "customer_vat_number": order.customer_vat_number,
        "customer_tax_id": order.customer_tax_id,
        "email": order.email,
        "language": order.language,
        "billing_address": order.billing_address,
        "billing_country": str(order.billing_country),
        "shipping_address": order.shipping_address,
        "shipping_country": str(order.shipping_country),
        "vat_validation": order.vat_validation,
        "goods_net": str(order.goods_net),
        "shipping_net": str(order.shipping_net),
        "tax_total": str(order.tax_total),
        "grand_total": str(order.grand_total),
        "vendor_orders": [
            {
                "vendor": str(sub.vendor.public_id),
                "state": sub.state,
                "gross_total": str(sub.gross_total),
                "carrier_contact_phone": sub.carrier_contact_phone,
                "lines": [
                    {
                        "sku": line.sku,
                        "title": line.title,
                        "variant_label": line.variant_label,
                        "quantity": line.quantity,
                        "unit_price_net": str(line.unit_price_net),
                        "net_total": str(line.net_total),
                        "tax_rate": str(line.tax_rate),
                        "tax_amount": str(line.tax_amount),
                    }
                    for line in sub.lines.all()
                ],
            }
            for sub in order.vendor_orders.select_related("vendor").prefetch_related("lines")
        ],
    }


def export_customer_data(user: User, *, ctx: AuditContext) -> dict[str, Any]:
    """The customer's own data, one section per model that holds personal data. Only rows that
    belong to `user` (by foreign key, or by the account's own email for the outbox) are read.
    Audited as `compliance.gdpr_export` with counts only."""
    orders = list(_orders(user).order_by("created_at", "id"))
    carts = [
        {
            "created_at": _iso(cart.created_at),
            "language": cart.language,
            "country_hint": str(cart.country_hint),
            "lines": [
                {
                    "product": str(line.product.public_id),
                    "title": str(line.product),
                    "quantity": line.quantity,
                    "added_price_net": str(line.added_price_net),
                }
                for line in cart.lines.select_related("product")
            ],
        }
        for cart in Cart.objects.filter(user=user).prefetch_related("lines")
    ]
    memberships = [
        {"vendor": str(member.vendor.public_id), "role": member.role}
        for member in VendorMember.objects.filter(user=user).select_related("vendor")
    ]
    emails = [
        {
            "template_key": mail.template_key,
            "status": mail.status,
            "language": mail.language,
            "created_at": _iso(mail.created_at),
            "sent_at": _iso(mail.sent_at),
        }
        for mail in OutboxEmail.objects.filter(recipient__iexact=user.email).order_by("id")
    ]
    devices = [
        {"kind": type(device).__name__, "name": device.name}
        for device in devices_for_user(user, confirmed=None)
    ]
    audit_entries = [
        {
            "at": _iso(entry.created_at),
            "action": entry.action,
            "target_model": entry.target_model,
            "ip": entry.ip,
        }
        for entry in AuditLog.objects.filter(actor_id=user.pk).order_by("id")
    ]
    data: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "generated_at": timezone.now().isoformat(),
        "profile": {
            "email": user.email,
            "full_name": user.full_name,
            "preferred_language": user.preferred_language,
            "date_joined": _iso(user.date_joined),
        },
        "carts": carts,
        "orders": [_export_order(order) for order in orders],
        "vendor_memberships": memberships,
        "emails": emails,
        "devices": devices,
        "audit_entries": audit_entries,
    }
    counts = {
        key: len(data[key])
        for key in ("carts", "orders", "vendor_memberships", "emails", "devices", "audit_entries")
    }
    record(ctx, ACTION_EXPORT, "accounts.user", str(user.public_id), None, counts)
    return data


def export_customer_json(user: User, *, ctx: AuditContext) -> str:
    return json.dumps(export_customer_data(user, ctx=ctx), indent=2, ensure_ascii=False)


# --- erasure ----------------------------------------------------------------------------------


def _user_state(user: User) -> dict[str, Any]:
    return {
        "active": user.is_active,
        "usable_password": user.has_usable_password(),
        "anonymised": is_anonymised(user),
        "staff": user.is_staff or user.is_superuser,
    }


def _never_paid_and_dead(order: Order, now: dt.datetime) -> bool:
    """A checkout that can no longer be paid: `checkout` and `quote_flow` never reached the
    payment provider; an `awaiting_payment` order may still be captured there, so it counts only
    once it is older than the payment window (`engine.ABANDONED_AFTER_DAYS`, the same rule that
    lets the scrub service anonymise it at once). A younger one keeps the erasure pending."""
    if order.paid_at is not None:
        return False
    if order.state in ("cancelled", "checkout", "quote_flow"):
        return True
    return order.state == "awaiting_payment" and now - order.created_at > dt.timedelta(
        days=engine.ABANDONED_AFTER_DAYS
    )


def _anonymise_never_paid(order: Order, ctx: AuditContext, stamp: dt.datetime) -> list[Change]:
    """A checkout that never reached `paid`: cancel it, then anonymise it at once (D-30)."""
    changes: list[Change] = []
    if order.state != "cancelled":
        if order.state == "checkout" or order.state == "quote_flow":
            changes += order_services.transition_order(order, "awaiting_payment", ctx=ctx).changes
        changes += order_services.transition_order(order, "cancelled", ctx=ctx).changes
    if order.privacy_status != "anonymized":
        changes += order_services.anonymise_order(order, ctx=ctx, now=stamp).changes
    return changes


def _purge_sessions(user: User) -> None:
    key = str(user.pk)
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == key:
            session.delete()


def _erase_outbox(address: str, tombstone: str) -> None:
    """Cancel mail still waiting for this address, then empty every row's recipient and
    context. The append-only event rows may hold provider feedback text (documented in
    docs/dev/audit-trail.md)."""
    rows = OutboxEmail.objects.filter(recipient__iexact=address)
    for row in rows.exclude(status__in=OutboxEmail.FINAL):
        row.status = OutboxEmail.Status.SUPPRESSED
        row.next_attempt_at = None
        row.save(update_fields=["status", "next_attempt_at", "updated_at"])
        OutboxEmailEvent.objects.create(
            email=row, status=OutboxEmailEvent.Kind.SUPPRESSED, detail="account erased"
        )
    rows.update(recipient=tombstone, context="")


def anonymise_customer(
    user: User, *, ctx: AuditContext, dry_run: bool = False, now: dt.datetime | None = None
) -> ChangeSet:
    """Erase a customer account (D-29, D-30). Accepted at once, never refused for open orders.

    Now: the login is blocked (inactive, unusable password), carts and second factors are
    deleted, concluded orders go to `restricted_tax_only` (`reason="account_erasure"`, amounts
    and fiscal fields untouched). When no order is open the profile is anonymised (fixed
    placeholder email, empty name), the account's outbox rows lose their recipient and context,
    and the request completes. With an open order the profile waits: call again later.
    Idempotent: a repeat changes nothing and writes nothing. Raises `VendorLinkedUser` for an
    account tied to a vendor."""
    if _has_vendor_ties(user):
        raise VendorLinkedUser("vendor members and signers are handled by the vendor erasure")
    stamp = now or timezone.now()
    changeset = ChangeSet()
    own = ChangeSet()
    try:
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if is_anonymised(locked):
                return changeset
            key = str(locked.public_id)
            before = _user_state(locked)
            for cart in Cart.objects.filter(user=locked):
                own.delete("cart.cart", str(cart.public_id), {"lines": cart.lines.count()})
            for device in devices_for_user(locked, confirmed=None):
                own.delete(
                    f"accounts.{type(device).__name__.lower()}",
                    str(getattr(device, "public_id", device.pk)),
                    {},
                )
            open_orders = 0
            for order in _orders(locked).order_by("id"):
                if _never_paid_and_dead(order, stamp):
                    changeset.changes.extend(_anonymise_never_paid(order, ctx, stamp))
                elif order.state not in CONCLUDED_ORDER_STATES:
                    open_orders += 1
                elif order.privacy_status == "active":
                    changeset.changes.extend(
                        order_services.restrict_order(
                            order, ctx=ctx, reason="account_erasure", now=stamp
                        ).changes
                    )
            Cart.objects.filter(user=locked).delete()
            ElevationGrant.objects.filter(user=locked).delete()
            for device in list(devices_for_user(locked, confirmed=None)):
                device.delete()
            complete = open_orders == 0
            old_email = locked.email
            _purge_sessions(locked)
            locked.groups.clear()
            locked.user_permissions.clear()
            values: dict[str, Any] = {
                "is_active": False,
                "is_staff": False,
                "is_superuser": False,
                "updated_at": stamp,
            }
            if complete:
                values.update(email=_tombstone_email(locked), full_name="", date_joined=stamp)
            User.objects.filter(pk=locked.pk).update(**values)
            locked.refresh_from_db()
            if locked.has_usable_password():
                locked.set_unusable_password()
                locked.save(update_fields=["password"])
            _erase_outbox(old_email, _tombstone_email(locked))
            own.update("accounts.user", key, before, _user_state(locked))
            request, _ = ErasureRequest.objects.get_or_create(
                user=locked, defaults={"requested_at": stamp}
            )
            if complete and request.completed_at is None:
                request.completed_at = stamp
                request.save(update_fields=["completed_at", "updated_at"])
            changeset.changes.extend(own.changes)
            if dry_run:
                raise _DryRunRollback  # rolls back the sub-services' writes and audit rows too
            audit_changes(own, ctx=ctx, action=ACTION_ERASURE)
    except _DryRunRollback:
        pass
    return changeset
