"""Order amendments, part 1 of ORD.20 (owner decision D-47; spec 07 §Order amendments).

`draft_amendment` checks who acts and on which package, replays the checkout on the current
version with the typed changes (`amendment_engine.recompute_vendor_order`), classifies the result
(`classify_amendment`, never the actor) and stores an `OrderAmendment` in `draft`. A change of
delivery address runs the checkout's eligibility and blocking checks on the new destination
(tax context and goods decisions, the vendor's shipping, Northern Ireland for a vendor above the
threshold without OSS) and an unsupported destination refuses the draft with a plain reason.

`apply_amendment` writes the next version. In this part it applies only what moves no money and
needs no passkey (a carrier change at the same price, or a dearer one the vendor absorbs); refunds
through PAY.4, the passkey of critical actions (CORE.25) and the customer messages come with
ORD.20 parts 2 and 3. Every apply first checks that the version did not move since the draft:
otherwise the amendment becomes `superseded` and nothing moves.

Like `checkout`, this module joins orders with the tax and shipping services; tax and shipping
never import orders.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import InvalidCountry
from apps.core.services import PLATFORM_OWNER_PERMISSION, audit_changes
from apps.shipping import engine as shipping_engine
from apps.shipping.services import quote_shipping
from apps.tax import engine as tax_engine
from apps.tax import services as tax_services
from apps.tax.models import Territory
from apps.vendors import engine as vendors_engine
from apps.vendors.scoping import is_platform_staff
from apps.vendors.services import has_vendor_permission, member_role

from . import amendment_engine as am
from . import engine, versions
from .models import (
    OPEN_AMENDMENT_STATES,
    AmendmentState,
    OrderAmendment,
    VendorOrder,
    VendorOrderVersion,
)
from .services import PAID_OR_LATER
from .tz import business_tz

AmendmentRefused = am.AmendmentRefused
AmendmentBlocked = am.AmendmentBlocked

CUSTOMER_REQUEST_KEYS = ("channel", "date", "text")


class _DryRunRollback(Exception):
    pass


# --- who acts -----------------------------------------------------------------------------------


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
            raise PermissionDenied("Only the shop's owner or a member with orders may amend.")
        return am.VENDOR_OWNER if role == vendors_engine.OWNER else am.VENDOR_MEMBER
    if is_platform_staff(actor):
        return am.PLATFORM_OWNER if actor.has_perm(PLATFORM_OWNER_PERMISSION) else am.SUPPORT
    raise PermissionDenied("Only the shop or platform staff may amend an order.")


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
            f"The seller cannot deliver to {place}, so the address cannot be changed to it "
            f"({outcome.message})"
        )
    if isinstance(outcome, shipping_engine.NeedsQuote | shipping_engine.IndicativeRates):
        raise AmendmentBlocked(
            f"The seller has no fixed shipping price for {place}, so the address cannot be "
            "changed to it: such a change would need a shipping quote, which amendments do not "
            "support yet."
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
            "The new address is incomplete: " + ", ".join(missing) + " is missing."
        )
    country, postcode = str(address["country"]), str(address["postcode"])
    try:
        destination = shipping_engine.Destination(
            country, postcode, str(address.get("state") or "")
        )
    except InvalidCountry:
        raise AmendmentRefused(f"{country!r} is not a country the address can be in.") from None
    place = _place(destination.country, postcode)

    def blocked(error: Exception) -> AmendmentBlocked:
        return AmendmentBlocked(
            f"VAT cannot be decided for a delivery to {place}, so the address cannot be changed "
            f"to it ({error})."
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
            f"This is a business order, so the address cannot be moved to {place} without a new "
            "VAT number check for that destination, which amendments do not support yet."
        )
    rates = _rates(sub, snapshot, destination, place)
    current = next((r for r in rates if r.key == snapshot.shipping.key), None)
    if current is None:
        raise AmendmentBlocked(
            f"The package's shipping method ({snapshot.shipping.label or snapshot.shipping.key}) "
            f"does not deliver to {place}, so the address cannot be changed to it."
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
                    f"The shipping method {change.offer.key!r} is not offered for this package."
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
        except tax_engine.BlockingTaxError as error:
            raise AmendmentRefused(f"VAT cannot be decided for this package ({error}).") from None
    ctx = am.RecomputeContext(
        tax_on=at.astimezone(business_tz()).date(),
        tax=tax,
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
        raise AmendmentRefused(f"VAT cannot be decided for this change ({error}).") from None
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


def _amendment_snapshot(amendment: OrderAmendment) -> dict[str, Any]:
    sub = amendment.vendor_order
    return {
        VENDOR_FIELD: str(sub.vendor.public_id),
        "order": sub.order.number,
        "state": amendment.state,
        "kind": amendment.kind,
        "reasons": list(amendment.reasons),
        "initiator": amendment.initiator,
        "reason": amendment.reason,
        "from_version": amendment.from_version.number,
        "changes": [
            am.change_to_json(am.change_from_json(c), with_address=False) for c in amendment.changes
        ],
        "money_delta": amendment.preview.get("money_delta"),
        "commission_delta": amendment.preview.get("commission_delta"),
    }


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
            "An address change asked by the customer needs the request's "
            + ", ".join(missing)
            + "."
        )
    return {key: str(request[key]) for key in CUSTOMER_REQUEST_KEYS}


def _lock_amendable(sub: VendorOrder) -> VendorOrder:
    locked = (
        VendorOrder.objects.select_for_update().select_related("order", "vendor").get(pk=sub.pk)
    )
    if locked.order.state not in PAID_OR_LATER:
        raise AmendmentRefused(
            "This order is not paid yet: change it through the checkout or the quote instead."
        )
    if locked.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        raise AmendmentRefused(
            f"This package is {locked.state}: an order can be amended only before shipping, "
            "while the package is pending or accepted."
        )
    return locked


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
        raise AmendmentRefused("Give the reason for the change; the customer will read it.")
    if initiator in am.STAFF_INITIATORS:
        if not staff_note.strip():
            raise AmendmentRefused("Platform staff must add a note to every amendment.")
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
            open_one = OrderAmendment.objects.filter(
                vendor_order=locked, state__in=OPEN_AMENDMENT_STATES
            ).first()
            if open_one is not None:
                raise AmendmentRefused(
                    f"This package already has an open amendment ({open_one.public_id}, "
                    f"{open_one.state}): finish, withdraw or discard it before drafting another."
                )
            version = versions.current_version(locked)
            preview, resolved, _ = _recompute(locked, version, changes, at=stamp)
            error = am.staff_carrier_error(initiator, preview, resolved)
            if error:
                raise AmendmentRefused(error)
            result = am.classify_amendment(preview, resolved, initiator)
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
                    "This package already has an open amendment: finish, withdraw or discard it "
                    "before drafting another."
                ) from None
            changeset.create(
                "orders.orderamendment", str(amendment.public_id), _amendment_snapshot(amendment)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment.draft")
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
                    f"Only a draft can be discarded; this one is {locked.state}."
                )
            before = _amendment_snapshot(locked)
            locked.state = AmendmentState.DISCARDED
            locked.save(update_fields=["state", "updated_at"])
            changeset.update(
                "orders.orderamendment", str(locked.public_id), before, _amendment_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment.discard")
    except _DryRunRollback:
        pass
    amendment.refresh_from_db()
    return changeset


# --- applying -----------------------------------------------------------------------------------


def _moves_money_or_needs_a_passkey(
    preview: am.AmendmentPreview, changes: Sequence[am.AmendmentChange]
) -> bool:
    return preview.money_delta != 0 or any(isinstance(c, am.ChangeAddress) for c in changes)


def apply_amendment(
    amendment: OrderAmendment,
    *,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> ChangeSet:
    """Apply a minor draft: write the next version of the package and make it current.

    The vendor order is locked first; when its current version is no longer the draft's
    `from_version`, the amendment becomes `superseded` and nothing else moves. A substantial
    amendment needs the customer's acceptance (ORD.21). In this part, an amendment that moves
    money or changes the address is refused: refunds and the passkey come with ORD.20 part 2."""
    sub = _scoped(amendment.vendor_order, actor)
    initiator_of(actor, sub)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked_sub = _lock_amendable(sub)
            locked = (
                OrderAmendment.objects.select_for_update()
                .select_related("from_version", "vendor_order__vendor", "vendor_order__order")
                .get(pk=amendment.pk)
            )
            if locked.state != AmendmentState.DRAFT:
                raise AmendmentRefused(f"Only a draft can be applied; this one is {locked.state}.")
            before = _amendment_snapshot(locked)
            if locked_sub.current_version_id != locked.from_version_id:
                # The version moved under the draft: it is superseded and nothing else moves.
                locked.state = AmendmentState.SUPERSEDED
                locked.save(update_fields=["state", "updated_at"])
                action = "orders.amendment.superseded"
            else:
                version = _write_next_version(locked_sub, locked, at=stamp)
                changeset.create(
                    "orders.vendororderversion",
                    str(version.public_id),
                    versions.audit_snapshot(version),
                )
                action = "orders.amendment.apply"
            changeset.update(
                "orders.orderamendment", str(locked.public_id), before, _amendment_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action=action)
    except _DryRunRollback:
        pass
    amendment.refresh_from_db()
    return changeset


def _write_next_version(
    sub: VendorOrder, amendment: OrderAmendment, *, at: dt.datetime
) -> VendorOrderVersion:
    """Recompute the minor draft on the apply date and write the next version."""
    if amendment.kind != am.MINOR:
        raise AmendmentRefused(
            "This is a substantial change: it needs the customer's acceptance first."
        )
    changes = [am.change_from_json(c) for c in amendment.changes]
    preview, resolved, address = _recompute(sub, amendment.from_version, changes, at=at)
    result = am.classify_amendment(preview, resolved, amendment.initiator)
    if result.kind != am.MINOR:
        raise AmendmentRefused(
            "Recomputed today, this is a substantial change ("
            + "; ".join(result.reasons)
            + "): it needs the customer's acceptance first."
        )
    if _moves_money_or_needs_a_passkey(preview, resolved):
        raise AmendmentRefused(
            "This amendment moves money or changes the address: applying it needs the refund "
            "and passkey steps, which are not available yet."
        )
    version = versions.create_next_version(
        sub,
        preview.after,
        number=amendment.from_version.number + 1,
        amendment=amendment,
        shipping_address=address,
    )
    amendment.state = AmendmentState.APPLIED
    amendment.preview = preview.to_json()
    amendment.changes = [am.change_to_json(c) for c in resolved]
    amendment.save(update_fields=["state", "preview", "changes", "updated_at"])
    return version
