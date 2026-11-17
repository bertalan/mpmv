"""Order amendments, part 1 of ORD.20 (owner decision D-47; spec 07 §Order amendments).

`draft_amendment` checks who acts and on which package, replays the checkout on the current
version with the typed changes (`amendment_engine.recompute_vendor_order`), classifies the result
(`classify_amendment`, never the actor) and stores an `OrderAmendment` in `draft`. A change of
delivery address runs the checkout's eligibility and blocking checks on the new destination
(tax context and goods decisions, the vendor's shipping, Northern Ireland for a vendor above the
threshold without OSS) and an unsupported destination refuses the draft with a plain reason.

`apply_amendment` writes the next version and, for a decrease, the PAY.4 refund event in the same
transaction (ORD.20 part 2): the ledger carries out the refund through the hook named by the
setting `ORDER_AMENDMENT_REFUND_HOOK` (orders never imports the ledger). A critical amendment (an
address change, any refund) spends a fresh passkey grant bound to its digest (CORE.25). Every
apply first checks that the version did not move since the draft: otherwise the amendment
becomes `superseded` and nothing moves. After a partial unavailability the customer may cancel
the rest of the package (`cancel_rest_of_package`) and shipping is held meanwhile. A refund or a
withdrawal recorded on the package closes its open amendment (`supersede_open_amendment`,
`withdraw_open_amendment`), and a draft idle for an hour is discarded (`discard_idle_drafts`).
Every applied change and cancellation sends its notices (`amendment_notices`, ORD.20 part 3b): an
email to the customer and to the vendor, each kept as an `OrderEvent` (durable medium).

Like `checkout`, this module joins orders with the tax and shipping services; tax and shipping
never import orders.
"""

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.services import USER_MODEL
from apps.catalog.models import Product, Variant
from apps.core.audit import VENDOR_FIELD, AuditContext, record
from apps.core.changeset import Change, ChangeSet
from apps.core.countries import InvalidCountry
from apps.core.models import AuditLog
from apps.core.services import (
    PLATFORM_OWNER_PERMISSION,
    NoPlatformOperator,
    OverlappingPlatformOperators,
    audit_changes,
    current_operator,
)
from apps.shipping import engine as shipping_engine
from apps.shipping.services import quote_shipping
from apps.tax import engine as tax_engine
from apps.tax import services as tax_services
from apps.tax.models import Territory
from apps.vendors import engine as vendors_engine
from apps.vendors.models import Vendor
from apps.vendors.scoping import is_platform_staff
from apps.vendors.services import has_vendor_permission, member_role

from . import amendment_engine as am
from . import amendment_notices, engine, versions
from .models import (
    OPEN_AMENDMENT_STATES,
    AmendmentState,
    Order,
    OrderAmendment,
    OrderLine,
    VendorOrder,
    VendorOrderVersion,
)
from .services import (
    PAID_OR_LATER,
    OrdersFrozen,
    apply_refund_to_states,
    orders_frozen,
)
from .tz import business_tz

logger = logging.getLogger(__name__)
AmendmentRefused = am.AmendmentRefused
AmendmentBlocked = am.AmendmentBlocked

CUSTOMER_REQUEST_KEYS = ("channel", "date", "text")
# The Support role's permission (`apps.manager.roles`, CORE.18): orders never imports the Manager.
SUPPORT_PERMISSION = "manager.platform_support"


class _DryRunRollback(Exception):
    pass


# Counsel (2026-11-17, A2): the date until which the seller may not ship ("the seller will not
# ship it before {date}") is never less than this after the A2 notice is sent, so the customer
# has real time to read it and cancel the rest. The notice goes out when the change applies.
A2_MIN_WINDOW_HOURS = 24


# --- the operator's settings (D-47, owner 2026-11-16) -------------------------------------------


@dataclass(frozen=True)
class AmendmentSettings:
    """The three amendment settings of the `PlatformOperator` version in force (ARC.7)."""

    answer_hours: int
    comparable_delay_days: int
    cancel_hold_hours: int


DEFAULT_SETTINGS = AmendmentSettings(
    answer_hours=am.DEFAULT_ANSWER_HOURS,
    comparable_delay_days=am.DEFAULT_COMPARABLE_DELAY_DAYS,
    cancel_hold_hours=am.DEFAULT_CANCEL_HOLD_HOURS,
)


def amendment_settings(at: dt.datetime | None = None) -> AmendmentSettings:
    """The amendment settings in force at `at` (default now), read from the same configuration
    the Manager shows. Before any operator is configured, or when versions overlap, the code
    defaults apply (72 hours, 2 days, 24 hours)."""
    try:
        operator = current_operator(at)
    except (NoPlatformOperator, OverlappingPlatformOperators):
        return DEFAULT_SETTINGS
    return AmendmentSettings(
        answer_hours=operator.amendment_answer_hours,
        comparable_delay_days=operator.amendment_comparable_delay_days,
        cancel_hold_hours=operator.amendment_cancel_hold_hours,
    )


# --- who acts -----------------------------------------------------------------------------------


def _same_side(initiator: str, drafted_by: str) -> None:
    """The platform and the shop never act on each other's drafts (staff limits, passkeys)."""
    if initiator != drafted_by and (
        initiator in am.STAFF_INITIATORS or drafted_by in am.STAFF_INITIATORS
    ):
        raise AmendmentRefused(
            gettext(
                "This draft was started by platform staff: only that side may change or apply it."
            )
            if drafted_by in am.STAFF_INITIATORS
            else gettext(
                "This draft was started by the shop: only that side may change or apply it."
            )
        )


def _scoped(vendor_order: VendorOrder, actor: User) -> VendorOrder:
    """The package when `actor` may see it (CORE.3 scoping): another shop's is a 404."""
    found = VendorOrder.objects.for_actor(actor).filter(pk=vendor_order.pk).first()
    if found is None:
        raise Http404("No vendor order matches the given query.")
    return found


def initiator_of(actor: User, vendor_order: VendorOrder) -> str:
    """The role the actor amends with (D-47 (4)): the vendor's owner or a member with ORDERS,
    the platform owner or Support. Anyone else is refused."""
    role = member_role(actor, vendor_order.vendor)
    if role is not None:
        if not has_vendor_permission(actor, vendor_order.vendor, "orders"):
            raise PermissionDenied(
                gettext("Only the shop's owner or a member with orders may amend.")
            )
        return am.VENDOR_OWNER if role == vendors_engine.OWNER else am.VENDOR_MEMBER
    if is_platform_staff(actor):
        # Spec 07 roles and ORD.22: only the platform owner and Support amend; the Finance,
        # Catalog and Configuration roles see orders but never change them.
        if actor.has_perm(PLATFORM_OWNER_PERMISSION):
            return am.PLATFORM_OWNER
        if actor.has_perm(SUPPORT_PERMISSION):
            return am.SUPPORT
        raise PermissionDenied(
            gettext(
                "Only the platform owner or Support may change an order from the platform side."
            )
        )
    raise PermissionDenied(gettext("Only the shop or platform staff may amend an order."))


# --- the new destination: the checkout's checks (ORD.3) ----------------------------------------


def _place(country: str, postcode: str) -> str:
    """The place named in a refusal: the tax territory's name, Northern Ireland, or the country."""
    if tax_engine.is_northern_ireland(country, postcode):
        return "Northern Ireland"
    rule = tax_services.find_territory(country, postcode)
    if rule is not None:
        name = Territory.objects.filter(code=rule.code).values_list("name", flat=True).first()
        return str(name or rule.code)
    return country


def _offer(rate: shipping_engine.ShippingRate) -> am.ShippingOffer:
    return am.ShippingOffer(
        key=rate.key,
        label=rate.label,
        net=rate.net,
        carrier_code=rate.carrier_code,
        zone_code=rate.zone_code,
        min_days=rate.min_days,
        max_days=rate.max_days,
    )


def _package(sub: VendorOrder, snapshot: am.VersionSnapshot) -> shipping_engine.VendorPackage:
    """The package as the shipping engine reads it, from the weights snapshotted when the order
    was placed (never the live product). A legacy line without a weight counts 0 kg: the shipping
    engine then asks a quote, which refuses the change."""
    return shipping_engine.VendorPackage(
        vendor_id=sub.vendor_id,
        lines=tuple(
            shipping_engine.PackageLine(
                weight_kg=line.weight_kg or Decimal("0"),
                quantity=line.quantity,
                net_goods=line.net,
            )
            for line in snapshot.lines
        ),
    )


