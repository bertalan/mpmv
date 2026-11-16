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
The customer messages come with ORD.20 part 3.

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

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.services import USER_MODEL
from apps.catalog.models import Product, Variant
from apps.core.audit import VENDOR_FIELD, AuditContext, record
from apps.core.changeset import Change, ChangeSet
from apps.core.countries import InvalidCountry
from apps.core.models import AuditLog
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


class _DryRunRollback(Exception):
    pass


# --- who acts -----------------------------------------------------------------------------------


def _same_side(initiator: str, drafted_by: str) -> None:
    """The platform and the shop never act on each other's drafts (staff limits, passkeys)."""
    if initiator != drafted_by and (
        initiator in am.STAFF_INITIATORS or drafted_by in am.STAFF_INITIATORS
    ):
        raise AmendmentRefused(
            "This draft was started by "
            + ("platform staff" if drafted_by in am.STAFF_INITIATORS else "the shop")
            + ": only that side may change or apply it."
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
            "The shop's open orders are frozen pending staff review, so they cannot be amended."
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


def withdrawn(sub: VendorOrder) -> bool:
    """Whether a consumer withdrawal is registered on the package, through the hooks in
    `settings.ORDER_WITHDRAWAL_HOOKS` (orders never imports compliance, which records them)."""
    return any(import_string(path)(sub) for path in settings.ORDER_WITHDRAWAL_HOOKS)


def _refuse_withdrawn(sub: VendorOrder) -> None:
    if withdrawn(sub):
        raise AmendmentRefused(
            "The customer has withdrawn from this package, so it can no longer be changed: the "
            "withdrawal and its refund run on the current version."
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
            _refuse_withdrawn(locked)
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
    if not passkey_session or not levels.spend_grant(
        passkey_session, actor, ask.action, ask.digest
    ):
        raise PasskeyRequired(
            "This change "
            + (
                "moves the delivery address"
                if ask.action == am.ACTION_ADDRESS
                else "refunds money to the customer"
            )
            + ", so it needs a fresh passkey confirmation for this amendment (an open passkey "
            "window does not cover it; a confirmation given before the draft was edited no "
            "longer counts).",
            action=ask.action,
            digest=ask.digest,
        )
    record(
        ctx,
        levels.SINGLE_USED,
        USER_MODEL,
        str(actor.public_id),
        None,
        {
            "kind": "single",
            "action": ask.action,
            "shop": levels.shop_key(amendment.vendor_order.vendor),
            "amendment": str(amendment.public_id),
        },
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
    unavailability the package is held from shipping for `DEFAULT_CANCEL_HOLD_HOURS` and the
    customer may cancel the rest. A dry run needs no passkey, calls no provider and writes
    nothing; it returns the ChangeSet with the refund estimate."""
    sub = _scoped(amendment.vendor_order, actor)
    initiator = initiator_of(actor, sub)
    stamp = now or timezone.now()
    changeset = ChangeSet()
    refund_changes: list[Change] = []
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
            changeset.update(
                "orders.orderamendment", str(locked.public_id), before, _amendment_snapshot(locked)
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action=action)
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
            "This is a substantial change: it needs the customer's acceptance first."
        )
    changes = [am.change_from_json(c) for c in amendment.changes]
    preview, resolved, address = _recompute(sub, amendment.from_version, changes, at=at)
    # The price is quoted again at apply: a staff draft must still meet the staff limits.
    error = am.staff_change_error(amendment.initiator, resolved) or am.staff_carrier_error(
        amendment.initiator, preview, resolved
    )
    if error:
        raise AmendmentRefused(error)
    result = am.classify_amendment(preview, resolved, amendment.initiator)
    if result.kind != am.MINOR:
        raise AmendmentRefused(
            "Recomputed today, this is a substantial change ("
            + "; ".join(result.reasons)
            + "): it needs the customer's acceptance first."
        )
    drafted = Decimal(str(amendment.preview.get("money_delta") or "0"))
    if preview.money_delta != drafted:
        raise AmendmentRefused(
            f"Recomputed today, this change moves {preview.money_delta} instead of the "
            f"{drafted} shown in the draft: edit the draft so it shows today's amounts, then "
            "confirm it again."
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
            ship_hold_until=at + dt.timedelta(hours=am.DEFAULT_CANCEL_HOLD_HOURS),
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
                raise AmendmentRefused(f"Only a draft can be edited; this one is {locked.state}.")
            _same_side(initiator, locked.initiator)
            new_reason = (reason if reason is not None else locked.reason).strip()
            new_note = (staff_note if staff_note is not None else locked.staff_note).strip()
            if not new_reason:
                raise AmendmentRefused("Give the reason for the change; the customer will read it.")
            if initiator in am.STAFF_INITIATORS:
                if not new_note:
                    raise AmendmentRefused("Platform staff must add a note to every amendment.")
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
            "The package changed since this draft was made, so the draft was closed as "
            "superseded: start a new one."
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
    result = am.classify_amendment(preview, resolved, initiator)
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


# --- the cancel-the-rest offer ------------------------------------------------------------------


def _customer_may_answer(order: Order, actor: User | None, verified_guest: bool) -> None:
    if verified_guest:
        return  # the adapter checked the order's signed link (a guest order)
    if actor is None or order.customer_id is None or actor.pk != order.customer_id:
        raise PermissionDenied("Only the customer who placed the order may answer this offer.")


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
        raise AmendmentRefused("There is no offer to cancel the rest of this package.")
    if locked.state not in am.AMENDABLE_VENDOR_ORDER_STATES:
        raise AmendmentRefused(
            f"This package is {locked.state}, so the rest of it can no longer be cancelled."
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
                    f"The package could not be cancelled (it is {locked.state}); nothing changed."
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
                },
            )
            if dry_run:
                raise _DryRunRollback
            audit_changes(changeset, ctx=ctx, action="orders.amendment_package_cancelled")
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
    """The customer answers the offer by keeping the rest: the shipping hold ends at once."""
    _customer_may_answer(vendor_order.order, actor, verified_guest)
    changeset = ChangeSet()
    try:
        with transaction.atomic():
            locked = _lock_offer(vendor_order)
            before = _offer_snapshot(locked)
            VendorOrder.objects.filter(pk=locked.pk).update(
                cancel_rest_offered_at=None, ship_hold_until=None
            )
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
