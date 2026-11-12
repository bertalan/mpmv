"""Stripe Express onboarding of a vendor (VEN.6, owner decision D-19).

The vendor owner creates the connected account and gets an onboarding link from the Manager; the
provider's `account.updated` webhook (and the return from the link) keep the mirrored status up to
date. A vendor without an active account sells on request only (`sales_mode_default=on_request`:
catalog visible, no cart) until the account is active, then the switch back to sale is automatic
and audited. Stripe is reached only through `StripeClient`; the account id comes from our own row,
never from request data, so a vendor cannot act on another vendor's account.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation
from apps.vendors.models import Vendor
from apps.vendors.services import PROFILE_LOCKED, has_vendor_permission

from . import stripe_engine
from .changeset_keys import (
    CONNECTED_ACCOUNT_CREATED,
    CONNECTED_ACCOUNT_MODEL,
    CONNECTED_ACCOUNT_UPDATED,
    VENDOR_MODEL,
    VENDOR_SALES_MODE_CHANGED,
)
from .models import ConnectedAccount
from .stripe_client import (
    StripeAccount,
    StripeClient,
    StripeError,
    account_from_payload,
)

PROVIDER = "stripe"
VENDOR_ACTION = "bank_account"  # the role matrix: the owner only (payout account)


@dataclass
class OnboardingResult:
    changes: ChangeSet = field(default_factory=ChangeSet)
    account: ConnectedAccount | None = None
    url: str = ""  # the provider's onboarding link; empty on a dry run or when already active


def _snapshot(account: ConnectedAccount) -> dict[str, Any]:
    return {
        "vendor": str(account.vendor.public_id),
        "provider": account.provider,
        "status": account.status,
        "details_submitted": account.details_submitted,
        "charges_enabled": account.charges_enabled,
        "payouts_enabled": account.payouts_enabled,
        "requirements_due": list(account.requirements_due),
        "disabled_reason": account.disabled_reason,
    }


def _require_owner(vendor: Vendor, actor: User) -> None:
    if not has_vendor_permission(actor, vendor, VENDOR_ACTION):
        raise PermissionDenied("Only the vendor owner connects the payout account.")
    if vendor.status in PROFILE_LOCKED:
        raise PermissionDenied("A suspended or closed vendor cannot connect a payout account.")


def has_active_account(vendor: Vendor) -> bool:
    return ConnectedAccount.objects.filter(
        vendor=vendor, provider=PROVIDER, status=stripe_engine.ACTIVE
    ).exists()


def start_onboarding(
    vendor: Vendor,
    *,
    actor: User,
    client: StripeClient,
    refresh_url: str,
    return_url: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> OnboardingResult:
    """Create the vendor's Express account if it has none, then an onboarding link for it.

    Owner only (`bank_account`). The same vendor never gets a second account: the provider call
    carries an idempotency key bound to the vendor, and the row is one per vendor. A vendor with no
    active account is put on request (D-19). A dry run reports the account that would be created
    and calls nothing."""
    _require_owner(vendor, actor)
    if not vendor.country:
        raise ValidationError({"country": "The vendor needs a country before it can be connected."})
    existing = ConnectedAccount.objects.filter(vendor=vendor, provider=PROVIDER).first()
    changes = ChangeSet()
    if dry_run:
        if existing is None:
            changes.create(
                CONNECTED_ACCOUNT_MODEL,
                str(vendor.public_id),
                {"provider": PROVIDER, "status": stripe_engine.PENDING},
            )
        return OnboardingResult(changes=changes, account=existing)
    try:
        if existing is None:
            created = client.create_account(
                country=str(vendor.country),
                email=vendor.contact_email,
                metadata={"vendor": str(vendor.public_id)},
                idempotency_key=f"vendor-account:{vendor.public_id}",
            )
            account = ConnectedAccount(vendor=vendor, provider=PROVIDER, account_ref=created.id)
            changeset = ChangeSet()
            changeset.create(CONNECTED_ACCOUNT_MODEL, str(vendor.public_id), _snapshot(account))
            try:
                with transaction.atomic():
                    run_mutation(
                        changeset,
                        account.save,
                        ctx=ctx,
                        action=CONNECTED_ACCOUNT_CREATED,
                        dry_run=False,
                    )
            except IntegrityError:  # a concurrent request created it first: use that one
                account = ConnectedAccount.objects.get(vendor=vendor, provider=PROVIDER)
            else:
                changes.changes.extend(changeset.changes)
        else:
            account = existing
        changes.changes.extend(sync_sales_mode(vendor, ctx=ctx, account=account).changes)
        url = ""
        if not account.is_active:
            url = client.create_account_link(
                account_id=account.account_ref, refresh_url=refresh_url, return_url=return_url
            )
    except StripeError as error:
        raise ValidationError(
            "The payment provider could not start the onboarding. Try again later."
        ) from error
    return OnboardingResult(changes=changes, account=account, url=url)


def refresh_account(
    vendor: Vendor,
    *,
    actor: User,
    client: StripeClient,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Read the vendor's own account from the provider and mirror it (on return from the link).

    The webhook does the same asynchronously; this makes the page right at once. The account id
    comes from our row, never from the request."""
    _require_owner(vendor, actor)
    account = ConnectedAccount.objects.filter(vendor=vendor, provider=PROVIDER).first()
    if account is None:
        return ChangeSet()
    try:
        state = client.get_account(account.account_ref)
    except StripeError as error:
        raise ValidationError("The payment provider could not be reached. Try again.") from error
    return apply_account_state(account, state, ctx=ctx, dry_run=dry_run)