def _rates(
    sub: VendorOrder,
    snapshot: am.VersionSnapshot,
    destination: shipping_engine.Destination,
    place: str,
) -> tuple[shipping_engine.ShippingRate, ...]:
    """The exact rates of the package to `destination`, or a plain refusal."""
    outcome = quote_shipping(
        sub.vendor, _package(sub, snapshot), destination, language=sub.order.language
    )
    if isinstance(outcome, shipping_engine.Blocked):
        raise AmendmentBlocked(
            gettext(
                "The seller cannot deliver to %(place)s, so the address cannot be changed to it "
                "(%(why)s)"
            )
            % {"place": place, "why": outcome.message}
        )
    if isinstance(outcome, shipping_engine.NeedsQuote | shipping_engine.IndicativeRates):
        raise AmendmentBlocked(
            gettext(
                "The seller has no fixed shipping price for %(place)s, so the address cannot be "
                "changed to it: such a change would need a shipping quote, which amendments do "
                "not support yet."
            )
            % {"place": place}
        )
    return outcome.rates


def check_destination(
    sub: VendorOrder,
    snapshot: am.VersionSnapshot,
    address: Mapping[str, Any],
    *,
    address_before: Mapping[str, Any],
    categories: Mapping[str, str],
    at: dt.datetime,
) -> tuple[tax_engine.TaxContext, am.ShippingOffer]:
    """Check a new delivery address and quote the package's method again for it.

    The address must be complete and the package's shipping method must have an exact price
    there. When the address moves to another country or tax territory, the checks of a new
    checkout run too (spec 07, "Territory change"), the same as `checkout.build_checkout`: the
    tax context and a goods decision for every line (the tax engine's own rules, Northern Ireland
    included), and a business order is refused until the VIES check of the new destination exists
    (ORD.21). A correction inside the same territory runs no territory check. Returns the tax
    context at `at` for the new destination and the method quoted again; raises
    `AmendmentBlocked` with a plain reason otherwise."""
    missing = engine.missing_address_fields(address)
    if missing:
        raise AmendmentRefused(
            gettext("The new address is incomplete: %(fields)s is missing.")
            % {"fields": ", ".join(missing)}
        )
    country, postcode = str(address["country"]), str(address["postcode"])
    try:
        destination = shipping_engine.Destination(
            country, postcode, str(address.get("state") or "")
        )
    except InvalidCountry:
        raise AmendmentRefused(
            gettext("%(country)r is not a country the address can be in.") % {"country": country}
        ) from None
    place = _place(destination.country, postcode)

    def blocked(error: Exception) -> AmendmentBlocked:
        return AmendmentBlocked(
            gettext(
                "VAT cannot be decided for a delivery to %(place)s, so the address cannot be "
                "changed to it (%(why)s)."
            )
            % {"place": place, "why": error}
        )

    try:
        tax = tax_services.build_tax_context(
            sub.vendor,
            destination_country=destination.country,
            destination_postcode=postcode,
            at=at,
        )
    except (tax_engine.BlockingTaxError, LookupError) as error:
        raise blocked(error) from None
    territory_before = am.tax_territory(
        str(address_before.get("country") or ""),
        str(address_before.get("postcode") or ""),
        tax.territories,
    )
    moved = am.tax_territory(destination.country, postcode, tax.territories) != territory_before
    if moved:
        try:
            for category in set(categories.values()):
                tax_engine.resolve_goods_tax(tax, category)
        except (tax_engine.BlockingTaxError, LookupError) as error:
            raise blocked(error) from None
    if moved and sub.order.customer_tax_status != "b2c":
        raise AmendmentBlocked(
            gettext(
                "This is a business order, so the address cannot be moved to %(place)s without a "
                "new VAT number check for that destination, which amendments do not support yet."
            )
            % {"place": place}
        )
    rates = _rates(sub, snapshot, destination, place)
    current = next((r for r in rates if r.key == snapshot.shipping.key), None)
    if current is None:
        raise AmendmentBlocked(
            gettext(
                "The package's shipping method (%(method)s) does not deliver to %(place)s, so the "
                "address cannot be changed to it."
            )
            % {"method": snapshot.shipping.label or snapshot.shipping.key, "place": place}
        )
    return tax, _offer(current)


# --- the recompute ------------------------------------------------------------------------------


def _categories(snapshot: am.VersionSnapshot) -> dict[str, str]:
    """The fiscal category of each line, as snapshotted when the order was placed."""
    return {line.line_key: line.fiscal_category for line in snapshot.lines if line.fiscal_category}


def _recompute(
    sub: VendorOrder,
    version: VendorOrderVersion,
    changes: Sequence[am.AmendmentChange],
    *,
    at: dt.datetime,
) -> tuple[am.AmendmentPreview, list[am.AmendmentChange], dict[str, Any]]:
    """The preview of `changes` on `version`, with prices and destinations checked by the
    shipping and tax services; returns the preview, the resolved changes and the delivery
    address after them."""
    snapshot = am.VersionSnapshot.from_json(version.snapshot)
    address_before = versions.delivery_address(sub, version)
    address_after = dict(address_before)
    categories = _categories(snapshot)
    tax: tax_engine.TaxContext | None = None
    resolved: list[am.AmendmentChange] = []
    for change in changes:
        if isinstance(change, am.ChangeAddress):
            tax, offer = check_destination(
                sub,
                snapshot,
                change.address,
                address_before=address_before,
                categories=categories,
                at=at,
            )
            address_after = dict(change.address)
            resolved.append(replace(change, shipping=offer))
        elif isinstance(change, am.ChangeCarrier):
            destination = shipping_engine.Destination(
                str(address_after.get("country") or snapshot.destination_country),
                str(address_after.get("postcode") or ""),
                str(address_after.get("state") or ""),
            )
            place = _place(destination.country, destination.postcode)
            rates = _rates(sub, snapshot, destination, place)
            rate = next((r for r in rates if r.key == change.offer.key), None)
            if rate is None:
                raise AmendmentRefused(
                    gettext("The shipping method %(method)r is not offered for this package.")
                    % {"method": change.offer.key}
                )
            resolved.append(am.ChangeCarrier(_offer(rate)))  # the price is quoted, never typed
        else:
            resolved.append(change)
    if tax is None:
        try:
            tax = tax_services.build_tax_context(
                sub.vendor,
                destination_country=str(
                    address_before.get("country") or snapshot.destination_country
                ),
                destination_postcode=str(address_before.get("postcode") or "") or None,
                at=at,
            )
        except (tax_engine.BlockingTaxError, LookupError) as error:
            # LookupError: no vendor tax profile or rate in force (a configuration gap).
            raise AmendmentRefused(
                gettext("VAT cannot be decided for this package (%(why)s).") % {"why": error}
            ) from None
    tax_on = at.astimezone(business_tz()).date()
    ctx = am.RecomputeContext(
        tax_on=tax_on,
        tax=replace(tax, on=tax_on),  # the business-zone date, never the UTC one
        address_before=address_before,
        categories=categories,
    )
    try:
        preview = am.recompute_vendor_order(snapshot, resolved, ctx)
        requoted = _requote_lighter(sub, snapshot, preview, resolved, address_after)
        if requoted is not None:
            ctx = replace(ctx, requoted_shipping_net=requoted)
            preview = am.recompute_vendor_order(snapshot, resolved, ctx)
    except tax_engine.BlockingTaxError as error:
        raise AmendmentRefused(
            gettext("VAT cannot be decided for this change (%(why)s).") % {"why": error}
        ) from None
    return preview, resolved, address_after


def _requote_lighter(
    sub: VendorOrder,
    snapshot: am.VersionSnapshot,
    preview: am.AmendmentPreview,
    changes: Sequence[am.AmendmentChange],
    address: Mapping[str, Any],
) -> Decimal | None:
    """Owner decision 2026-11-16 (choice 4): after a removal or a lower quantity, quote the
    package's same method again for the lighter package (snapshot weights). Returns the new net,
    or None when nothing is re-quoted: no lighter package, a carrier or address change (they
    quote on their own), or a re-quote that fails or needs a quote (the price paid stays)."""
    if not am.lowers_weight(snapshot, changes) or any(
        isinstance(c, am.ChangeCarrier | am.ChangeAddress) for c in changes
    ):
        return None
    try:
        destination = shipping_engine.Destination(
            str(address.get("country") or snapshot.destination_country),
            str(address.get("postcode") or ""),
            str(address.get("state") or ""),
        )
        rates = _rates(sub, preview.after, destination, "the delivery address")
    except (AmendmentRefused, InvalidCountry):
        return None
    rate = next((r for r in rates if r.key == snapshot.shipping.key), None)
    return None if rate is None else rate.net


# --- snapshots for the audit trail (no address: personal data) ---------------------------------


def _redacted(text: str) -> dict[str, Any]:
    """A free text as the audit trail keeps it: its length and a SHA-256 digest, never the text."""
    return {
        "length": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest() if text else "",
    }


