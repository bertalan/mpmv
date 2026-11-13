"""Stripe Express onboarding of a vendor (VEN.6, owner decision D-19).

The vendor owner creates the connected account and gets an onboarding link from the Manager; the
provider's `account.updated` webhook (and the return from the link) keep the mirrored status up to
date. Nothing here writes the vendor's sales mode: the mode buyers get is derived at read time
(`vendors.services.effective_sales_mode`), so a vendor without an active account is on request
(catalog visible, no cart) and a vendor that chose on request stays on request after activation.
The account status changes are audited (`payments.connected_account.*`). Stripe is reached only
through `StripeClient`; the account id comes from our own row, never from request data, so a
vendor cannot act on another vendor's account.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
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


DEMO_ACCOUNT_PREFIX = "acct_demo_"


def record_demo_account(
    vendor: Vendor, *, number: int, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """A ready payout account for a demo vendor (ARC.10 seed only, owner decision 2026-11-13).

    The id `acct_demo_<n>` is obviously fake and never reaches the provider; the model has no
    demo flag, so the prefix marks the row as test data. Only the demo seed calls this, and the
    seed refuses to run on live data, and so does this (defence in depth). Idempotent: a vendor
    that has an account keeps it as is."""
    if getattr(settings, "LIVE_DATA", False):
        raise PermissionDenied("Demo payout accounts are never created on live data.")
    if ConnectedAccount.objects.filter(vendor=vendor).exists():
        return ChangeSet()
    account = ConnectedAccount(
        vendor=vendor,
        provider=PROVIDER,
        account_ref=f"{DEMO_ACCOUNT_PREFIX}{number}",
        status=stripe_engine.ACTIVE,
        details_submitted=True,
        charges_enabled=True,
        payouts_enabled=True,
    )
    changes = ChangeSet()
    changes.create(CONNECTED_ACCOUNT_MODEL, str(vendor.public_id), _snapshot(account))
    run_mutation(changes, account.save, ctx=ctx, action=CONNECTED_ACCOUNT_CREATED, dry_run=dry_run)
    return changes


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
    if account is None or account.account_ref.startswith(DEMO_ACCOUNT_PREFIX):
        return ChangeSet()  # a demo account (STAGE seed) is not known to the provider
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
    """Mirror the provider's report on the account row and audit it. The vendor's stored sales
    mode is never written: readiness is read from this row (`vendors.services.payout_ready`).

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
    account.refresh_from_db()
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