def apply_account_state(
    account: ConnectedAccount,
    state: StripeAccount,
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Mirror the provider's report on the account row, audit it and sync the vendor's sales mode.

    Idempotent: a repeated report changes nothing and audits nothing."""
    changes = ChangeSet()
    with transaction.atomic():
        locked = ConnectedAccount.objects.select_for_update().get(pk=account.pk)
        before = _snapshot(locked)
        locked.details_submitted = state.details_submitted
        locked.charges_enabled = state.charges_enabled
        locked.payouts_enabled = state.payouts_enabled
        locked.requirements_due = list(state.requirements_due)
        locked.disabled_reason = state.disabled_reason
        locked.status = stripe_engine.connect_status(
            details_submitted=state.details_submitted,
            charges_enabled=state.charges_enabled,
            payouts_enabled=state.payouts_enabled,
        )
        changes.update(
            CONNECTED_ACCOUNT_MODEL, str(locked.vendor.public_id), before, _snapshot(locked)
        )
        if dry_run:
            return changes
        run_mutation(changes, locked.save, ctx=ctx, action=CONNECTED_ACCOUNT_UPDATED, dry_run=False)
        changes.changes.extend(sync_sales_mode(locked.vendor, ctx=ctx, account=locked).changes)
    account.refresh_from_db()
    return changes


def sync_sales_mode(
    vendor: Vendor, *, ctx: AuditContext, account: ConnectedAccount | None = None
) -> ChangeSet:
    """Apply D-19 to the vendor's default sales mode, audited.

    No active account and the vendor sells: it goes on request, and the account row (when there
    is one) remembers that the platform did it. Active account and the platform had forced on
    request: back to sale. A vendor that chose on request itself is never switched to sale."""
    if account is None:
        account = ConnectedAccount.objects.filter(vendor=vendor, provider=PROVIDER).first()
    active = account is not None and account.is_active
    forced = account.on_request_forced if account is not None else False
    changes = ChangeSet()
    with transaction.atomic():
        row = Vendor.objects.select_for_update().get(pk=vendor.pk)
        current = row.sales_mode_default
        target = current
        if not active and current == Vendor.SalesMode.BUY:
            target, forced = Vendor.SalesMode.ON_REQUEST, True
        elif active and current == Vendor.SalesMode.ON_REQUEST and forced:
            target, forced = Vendor.SalesMode.BUY, False
        changes.update(
            VENDOR_MODEL,
            str(vendor.public_id),
            {"sales_mode_default": current},
            {"sales_mode_default": target},
        )

        def apply() -> None:
            row.sales_mode_default = target
            row.save(update_fields=["sales_mode_default", "updated_at"])
            vendor.sales_mode_default = target
            if account is not None and account.on_request_forced != forced:
                account.on_request_forced = forced
                account.save(update_fields=["on_request_forced", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=VENDOR_SALES_MODE_CHANGED, dry_run=False)
    return changes


def handle_account_updated(payload: Mapping[str, Any], *, ctx: AuditContext) -> ChangeSet:
    """The `account.updated` webhook event: find our row by the provider's account id and mirror.

    An account this platform did not create changes nothing. A payload without an id raises
    `KeyError`, which the webhook processing reports as malformed."""
    state = account_from_payload(payload.get("object", payload))
    account = (
        ConnectedAccount.objects.select_related("vendor")
        .filter(provider=PROVIDER, account_ref=state.id)
        .first()
    )
    if account is None:
        return ChangeSet()
    return apply_account_state(account, state, ctx=ctx)