def _amendment_snapshot(amendment: OrderAmendment) -> dict[str, Any]:
    sub = amendment.vendor_order
    return {
        VENDOR_FIELD: str(sub.vendor.public_id),
        "order": sub.order.number,
        "state": amendment.state,
        "kind": amendment.kind,
        "reasons": list(amendment.reasons),
        "initiator": amendment.initiator,
        # Free text may hold personal data and the audit table is append-only (D-29): the entry
        # keeps a digest of each text; the text itself stays on the amendment, scrubbed with the
        # order. Channel and date of the customer's request are not personal data.
        "reason": _redacted(amendment.reason),
        "staff_note": _redacted(amendment.staff_note),
        "customer_request": {
            "channel": (amendment.customer_request or {}).get("channel", ""),
            "date": (amendment.customer_request or {}).get("date", ""),
            "text": _redacted(str((amendment.customer_request or {}).get("text") or "")),
        },
        "from_version": amendment.from_version.number,
        "from_digest": versions.digest(amendment.from_version.snapshot),
        "to_digest": (
            versions.digest(amendment.preview["after_snapshot"])
            if "after_snapshot" in amendment.preview
            else ""
        ),
        "changes": [
            am.change_to_json(am.change_from_json(c), with_address=False) for c in amendment.changes
        ],
        "money_delta": amendment.preview.get("money_delta"),
        "refund": amendment.preview.get("refund"),
        "commission_delta": amendment.preview.get("commission_delta"),
        "refund_ref": amendment.refund_ref,
        "revision": amendment.revision,
    }


# --- reading (for the adapters) -----------------------------------------------------------------


def open_amendment(vendor_order: VendorOrder) -> OrderAmendment | None:
    """The package's open amendment (at most one), with what the pages show."""
    return (
        OrderAmendment.objects.select_related("from_version", "actor", "vendor_order__vendor")
        .filter(vendor_order=vendor_order, state__in=OPEN_AMENDMENT_STATES)
        .first()
    )


def available_carriers(vendor_order: VendorOrder) -> list[am.ShippingOffer]:
    """The vendor's shipping methods with an exact price for the package at its current address
    (the choices of a carrier change); empty when none can be quoted there."""
    version = versions.current_version(vendor_order)
    snapshot = am.VersionSnapshot.from_json(version.snapshot)
    address = versions.delivery_address(vendor_order, version)
    try:
        destination = shipping_engine.Destination(
            str(address.get("country") or snapshot.destination_country),
            str(address.get("postcode") or ""),
            str(address.get("state") or ""),
        )
        rates = _rates(vendor_order, snapshot, destination, "the delivery address")
    except (AmendmentRefused, InvalidCountry):
        return []
    return [_offer(rate) for rate in rates]


@dataclass(frozen=True)
class AppliedVersion:
    """One applied version of a package for a timeline: v1 (the placed order, no amendment) or
    an applied amendment with its changes and the money it refunded."""

    number: int
    created_at: dt.datetime
    gross_total: Decimal
    changes: tuple[am.AmendmentChange, ...]
    before: am.VersionSnapshot | None
    refund: Decimal
    reason: str


def applied_versions(vendor_order: VendorOrder) -> list[AppliedVersion]:
    """The package's versions in order, oldest first. Internal notes (the staff note, the
    customer's words) are not part of it."""
    found: list[AppliedVersion] = []
    rows = (
        VendorOrderVersion.objects.filter(vendor_order=vendor_order)
        .select_related("amendment__from_version")
        .order_by("number")
    )
    for version in rows:
        amendment = version.amendment
        found.append(
            AppliedVersion(
                number=version.number,
                created_at=version.created_at,
                gross_total=version.gross_total,
                changes=tuple(am.change_from_json(c) for c in amendment.changes)
                if amendment is not None
                else (),
                before=am.VersionSnapshot.from_json(amendment.from_version.snapshot)
                if amendment is not None
                else None,
                refund=Decimal(str(amendment.preview.get("refund") or "0"))
                if amendment is not None
                else Decimal("0.00"),
                reason=amendment.reason if amendment is not None else "",
            )
        )
    return found


# --- drafting -----------------------------------------------------------------------------------


def _check_customer_request(
    changes: Sequence[am.AmendmentChange], customer_request: Mapping[str, Any] | None
) -> dict[str, Any]:
    asked = any(isinstance(c, am.ChangeAddress) and c.requested_by_customer for c in changes)
    if not asked:
        return dict(customer_request or {})
    request = dict(customer_request or {})
    missing = [key for key in CUSTOMER_REQUEST_KEYS if not str(request.get(key) or "").strip()]
    if missing:
        raise AmendmentRefused(
            gettext("An address change asked by the customer needs the request's %(fields)s.")
            % {"fields": ", ".join(missing)}
        )
    return {key: str(request[key]) for key in CUSTOMER_REQUEST_KEYS}


def _lock_amendable(sub: VendorOrder) -> VendorOrder:
    """Lock the Order, then the VendorOrder (only its own row, `of=("self",)`): the order of
    `transition_vendor_order` and `ledger.refunds._lock`, so an amendment, a refund and a
    fulfilment step never deadlock, and the Vendor row is never locked. CMP.5: a vendor whose open
    orders are frozen (termination under staff review) cannot amend them."""
    Order.objects.select_for_update().filter(pk=sub.order_id).first()
    locked = (
        VendorOrder.objects.select_for_update(of=("self",))
        .select_related("order", "vendor")
        .get(pk=sub.pk)
    )
    if orders_frozen(locked.vendor):
        raise OrdersFrozen(
            gettext(
                "The shop's open orders are frozen pending staff review, so they cannot be amended."
            )
        )
    if locked.order.state not in PAID_OR_LATER:
        raise AmendmentRefused(
            gettext(
                "This order is not paid yet: change it through the checkout or the quote instead."
            )
        )
    if locked.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        raise AmendmentRefused(
            gettext(
                "This package is %(state)s: an order can be amended only before shipping, while "
                "the package is pending or accepted."
            )
            % {"state": locked.state}
        )
    return locked


def withdrawn(sub: VendorOrder) -> bool:
    """Whether a consumer withdrawal is registered on the package, through the hooks in
    `settings.ORDER_WITHDRAWAL_HOOKS` (orders never imports compliance, which records them)."""
    return any(import_string(path)(sub) for path in settings.ORDER_WITHDRAWAL_HOOKS)


def _refuse_withdrawn(sub: VendorOrder) -> None:
    if withdrawn(sub):
        raise AmendmentRefused(
            gettext(
                "The customer has withdrawn from this package, so it can no longer be changed: "
                "the withdrawal and its refund run on the current version."
            )
        )


def draft_amendment(
    vendor_order: VendorOrder,
    changes: Sequence[am.AmendmentChange],
    *,
    actor: User,
    reason: str,
    ctx: AuditContext,
    staff_note: str = "",
    customer_request: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> tuple[OrderAmendment | None, ChangeSet]:
    """Draft an amendment of a paid package (ORD.20). Returns `(amendment, changeset)`, or
    `(None, changeset)` for a dry run, which writes nothing.

    Raises `Http404` when the actor cannot see the package, `PermissionDenied` for an actor who
    may not amend it, `AmendmentRefused` (or `AmendmentBlocked` for an unsupported destination)
    with a plain reason: no reason given, staff changing products or prices or without a note,
    a package not pending or accepted, an open amendment already there, a change that cannot be
    computed."""
    sub = _scoped(vendor_order, actor)
    initiator = initiator_of(actor, sub)
    reason = reason.strip()
    if not reason:
        raise AmendmentRefused(
            gettext("Give the reason for the change; the customer will read it.")
        )
    if initiator in am.STAFF_INITIATORS:
        if not staff_note.strip():
            raise AmendmentRefused(gettext("Platform staff must add a note to every amendment."))
        error = am.staff_change_error(initiator, changes)
        if error:
            raise AmendmentRefused(error)
    request = _check_customer_request(changes, customer_request)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    amendment: OrderAmendment | None = None
    try:
        with transaction.atomic():
            locked = _lock_amendable(sub)
            _refuse_withdrawn(locked)
            open_one = OrderAmendment.objects.filter(
                vendor_order=locked, state__in=OPEN_AMENDMENT_STATES
            ).first()
            if open_one is not None:
                raise AmendmentRefused(
                    gettext(
                        "This package already has an open amendment (%(ref)s, %(state)s): finish, "
                        "withdraw or discard it before drafting another."
                    )
                    % {"ref": open_one.public_id, "state": open_one.state}
                )
            version = versions.current_version(locked)
            preview, resolved, _ = _recompute(locked, version, changes, at=stamp)
            error = am.staff_carrier_error(initiator, preview, resolved)
            if error:
                raise AmendmentRefused(error)
            result = am.classify_amendment(
                preview,
                resolved,
                initiator,
                comparable_delay_days=amendment_settings(stamp).comparable_delay_days,
            )
            amendment = OrderAmendment(
                vendor_order=locked,
                from_version=version,
                changes=[am.change_to_json(c) for c in resolved],
                kind=result.kind,
                reasons=list(result.reasons),
                initiator=initiator,
                actor=actor,
                reason=reason,
                staff_note=staff_note.strip(),
                customer_request=request,
                preview=preview.to_json(),
            )
            try:
                with transaction.atomic():
                    amendment.save()
            except IntegrityError:
                raise AmendmentRefused(
                    gettext(
                        "This package already has an open amendment: finish, withdraw or discard "
                        "it before drafting another."
                    )
                ) from None
            changeset.create(
                "orders.orderamendment", str(amendment.public_id), _amendment_snapshot(amendment)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_drafted")
    except _DryRunRollback:
        return None, changeset
    return amendment, changeset


def discard_amendment(
    amendment: OrderAmendment, *, actor: User, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Discard a draft that was never sent (`draft` -> `discarded`)."""
    sub = _scoped(amendment.vendor_order, actor)
    initiator_of(actor, sub)
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = OrderAmendment.objects.select_for_update().get(pk=amendment.pk)
            if locked.state != AmendmentState.DRAFT:
                raise AmendmentRefused(
                    gettext("Only a draft can be discarded; this one is %(state)s.")
                    % {"state": locked.state}
                )
            before = _amendment_snapshot(locked)
            locked.state = AmendmentState.DISCARDED
            locked.save(update_fields=["state", "updated_at"])
            changeset.update(
                "orders.orderamendment", str(locked.public_id), before, _amendment_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_discarded")
    except _DryRunRollback:
        pass
    amendment.refresh_from_db()
    return changeset


# --- applying -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefundOutcome:
    """What the refund hook (the ledger, PAY.4) reports for an amendment or a cancel of the rest:
    the refund event, its amount and fee, and the ChangeSet entries it wrote or would write."""

    refund_id: str
    provider_ref: str
    amount: Decimal
    refund_fee: Decimal
    fee_exclusion_reason: str
    due_by: dt.datetime | None
    changes: list[Change] = field(default_factory=list)
    platform_error: bool = False

    @property
    def fee_charged(self) -> bool:
        """Whether the vendor pays the D-05 refund fee for this event, as the ledger decided it:
        no exclusion reason and no platform-error flag (L-10). The notices read it here, never
        from the cause, so the email and the ledger cannot diverge."""
        return not self.fee_exclusion_reason and not self.platform_error


class PasskeyRequired(PermissionDenied):
    """A critical amendment (CORE.25: an address change, any refund) needs a fresh passkey bound
    to this amendment's digest and shop; an open passkey window does not cover it."""

    def __init__(self, message: str, *, action: str, digest: str) -> None:
        super().__init__(message)
        self.action = action
        self.digest = digest


@dataclass(frozen=True)
class PasskeyAsk:
    """The one passkey a critical amendment needs: the CORE.25 action and the digest the grant
    must be bound to (the adapter's prompt mints the grant for exactly these)."""

    action: str
    digest: str


def _is_address(change: Mapping[str, Any]) -> bool:
    return str(change.get("kind")) == am.ChangeAddress.kind


def amendment_passkey(amendment: OrderAmendment) -> PasskeyAsk | None:
    """The passkey a critical amendment asks (spec 07, Roles and the passkey), or None for an
    everyday one. One prompt per amendment, even when it is critical on several counts: an
    address change asks `order_amend_address`; otherwise a refund spends the PAY.7
    `vendor_refund` grant (vendor members) or `order_amend_cancel` (platform staff).

    The digest binds the vendor order, the shop, the from-version, the draft's revision, its
    changes and its amounts: any edit of the draft voids a grant made for it."""
    address = any(_is_address(c) for c in amendment.changes)
    refund = Decimal(str(amendment.preview.get("refund") or "0")) > 0
    if not (address or refund):
        return None
    if address:
        action = am.ACTION_ADDRESS
    elif amendment.initiator in am.STAFF_INITIATORS:
        action = am.ACTION_STAFF_REFUND
    else:
        action = am.ACTION_VENDOR_REFUND
    sub = amendment.vendor_order
    material = json.dumps(
        {
            "action": action,
            "amendment": str(amendment.public_id),
            "vendor_order": str(sub.public_id),
            "shop": levels.shop_key(sub.vendor),
            "from_version": amendment.from_version.number,
            "from_digest": versions.digest(amendment.from_version.snapshot),
            "revision": amendment.revision,
            "changes": amendment.changes,
            "money_delta": amendment.preview.get("money_delta"),
            "refund": amendment.preview.get("refund"),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return PasskeyAsk(action, hashlib.sha256(material.encode()).hexdigest())


def _spend_passkey(
    amendment: OrderAmendment, actor: User, passkey_session: str, ctx: AuditContext
) -> None:
    """Spend the fresh passkey grant of a critical amendment, or refuse. Never the passkey
    window: a critical action asks a passkey each time (CORE.25)."""
    ask = amendment_passkey(amendment)
    if ask is None:
        return
    _spend(
        ask,
        actor,
        passkey_session,
        ctx,
        shop=amendment.vendor_order.vendor,
        about={"amendment": str(amendment.public_id)},
        refusal=(
            gettext(
                "This change moves the delivery address, so it needs a fresh passkey "
                "confirmation for this amendment (an open passkey window does not cover it; a "
                "confirmation given before the draft was edited no longer counts)."
            )
            if ask.action == am.ACTION_ADDRESS
            else gettext(
                "This change refunds money to the customer, so it needs a fresh passkey "
                "confirmation for this amendment (an open passkey window does not cover it; a "
                "confirmation given before the draft was edited no longer counts)."
            )
        ),
    )


def _spend(
    ask: PasskeyAsk,
    actor: User,
    passkey_session: str,
    ctx: AuditContext,
    *,
    shop: object,
    about: Mapping[str, str],
    refusal: str,
) -> None:
    """Spend the single-use grant of `ask` for `actor` in `passkey_session`, audited, or raise
    `PasskeyRequired` with `refusal`."""
    if not passkey_session or not levels.spend_grant(
        passkey_session, actor, ask.action, ask.digest
    ):
        raise PasskeyRequired(refusal, action=ask.action, digest=ask.digest)
    record(
        ctx,
        levels.SINGLE_USED,
        USER_MODEL,
        str(actor.public_id),
        None,
        {"kind": "single", "action": ask.action, "shop": levels.shop_key(shop), **about},
    )


def _hook(name: str) -> Callable[..., RefundOutcome]:
    hook: Callable[..., RefundOutcome] = import_string(getattr(settings, name))
    return hook


def _partly_unavailable(changes: Sequence[am.AmendmentChange], after: am.VersionSnapshot) -> bool:
    """A line removed or reduced as unavailable, with something left in the package (spec 07,
    minor change 2: the customer is then offered to cancel the rest)."""
    return bool(after.lines) and any(
        isinstance(c, am.RemoveLine | am.ChangeQuantity) and c.unavailable for c in changes
    )


def _restock(
    sub: VendorOrder,
    before: am.VersionSnapshot,
    after: am.VersionSnapshot,
    changes: Sequence[am.AmendmentChange],
) -> list[Change]:
    """Put back in stock the units of a line removed or reduced for a reason other than
    unavailability (spec 07, Stock); an unavailable line restores nothing. Only lines of the
    placed order (v1) carry the product or variant whose stock moved at payment."""
    keys = {
        c.line_key
        for c in changes
        if isinstance(c, am.RemoveLine | am.ChangeQuantity) and not c.unavailable
    }
    left = {line.line_key: line.quantity for line in after.lines}
    changes_out: list[Change] = []
    for line in before.lines:
        units = line.quantity - left.get(line.line_key, 0)
        if line.line_key not in keys or units <= 0:
            continue
        order_line = OrderLine.objects.filter(vendor_order=sub, public_id=line.line_key).first()
        if order_line is None:
            continue
        if order_line.product_id is None:
            continue
        # The variant the line was sold as (its id, kept on the line since ORD.20 part 2); a
        # line placed earlier falls back to the variant with the line's SKU.
        found = Variant.objects.select_for_update()
        variant = (
            found.filter(pk=order_line.variant_id).first()
            if order_line.variant_id is not None
            else found.filter(product_id=order_line.product_id, sku=order_line.sku).first()
        )
        if order_line.variant_id is not None and variant is None:
            continue  # the variant was deleted: there is no stock to put back
        row: Product | Variant
        if variant is not None:
            row = variant
            model = "catalog.variant"
        else:
            row = Product.objects.select_for_update(of=("self",)).get(pk=order_line.product_id)
            model = "catalog.product"
        stock_before = row.stock_qty
        row.stock_qty = stock_before + units
        row.save(update_fields=["stock_qty", "updated_at"])
        change = Change(
            "update",
            model,
            str(row.public_id),
            {"stock_qty": stock_before},
            {"stock_qty": row.stock_qty, "restocked_from": str(sub.public_id)},
        )
        changes_out.append(change)
    return changes_out


def apply_amendment(
    amendment: OrderAmendment,
    *,
    actor: User,
    ctx: AuditContext,
    passkey_session: str = "",
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """Apply a minor draft: write the next version of the package, make it current and refund
    the decrease, all in one transaction.

    The vendor order is locked first; when its current version is no longer the draft's
    `from_version`, the amendment becomes `superseded` and nothing else moves. A substantial
    amendment needs the customer's acceptance (ORD.21). A critical amendment (an address change,
    any refund) spends the fresh passkey grant of `passkey_session` bound to the amendment's
    digest (`amendment_passkey`), never the passkey window. A decrease is a PAY.4 refund event
    submitted to the provider in this step (the ledger, through `ORDER_AMENDMENT_REFUND_HOOK`); a
    provider failure rolls everything back and the draft stays a draft. After a partial
    unavailability the package is held from shipping for `amendment_cancel_hold_hours` and the
    customer may cancel the rest. A dry run needs no passkey, calls no provider and writes
    nothing; it returns the ChangeSet with the refund estimate."""
    sub = _scoped(amendment.vendor_order, actor)
    initiator = initiator_of(actor, sub)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    refund_changes: list[Change] = []
    applied: VendorOrderVersion | None = None
    try:
        with transaction.atomic():
            locked_sub = _lock_amendable(sub)
            locked = (
                OrderAmendment.objects.select_for_update()
                .select_related("from_version", "vendor_order__vendor", "vendor_order__order")
                .get(pk=amendment.pk)
            )
            if locked.state != AmendmentState.DRAFT:
                raise AmendmentRefused(
                    gettext("Only a draft can be applied; this one is %(state)s.")
                    % {"state": locked.state}
                )
            _same_side(initiator, locked.initiator)
            before = _amendment_snapshot(locked)
            if locked_sub.current_version_id != locked.from_version_id:
                # The version moved under the draft: it is superseded and nothing else moves.
                locked.state = AmendmentState.SUPERSEDED
                locked.save(update_fields=["state", "updated_at"])
                action = "orders.amendment_superseded"
            else:
                _refuse_withdrawn(locked_sub)
                if not dry_run:
                    _spend_passkey(locked, actor, passkey_session, ctx)
                version, refund_changes, stock_changes = _write_next_version(
                    locked_sub, locked, at=stamp, ctx=ctx, dry_run=dry_run
                )
                changeset.changes.extend(stock_changes)
                changeset.create(
                    "orders.vendororderversion",
                    str(version.public_id),
                    versions.audit_snapshot(version),
                )
                action = "orders.amendment_applied"
                applied = version
            changeset.update(
                "orders.orderamendment", str(locked.public_id), before, _amendment_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action=action)
            if applied is not None:
                amendment_notices.amendment_applied(locked, applied)
    except _DryRunRollback:
        pass
    amendment.refresh_from_db()
    if not dry_run and amendment.refund_ref:
        # The version and the pending refund are committed: submit it to the provider now. A
        # provider failure leaves the refund pending (retried by the sweep, alerted), never a
        # version without its refund or a refund without its version.
        _submit(amendment.refund_ref, ctx)
    return ChangeSet([*changeset.changes, *refund_changes])


def _submit(refund_id: str, ctx: AuditContext) -> None:
    """Submit the recorded refund once the change that recorded it is committed: at once
    outside a transaction, else on the real commit (`on_commit`). The change is already
    committed, so a failure here never surfaces to the caller: it is logged, and the ledger's
    sweep retries the refund (and alerts)."""

    def go() -> None:
        try:
            submit: Callable[..., bool] = import_string(settings.ORDER_REFUND_SUBMIT_HOOK)
            submit(refund_id, ctx=ctx)
        except Exception:
            logger.exception("refund %s left for the sweep", refund_id)

    transaction.on_commit(go)


def _write_next_version(
    sub: VendorOrder,
    amendment: OrderAmendment,
    *,
    at: dt.datetime,
    ctx: AuditContext,
    dry_run: bool,
) -> tuple[VendorOrderVersion, list[Change], list[Change]]:
    """Recompute the minor draft on the apply date, write the next version, refund the decrease
    and open the cancel-the-rest offer after a partial unavailability."""
    if amendment.kind != am.MINOR:
        raise AmendmentRefused(
            gettext("This is a substantial change: it needs the customer's acceptance first.")
        )
    changes = [am.change_from_json(c) for c in amendment.changes]
    preview, resolved, address = _recompute(sub, amendment.from_version, changes, at=at)
    # The price is quoted again at apply: a staff draft must still meet the staff limits.
    error = am.staff_change_error(amendment.initiator, resolved) or am.staff_carrier_error(
        amendment.initiator, preview, resolved
    )
    if error:
        raise AmendmentRefused(error)
    rules = amendment_settings(at)
    result = am.classify_amendment(
        preview,
        resolved,
        amendment.initiator,
        comparable_delay_days=rules.comparable_delay_days,
    )
    if result.kind != am.MINOR:
        raise AmendmentRefused(
            gettext(
                "Recomputed today, this is a substantial change (%(reasons)s): it needs the "
                "customer's acceptance first."
            )
            % {"reasons": "; ".join(result.reasons)}
        )
    drafted = Decimal(str(amendment.preview.get("money_delta") or "0"))
    if preview.money_delta != drafted:
        raise AmendmentRefused(
            gettext(
                "Recomputed today, this change moves %(now)s instead of the %(drafted)s shown in "
                "the draft: edit the draft so it shows today's amounts, then confirm it again."
            )
            % {"now": preview.money_delta, "drafted": drafted}
        )
    if (
        _partly_unavailable(resolved, preview.after)
        and rules.cancel_hold_hours < A2_MIN_WINDOW_HOURS
    ):
        raise AmendmentRefused(
            gettext(
                "The customer must have at least %(hours)s hours to read the notice and cancel the "
                "rest before the seller may ship, but the shipping hold is set to %(set)s hours: "
                "raise the hold in the amendment settings."
            )
            % {"hours": A2_MIN_WINDOW_HOURS, "set": rules.cancel_hold_hours}
        )
    version = versions.create_next_version(
        sub,
        preview.after,
        number=amendment.from_version.number + 1,
        amendment=amendment,
        shipping_address=address,
    )
    stock_changes = _restock(sub, preview.before, preview.after, resolved)
    if _partly_unavailable(resolved, preview.after):
        VendorOrder.objects.filter(pk=sub.pk).update(
            cancel_rest_offered_at=at,
            ship_hold_until=at + dt.timedelta(hours=rules.cancel_hold_hours),
        )
    refund_changes: list[Change] = []
    if preview.refund > 0:
        outcome = _hook("ORDER_AMENDMENT_REFUND_HOOK")(
            amendment,
            amount=preview.refund,
            # Owner decision on #564: an amendment gives back the commission of the recompute
            # (previous version minus the new one under the frozen rule), not the PAY.4 pro rata.
            commission_back=max(
                Decimal("0.00"), preview.commission_before - preview.commission_after
            ),
            ctx=ctx,
            now=at,
            dry_run=dry_run,
        )
        amendment.refund_ref = outcome.refund_id
        refund_changes = outcome.changes
    amendment.state = AmendmentState.APPLIED
    amendment.preview = preview.to_json()
    amendment.changes = [am.change_to_json(c) for c in resolved]
    amendment.save(update_fields=["state", "preview", "changes", "refund_ref", "updated_at"])
    return version, refund_changes, stock_changes


# --- editing a draft ----------------------------------------------------------------------------


def revise_amendment(
    amendment: OrderAmendment,
    changes: Sequence[am.AmendmentChange],
    *,
    actor: User,
    ctx: AuditContext,
    reason: str | None = None,
    staff_note: str | None = None,
    customer_request: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """Edit a draft: recompute it with `changes` on its from-version and bump its revision, which
    voids any passkey grant made for the earlier draft (CORE.25). Same checks as a new draft; a
    draft whose version moved is superseded instead."""
    sub = _scoped(amendment.vendor_order, actor)
    initiator = initiator_of(actor, sub)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    superseded = False
    try:
        with transaction.atomic():
            locked_sub = _lock_amendable(sub)
            _refuse_withdrawn(locked_sub)
            locked = (
                OrderAmendment.objects.select_for_update()
                .select_related("from_version", "vendor_order__vendor", "vendor_order__order")
                .get(pk=amendment.pk)
            )
            if locked.state != AmendmentState.DRAFT:
                raise AmendmentRefused(
                    gettext("Only a draft can be edited; this one is %(state)s.")
                    % {"state": locked.state}
                )
            _same_side(initiator, locked.initiator)
            new_reason = (reason if reason is not None else locked.reason).strip()
            new_note = (staff_note if staff_note is not None else locked.staff_note).strip()
            if not new_reason:
                raise AmendmentRefused(
                    gettext("Give the reason for the change; the customer will read it.")
                )
            if initiator in am.STAFF_INITIATORS:
                if not new_note:
                    raise AmendmentRefused(
                        gettext("Platform staff must add a note to every amendment.")
                    )
                error = am.staff_change_error(initiator, changes)
                if error:
                    raise AmendmentRefused(error)
            request = _check_customer_request(
                changes,
                customer_request if customer_request is not None else locked.customer_request,
            )
            before = _amendment_snapshot(locked)
            if locked_sub.current_version_id != locked.from_version_id:
                locked.state = AmendmentState.SUPERSEDED
                locked.save(update_fields=["state", "updated_at"])
                changeset.update(
                    "orders.orderamendment",
                    str(locked.public_id),
                    before,
                    _amendment_snapshot(locked),
                )
                if dry_run:
                    raise _DryRunRollback
                audit_changes(changeset, ctx=ctx, action="orders.amendment_superseded")
                superseded = True
            else:
                _revise_locked(locked, locked_sub, changes, initiator, stamp, request)
                locked.reason = new_reason
                locked.staff_note = new_note
                locked.save()
                changeset.update(
                    "orders.orderamendment",
                    str(locked.public_id),
                    before,
                    _amendment_snapshot(locked),
                )
                if dry_run:
                    raise _DryRunRollback
                audit_changes(changeset, ctx=ctx, action="orders.amendment_edited")
    except _DryRunRollback:
        pass
    amendment.refresh_from_db()
    if superseded:
        raise AmendmentRefused(
            gettext(
                "The package changed since this draft was made, so the draft was closed as "
                "superseded: start a new one."
            )
        )
    return changeset


def _revise_locked(
    locked: OrderAmendment,
    sub: VendorOrder,
    changes: Sequence[am.AmendmentChange],
    initiator: str,
    stamp: dt.datetime,
    request: dict[str, Any],
) -> None:
    """Recompute a locked draft with `changes` and bump its revision (not saved)."""
    preview, resolved, _ = _recompute(sub, locked.from_version, changes, at=stamp)
    error = am.staff_carrier_error(initiator, preview, resolved)
    if error:
        raise AmendmentRefused(error)
    result = am.classify_amendment(
        preview,
        resolved,
        initiator,
        comparable_delay_days=amendment_settings(stamp).comparable_delay_days,
    )
    locked.changes = [am.change_to_json(c) for c in resolved]
    locked.kind = result.kind
    locked.reasons = list(result.reasons)
    locked.customer_request = request
    locked.preview = preview.to_json()
    locked.revision += 1


# --- closing an open amendment from elsewhere (a refund, a withdrawal, idleness) ---------------


SYSTEM_CTX = AuditContext(actor_label="system:orders.amendments", source=AuditLog.Source.SYSTEM)


def _close_open(
    sub: VendorOrder, state: str, *, cause: str, ctx: AuditContext
) -> OrderAmendment | None:
    """Close the package's open amendment as `state`, audited with `cause`. The caller holds the
    vendor order's lock (a refund, a withdrawal) or the amendment's own."""
    with transaction.atomic():
        found = (
            OrderAmendment.objects.select_for_update()
            .select_related("from_version", "vendor_order__vendor", "vendor_order__order")
            .filter(vendor_order_id=sub.pk, state__in=OPEN_AMENDMENT_STATES)
            .first()
        )
        if found is None:
            return None
        before = _amendment_snapshot(found)
        found.state = state
        found.save(update_fields=["state", "updated_at"])
        changeset = ChangeSet()
        changeset.update(
            "orders.orderamendment",
            str(found.public_id),
            before,
            {**_amendment_snapshot(found), "closed_by": cause},
        )
        audit_changes(changeset, ctx=ctx, action=f"orders.amendment_{state}")
    return found


def supersede_open_amendment(
    vendor_order: VendorOrder, *, cause: str, ctx: AuditContext
) -> OrderAmendment | None:
    """A refund or a return recorded on the package while an amendment is open supersedes it
    (spec 07, Concurrency): the amendment was computed without it. Called by the ledger."""
    return _close_open(vendor_order, AmendmentState.SUPERSEDED, cause=cause, ctx=ctx)


def withdraw_open_amendment(
    vendor_order: VendorOrder, *, ctx: AuditContext
) -> OrderAmendment | None:
    """A consumer's withdrawal registered while an amendment is open closes it as `withdrawn`
    (spec 07, Withdrawal in the meantime); the withdrawal runs on the last applied version.
    Called by the compliance app when it records the withdrawal."""
    return _close_open(vendor_order, AmendmentState.WITHDRAWN, cause="withdrawal", ctx=ctx)


def discard_idle_drafts(*, now: dt.datetime | None = None) -> int:
    """Discard every draft idle for `DRAFT_IDLE_MINUTES` (spec 07, Concurrency), so a forgotten
    draft never blocks the package. Returns how many were discarded. Run by the periodic task
    `orders.discard_idle_amendment_drafts`."""
    moment = now or timezone.now()
    limit = moment - dt.timedelta(minutes=am.DRAFT_IDLE_MINUTES)
    ids = list(
        OrderAmendment.objects.filter(state=AmendmentState.DRAFT, updated_at__lt=limit).values_list(
            "pk", flat=True
        )
    )
    done = 0
    for pk in ids:
        with transaction.atomic():
            found = (
                OrderAmendment.objects.select_for_update(skip_locked=True)
                .select_related("from_version", "vendor_order__vendor", "vendor_order__order")
                .filter(pk=pk, state=AmendmentState.DRAFT, updated_at__lt=limit)
                .first()
            )
            if found is None:
                continue
            before = _amendment_snapshot(found)
            found.state = AmendmentState.DISCARDED
            found.save(update_fields=["state", "updated_at"])
            changeset = ChangeSet()
            changeset.update(
                "orders.orderamendment",
                str(found.public_id),
                before,
                {**_amendment_snapshot(found), "closed_by": "idle"},
            )
            audit_changes(changeset, ctx=SYSTEM_CTX, action="orders.amendment_discarded")
            done += 1
    return done


# --- cancelling the whole package (the shop or platform staff; ORD.22 for staff) ---------------

# Why a package is cancelled in full (owner decision 2026-11-16): it decides who pays the refund
# fee and whether the units go back to stock. The vendor's fault (goods unavailable, the vendor
# cannot fulfil) keeps the fee (D-05) and restocks nothing; a customer's request or a platform
# error drops the fee and restocks the units.
CANCEL_VENDOR_FAULT = "vendor_fault"
CANCEL_CUSTOMER_REQUEST = "customer_request"
CANCEL_PLATFORM_ERROR = "platform_error"
CANCEL_CAUSES = (CANCEL_VENDOR_FAULT, CANCEL_CUSTOMER_REQUEST, CANCEL_PLATFORM_ERROR)
SHOP_CANCEL_CAUSES = (CANCEL_VENDOR_FAULT, CANCEL_CUSTOMER_REQUEST)

# The kind of a package cancellation, written in its audit entry (`kind`): the customer cancelled
# the rest after an unavailable item, or the shop or staff cancelled the package in full.
KIND_REST_CANCELLED = "rest_cancelled"
KIND_PACKAGE_CANCELLED = "package_cancelled"


def cancel_package_passkey(
    vendor_order: VendorOrder,
    initiator: str,
    cause: str = CANCEL_VENDOR_FAULT,
    *,
    amount: Decimal,
) -> PasskeyAsk:
    """The passkey a full-refund cancellation asks (spec 07, Roles and the passkey): the PAY.7
    `vendor_refund` grant for the shop, `order_amend_cancel` for platform staff. The digest binds
    the package, its shop, its current version, the cause (it decides the refund fee) and the
    amount left to refund, so a grant never cancels another package, one that changed after the
    prompt, or for another cause or amount."""
    action = am.ACTION_STAFF_REFUND if initiator in am.STAFF_INITIATORS else am.ACTION_VENDOR_REFUND
    version = versions.current_version(vendor_order)
    material = json.dumps(
        {
            "action": action,
            "cancel_package": str(vendor_order.public_id),
            "cause": cause,
            "amount": str(amount),
            "shop": levels.shop_key(vendor_order.vendor),
            "version": version.number,
            "version_digest": versions.digest(version.snapshot),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PasskeyAsk(action, hashlib.sha256(material.encode()).hexdigest())


def _check_cancel_cause(
    initiator: str, cause: str, customer_request: Mapping[str, Any] | None
) -> dict[str, str]:
    """The cause a side may choose, and the customer's request the shop must record for it."""
    if cause not in CANCEL_CAUSES:
        raise AmendmentRefused(
            gettext(
                "Choose why the package is cancelled: the shop's fault, the customer's request "
                "or a platform error."
            )
        )
    if initiator in am.STAFF_INITIATORS:
        return {}
    if cause not in SHOP_CANCEL_CAUSES:
        raise AmendmentRefused(
            gettext(
                "A shop cancels either for its own fault or because the customer asked; a "
                "platform error is for platform staff to record."
            )
        )
    if cause != CANCEL_CUSTOMER_REQUEST:
        return {}
    request = dict(customer_request or {})
    missing = [key for key in CUSTOMER_REQUEST_KEYS if not str(request.get(key) or "").strip()]
    if missing:
        raise AmendmentRefused(
            gettext("A cancellation the customer asked for needs the request's %(fields)s.")
            % {"fields": ", ".join(missing)}
        )
    return {key: str(request[key]) for key in CUSTOMER_REQUEST_KEYS}


def _restock_package(sub: VendorOrder, cause: str) -> list[Change]:
    """Put the current version's units back in stock unless the vendor's fault (goods
    unavailable) caused the cancellation (lane-a decision on #577)."""
    if cause == CANCEL_VENDOR_FAULT:
        return []
    before = am.VersionSnapshot.from_json(versions.current_version(sub).snapshot)
    gone = [am.RemoveLine(line.line_key, unavailable=False) for line in before.lines]
    return _restock(sub, before, replace(before, lines=()), gone)


def cancel_package(
    vendor_order: VendorOrder,
    *,
    actor: User,
    reason: str,
    ctx: AuditContext,
    cause: str = CANCEL_VENDOR_FAULT,
    staff_note: str = "",
    customer_request: Mapping[str, Any] | None = None,
    passkey_session: str = "",
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """Cancel a paid package before shipping with a full refund of what is left (spec 07, Roles:
    the shop's owner or a member with ORDERS; the platform owner or Support with a note, ORD.22).
    A PAY.4 refund event due within 14 days (a platform-initiated one for staff); an open
    amendment is superseded by the refund. Critical (CORE.25): it spends a fresh passkey grant
    (`cancel_package_passkey`). A dry run needs no passkey and writes nothing.

    `cause` (owner decision 2026-11-16) decides the refund fee and the stock: `vendor_fault`
    charges the fee to the vendor and restocks nothing; `customer_request` and `platform_error`
    charge no fee (the ledger records the exclusion) and restock the units. The shop chooses its
    own fault or the customer's request, which it records (channel, date, words); only staff
    record a platform error."""
    sub = _scoped(vendor_order, actor)
    initiator = initiator_of(actor, sub)
    reason = reason.strip()
    if not reason:
        raise AmendmentRefused(
            gettext("Give the reason for the cancellation; the customer will read it.")
        )
    if initiator in am.STAFF_INITIATORS and not staff_note.strip():
        raise AmendmentRefused(gettext("Platform staff must add a note to every amendment."))
    request = _check_cancel_cause(initiator, cause, customer_request)
    stamp = now or timezone.now()
    hook = _hook("ORDER_CANCEL_PACKAGE_REFUND_HOOK")
    by_staff = initiator in am.STAFF_INITIATORS
    changeset = ChangeSet()
    refund_changes: list[Change] = []
    refund_id = ""
    try:
        with transaction.atomic():
            locked = _lock_amendable(sub)
            _refuse_withdrawn(locked)
            if not dry_run:
                estimate = hook(
                    locked,
                    reason=reason,
                    cancel_cause=cause,
                    note=staff_note.strip(),
                    by_staff=by_staff,
                    ctx=ctx,
                    now=stamp,
                    dry_run=True,
                )
                _spend(
                    cancel_package_passkey(locked, initiator, cause, amount=estimate.amount),
                    actor,
                    passkey_session,
                    ctx,
                    shop=locked.vendor,
                    about={"vendor_order": str(locked.public_id)},
                    refusal=gettext(
                        "Cancelling the package refunds the customer in full, so it needs a "
                        "fresh passkey confirmation (an open passkey window does not cover it)."
                    ),
                )
            before = {**_offer_snapshot(locked), "initiator": initiator}
            lines = amendment_notices.current_lines(locked)
            VendorOrder.objects.filter(pk=locked.pk).update(
                cancel_rest_offered_at=None, ship_hold_until=None
            )
            stock_changes = _restock_package(locked, cause)
            outcome = hook(
                locked,
                reason=reason,
                cancel_cause=cause,
                note=staff_note.strip(),
                by_staff=by_staff,
                ctx=ctx,
                now=stamp,
                dry_run=dry_run,
            )
            refund_changes = outcome.changes
            apply_refund_to_states(locked, fully_refunded=True, ctx=ctx)
            locked.refresh_from_db()
            if locked.state not in {"cancelled", "refunded"}:
                raise AmendmentRefused(
                    gettext(
                        "The package could not be cancelled (it is %(state)s); nothing changed."
                    )
                    % {"state": locked.state}
                )
            refund_id = outcome.refund_id
            changeset.changes.extend(stock_changes)
            changeset.update(
                "orders.vendororder",
                str(locked.public_id),
                before,
                {
                    **_offer_snapshot(locked),
                    "initiator": initiator,
                    "reason": _redacted(reason),
                    "staff_note": _redacted(staff_note.strip()),
                    "customer_request": {
                        "channel": request.get("channel", ""),
                        "date": request.get("date", ""),
                        "text": _redacted(request.get("text", "")),
                    },
                    "refund": outcome.refund_id,
                    "refunded": outcome.amount,
                    "kind": KIND_PACKAGE_CANCELLED,
                    "cause": "cancel_package",
                    "cancel_cause": cause,
                    "refund_fee": outcome.refund_fee,
                    "fee_exclusion_reason": outcome.fee_exclusion_reason,
                    "restocked": bool(stock_changes),
                },
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_package_cancelled")
            amendment_notices.package_cancelled(
                locked,
                by=amendment_notices.BY_STAFF if by_staff else amendment_notices.BY_SHOP,
                cause=cause,
                reason=reason,
                refunded=outcome.amount,
                refund_fee=outcome.fee_charged,
                refund_ref=outcome.refund_id,
                lines=lines,
            )
    except _DryRunRollback:
        refund_id = ""
    vendor_order.refresh_from_db()
    if refund_id:
        _submit(refund_id, ctx)
    return ChangeSet([*changeset.changes, *refund_changes])


def cancellation_ask(
    vendor_order: VendorOrder, actor: User, cause: str, *, ctx: AuditContext
) -> PasskeyAsk:
    """The passkey ask of a cancellation as the service would compute it now (for a prompt or a
    test): the dry-run refund gives the amount left."""
    sub = _scoped(vendor_order, actor)
    initiator = initiator_of(actor, sub)
    estimate = _hook("ORDER_CANCEL_PACKAGE_REFUND_HOOK")(
        sub,
        reason="estimate",
        cancel_cause=cause,
        note="estimate",
        by_staff=initiator in am.STAFF_INITIATORS,
        ctx=ctx,
        now=timezone.now(),
        dry_run=True,
    )
    return cancel_package_passkey(sub, initiator, cause, amount=estimate.amount)


def cancellation_kind(after: Mapping[str, Any]) -> str:
    """The kind of a cancellation's audit entry. Entries written before ORD.20 part 3b have no
    `kind`: a full cancellation by the shop or staff carried `cause = "cancel_package"`, a cancel
    of the rest by the customer carried no `cause` at all."""
    kind = after.get("kind")
    if kind in (KIND_REST_CANCELLED, KIND_PACKAGE_CANCELLED):
        return str(kind)
    return KIND_PACKAGE_CANCELLED if after.get("cause") == "cancel_package" else KIND_REST_CANCELLED


@dataclass(frozen=True)
class PackageCancellation:
    """A package cancelled with a refund (by the customer after an unavailable item, or in full by
    the shop or staff), for the customer's timeline: when, how much, and whether the customer
    asked."""

    at: dt.datetime
    refunded: Decimal
    by_customer: bool
    kind: str = KIND_PACKAGE_CANCELLED


def package_cancellations(vendor_order: VendorOrder) -> list[PackageCancellation]:
    """The package's cancellations, from their audit entries (no internal note in them)."""
    rows = AuditLog.objects.filter(
        action="orders.amendment_package_cancelled",
        target_model="orders.vendororder",
        target_key=str(vendor_order.public_id),
    ).order_by("id")
    found = []
    for entry in rows:
        after = entry.after or {}
        kind = cancellation_kind(after)
        found.append(
            PackageCancellation(
                at=entry.created_at,
                refunded=Decimal(str(after.get("refunded") or "0")),
                by_customer=kind == KIND_REST_CANCELLED,
                kind=kind,
            )
        )
    return found


# --- the cancel-the-rest offer ------------------------------------------------------------------


def _customer_may_answer(order: Order, actor: User | None, verified_guest: bool) -> None:
    if verified_guest:
        return  # the adapter checked the order's signed link (a guest order)
    if actor is None or order.customer_id is None or actor.pk != order.customer_id:
        raise PermissionDenied(
            gettext("Only the customer who placed the order may answer this offer.")
        )


def _offer_snapshot(sub: VendorOrder) -> dict[str, Any]:
    return {
        VENDOR_FIELD: str(sub.vendor.public_id),
        "order": sub.order.number,
        "state": sub.state,
        "cancel_rest_offered_at": sub.cancel_rest_offered_at,
        "ship_hold_until": sub.ship_hold_until,
    }


def _lock_offer(vendor_order: VendorOrder) -> VendorOrder:
    Order.objects.select_for_update().filter(pk=vendor_order.order_id).first()
    locked = (
        VendorOrder.objects.select_for_update(of=("self",))
        .select_related("order", "vendor")
        .get(pk=vendor_order.pk)
    )
    if locked.cancel_rest_offered_at is None:
        raise AmendmentRefused(gettext("There is no offer to cancel the rest of this package."))
    if locked.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        raise AmendmentRefused(
            gettext(
                "This package has already shipped or is closed, so the rest of it can no longer "
                "be cancelled."
            )
        )
    return locked


def cancel_rest_of_package(
    vendor_order: VendorOrder,
    *,
    actor: User | None,
    ctx: AuditContext,
    verified_guest: bool = False,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """The customer cancels the rest of the package after a partial unavailability, free of
    charge, before shipping (spec 07, minor change 2): a full refund of what is left under PAY.4
    (the refund fee is the vendor's, D-05), due within 14 days; the package is cancelled. A guest
    answers through the order's signed link (`verified_guest`, checked by the adapter)."""
    _customer_may_answer(vendor_order.order, actor, verified_guest)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    refund_changes: list[Change] = []
    refund_id = ""
    try:
        with transaction.atomic():
            locked = _lock_offer(vendor_order)
            _refuse_withdrawn(locked)
            before = _offer_snapshot(locked)
            lines = amendment_notices.current_lines(locked)
            VendorOrder.objects.filter(pk=locked.pk).update(
                cancel_rest_offered_at=None, ship_hold_until=None
            )
            outcome = _hook("ORDER_CANCEL_REST_REFUND_HOOK")(
                locked, ctx=ctx, now=stamp, dry_run=dry_run
            )
            refund_changes = outcome.changes
            # The package is cancelled with the refund recorded, before the provider is called:
            # it can never be shipped while its refund is on the way.
            apply_refund_to_states(locked, fully_refunded=True, ctx=ctx)
            locked.refresh_from_db()
            if locked.state not in {"cancelled", "refunded"}:
                raise AmendmentRefused(
                    gettext(
                        "The package could not be cancelled (it is %(state)s); nothing changed."
                    )
                    % {"state": locked.state}
                )
            refund_id = outcome.refund_id
            changeset.update(
                "orders.vendororder",
                str(locked.public_id),
                before,
                {
                    **_offer_snapshot(locked),
                    "refund": outcome.refund_id,
                    "refunded": outcome.amount,
                    "kind": KIND_REST_CANCELLED,
                },
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_package_cancelled")
            amendment_notices.package_cancelled(
                locked,
                by=amendment_notices.BY_CUSTOMER,
                cause=CANCEL_CUSTOMER_REQUEST,
                reason="",
                refunded=outcome.amount,
                refund_fee=outcome.fee_charged,
                refund_ref=outcome.refund_id,
                lines=lines,
            )
    except _DryRunRollback:
        refund_id = ""
    vendor_order.refresh_from_db()
    if refund_id:
        _submit(refund_id, ctx)
    return ChangeSet([*changeset.changes, *refund_changes])


def keep_rest_of_package(
    vendor_order: VendorOrder,
    *,
    actor: User | None,
    ctx: AuditContext,
    verified_guest: bool = False,
    dry_run: bool = False,
) -> ChangeSet:
    """The customer keeps the rest: the shipping hold ends at once, so the seller may ship. The
    offer itself stays open until shipping (the counsel clause: the customer may cancel the rest
    free of charge before shipping), so a later change of mind still works."""
    _customer_may_answer(vendor_order.order, actor, verified_guest)
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = _lock_offer(vendor_order)
            before = _offer_snapshot(locked)
            VendorOrder.objects.filter(pk=locked.pk).update(ship_hold_until=None)
            locked.refresh_from_db()
            changeset.update(
                "orders.vendororder", str(locked.public_id), before, _offer_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_rest_kept")
    except _DryRunRollback:
        pass
    vendor_order.refresh_from_db()
    return changeset


# --- the vendor's invoice (spec 07, Invoicing) --------------------------------------------------


def record_invoice_issued(
    vendor_order: VendorOrder,
    *,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """The shop records that it already issued its invoice or receipt for this package (spec 07,
    Invoicing): from then on every applied change and cancellation shows the invoice notice
    first and marked important. It never blocks a change. Only the shop's own side (the owner and
    members with ORDERS) records it, once; platform staff never issue the vendor's documents."""
    sub = _scoped(vendor_order, actor)
    if initiator_of(actor, sub) in am.STAFF_INITIATORS:
        raise PermissionDenied(gettext("Only the shop records its own invoice."))
    stamp = now or timezone.now()
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = (
                VendorOrder.objects.select_for_update(of=("self",))
                .select_related("order", "vendor")
                .get(pk=sub.pk)
            )
            if locked.order.state not in PAID_OR_LATER:
                raise AmendmentRefused(gettext("An order that is not paid has no invoice yet."))
            if locked.invoice_issued_at is not None:
                raise AmendmentRefused(
                    gettext("The invoice of this order is already recorded as issued.")
                )
            flags = {VENDOR_FIELD: str(locked.vendor.public_id)}
            VendorOrder.objects.filter(pk=locked.pk).update(invoice_issued_at=stamp)
            changeset.update(
                "orders.vendororder",
                str(locked.public_id),
                {**flags, "invoice_issued_at": None},
                {**flags, "invoice_issued_at": stamp},
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.invoice_issued_recorded")
    except _DryRunRollback:
        pass
    vendor_order.refresh_from_db()
    return changeset


# --- staff: package cancellations per shop and cause (ORD.22) -----------------------------------

#: The default period of the staff counts (spec 07, ORD.22: a chosen period, 90 days by default).
CANCELLATION_COUNT_DAYS = 90


@dataclass
class ShopCancellations:
    """One shop's package cancellations in the period, by who cancelled and why. The shop's own
    `customer_request` cancellations drop the refund fee on the shop's word alone, so staff look
    at them (owner, #577)."""

    vendor_public_id: str
    vendor_name: str = ""
    shop_fault: int = 0
    shop_customer_request: int = 0
    staff: int = 0
    customer_rest: int = 0

    @property
    def total(self) -> int:
        return self.shop_fault + self.shop_customer_request + self.staff + self.customer_rest


def cancellation_counts(
    *, days: int = CANCELLATION_COUNT_DAYS, now: dt.datetime | None = None
) -> list[ShopCancellations]:
    """The package cancellations of the last `days` per shop, read from their audit entries,
    the shops with the most self-declared `customer_request` cancellations first."""
    since = (now or timezone.now()) - dt.timedelta(days=days)
    rows: dict[str, ShopCancellations] = {}
    entries = AuditLog.objects.filter(
        action="orders.amendment_package_cancelled",
        target_model="orders.vendororder",
        created_at__gte=since,
    ).only("after")
    for entry in entries.iterator():
        after = entry.after or {}
        shop = str(after.get(VENDOR_FIELD) or "")
        if not shop:
            continue
        row = rows.setdefault(shop, ShopCancellations(vendor_public_id=shop))
        if cancellation_kind(after) == KIND_REST_CANCELLED:
            row.customer_rest += 1
        elif after.get("initiator") in am.STAFF_INITIATORS:
            row.staff += 1
        elif after.get("cancel_cause") == CANCEL_CUSTOMER_REQUEST:
            row.shop_customer_request += 1
        else:
            row.shop_fault += 1
    names = {
        str(public_id): name
        for public_id, name in Vendor.objects.filter(public_id__in=list(rows)).values_list(
            "public_id", "display_name"
        )
    }
    for row in rows.values():
        row.vendor_name = str(names.get(row.vendor_public_id, row.vendor_public_id))
    return sorted(rows.values(), key=lambda r: (-r.shop_customer_request, -r.total, r.vendor_name))
