"""Tax configuration services (TAX.1): versioned rates and vendor tax profiles, territories,
legal notes and the VIES answer cache; the regime change workflow and the consistency re-check
of profiles against the declared fiscal regime (TAX.2).

Rates and profiles follow the versioned-configuration pattern of the platform operator (ARC.7,
docs/dev/audit-trail.md): a change is a new row valid from a date, which closes the current
open-ended row of the same key, with one audit entry per row and the replaced version as
`before`. Every mutation accepts `dry_run` and returns its ChangeSet. Recording a VIES answer is
a cache write, not a configuration change: no ChangeSet, no audit entry (the vendor's own VAT
check is audited by `apps.vendors.services.check_vat_number`). `verify_vat_number` (TAX.5)
puts the cache, the VIES client and the fail-closed policy together.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import normalize_country
from apps.core.models import Versioned
from apps.core.services import run_mutation, save_audited
from apps.core.validity import NoValidRow, next_start_error, pick_valid
from apps.vendors.engine import VendorAction, split_vat_number
from apps.vendors.models import Vendor
from apps.vendors.services import has_vendor_permission, save_vendor

from . import engine
from .models import (
    TaxRate,
    TaxRegimeChangeRequest,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
    vat_registration_country,
)
from .vies import ViesClient, get_vies_client

TAX_RATE_MODEL = "tax.taxrate"
RATE_CONFIGURED = "tax.rate.configured"
RATE_FIELDS = ("country", "territory", "category", "rate", "source", "valid_from", "valid_to")

PROFILE_MODEL = "tax.vendortaxprofile"
PROFILE_CONFIGURED = "tax.vendor_tax_profile.configured"
PROFILE_FIELDS = (
    "regime",
    "exempt_reason",
    "exempt_country",
    "oss_registered_since",
    "valid_from",
    "valid_to",
)

TERRITORY_FIELDS = ("code", "name", "country", "vat_status", "postcode_patterns")
LEGAL_NOTE_FIELDS = ("language", "status", "legal_note")

logger = logging.getLogger(__name__)


class NoTaxRate(NoValidRow):
    """No rate is configured for the country, territory and category at that instant."""


class NoVendorTaxProfile(NoValidRow):
    """The vendor has no tax profile valid at that instant."""


def _valid_at[V: Versioned](rows: "QuerySet[V]", at: dt.datetime, *, what: str) -> V:
    covering = rows.filter(valid_from__lte=at).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
    return pick_valid(covering, at, what=what)


def _check_start(new: Versioned, latest: Versioned | None) -> None:
    error = next_start_error(new.valid_from, latest)
    if error is not None:
        raise ValidationError({"valid_from": error})


def _configure_version(
    new: Versioned,
    rows: "QuerySet[Any]",
    snapshot: Any,
    *,
    model: str,
    action: str,
    ctx: AuditContext,
    dry_run: bool,
) -> ChangeSet:
    """Add `new` after the latest row of `rows` (same key), closing it if it is open-ended."""
    if new.pk is not None:
        raise ValueError(f"Pass a new, unsaved {type(new).__name__}")
    new.valid_to = None
    new.full_clean(validate_constraints=False)  # constraints are enforced by the database
    latest = rows.order_by("-valid_from").first()  # the exclusion constraint settles races
    _check_start(new, latest)

    changeset = ChangeSet()
    closing = latest if latest is not None and latest.valid_to is None else None
    if closing is not None:
        changeset.update(
            model,
            str(closing.public_id),
            {**snapshot(closing), "valid_to": None},
            {**snapshot(closing), "valid_to": new.valid_from},
        )
    changeset.create(
        model,
        str(new.public_id),
        snapshot(new),
        supersedes=snapshot(latest) if latest is not None else None,
    )

    def apply() -> None:
        if closing is not None:
            closing.valid_to = new.valid_from
            closing.save(update_fields=["valid_to", "updated_at"])
        new.save()

    return run_mutation(changeset, apply, ctx=ctx, action=action, dry_run=dry_run)


# --- Tax rates


def rate_snapshot(rate: TaxRate) -> dict[str, Any]:
    values = {name: getattr(rate, name) for name in RATE_FIELDS}
    values["territory"] = rate.territory.code if rate.territory is not None else None
    return values


def _same_key(country: str, category: str, territory: Territory | None) -> "QuerySet[TaxRate]":
    return TaxRate.objects.filter(country=country, category=category, territory=territory)


def latest_rate(new: TaxRate) -> TaxRate | None:
    """The latest row with the same country, territory and category as `new`."""
    rows = _same_key(new.country, new.category, new.territory)
    return rows.order_by("-valid_from").first()


def check_rate_start(new: TaxRate) -> None:
    """A new rate starts after the latest one of its key; for forms, before the service runs."""
    _check_start(new, latest_rate(new))


def configure_tax_rate(new: TaxRate, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Add a rate from `new.valid_from`, closing the current one of the same key.

    Existing rates are never edited: orders keep the rate they were computed with (spec 05).
    """
    return _configure_version(
        new,
        _same_key(new.country, new.category, new.territory),
        rate_snapshot,
        model=TAX_RATE_MODEL,
        action=RATE_CONFIGURED,
        ctx=ctx,
        dry_run=dry_run,
    )


def current_rate(
    country: str,
    category: str,
    *,
    territory: Territory | None = None,
    at: dt.datetime | None = None,
) -> TaxRate:
    """The rate valid at `at` (default: now). A missing rate is an error, never 0% (T6)."""
    at = at or timezone.now()
    country = normalize_country(country)
    where = f"{territory.code} " if territory is not None else f"{country} "
    try:
        return _valid_at(
            _same_key(country, category, territory), at, what=f"{where}{category} tax rate"
        )
    except NoValidRow as exc:
        raise NoTaxRate(str(exc)) from exc


# --- Territories


def territory_snapshot(territory: Territory) -> dict[str, Any]:
    return {name: getattr(territory, name) for name in TERRITORY_FIELDS}


def territory_key_errors(territory: Territory) -> dict[str, str]:
    """Why `territory` cannot change its country or code; empty when it can.

    Rates check `territory.country == country` only when they are written, and rate snapshots
    and audit entries name the territory by its code: once a territory has rates, both are fixed.
    """
    if territory.pk is None:
        return {}
    stored = Territory.objects.filter(pk=territory.pk).values("country", "code").first()
    if stored is None or not TaxRate.objects.filter(territory_id=territory.pk).exists():
        return {}
    return {
        name: f"The territory has tax rates: its {name} stays {stored[name]}."
        for name in ("country", "code")
        if getattr(territory, name) != stored[name]
    }


def save_territory(territory: Territory, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or change a territory, audited. Territories are not versioned: rates are.

    A territory with rates keeps its country and code (`territory_key_errors`); raises
    `ValidationError` otherwise, also on dry-run.
    """
    territory.full_clean()
    errors = territory_key_errors(territory)
    if errors:
        raise ValidationError(errors)
    return save_audited(
        territory,
        territory_snapshot,
        key=str(territory.public_id),
        ctx=ctx,
        created="tax.territory.created",
        updated="tax.territory.updated",
        dry_run=dry_run,
    )


# --- Vendor tax profiles


def profile_snapshot(profile: VendorTaxProfile) -> dict[str, Any]:
    values: dict[str, Any] = {VENDOR_FIELD: str(profile.vendor.public_id)}
    values.update({name: getattr(profile, name) for name in PROFILE_FIELDS})
    values["requested_by"] = profile.requested_by_id
    values["approved_by"] = profile.approved_by_id
    return values


def _check_reviewer(user: User, *, requested_by_id: int | None) -> None:
    """Refuse `user` as the approver of a VAT regime change (`engine.reviewer_error`)."""
    error = engine.reviewer_error(
        is_staff=user.is_staff,
        is_active=user.is_active,
        has_permission=user.has_perm(REVIEW_PERMISSION),
        is_requester=requested_by_id is not None and user.pk == requested_by_id,
    )
    if error is not None:
        raise engine.RegimeChangeNotAllowed(error)


def latest_tax_profile(vendor: Vendor) -> VendorTaxProfile | None:
    return VendorTaxProfile.objects.filter(vendor=vendor).order_by("-valid_from").first()


def has_tax_profile(vendor: Vendor) -> bool:
    """Whether the vendor has a tax profile, current or scheduled (VEN.18 review M2).

    From then on its declared fiscal regime changes only through a TAX.2 regime-change request.
    """
    return VendorTaxProfile.objects.filter(vendor=vendor).exists()


def check_profile_start(new: VendorTaxProfile) -> None:
    """A new profile starts after the vendor's latest one; for forms, before the service runs."""
    _check_start(new, latest_tax_profile(new.vendor))


def configure_vendor_tax_profile(
    new: VendorTaxProfile, *, approver: User, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Give the vendor a new VAT regime from `new.valid_from`, closing the current profile.

    `approver` becomes `new.approved_by`. Changing a vendor's VAT regime needs what reviewing a
    request needs (`engine.reviewer_error`): active platform staff holding the review
    permission, other than `new.requested_by`. Vendor requests go through
    `approve_regime_change` (TAX.2), which calls this with the requester; a profile added by
    staff directly has no `requested_by` (staff-initiated). The regime must agree with the
    vendor's declared fiscal regime (RF19, see `engine.profile_errors`). Raises
    `engine.RegimeChangeNotAllowed` or `ValidationError`, also on dry-run.
    """
    _check_reviewer(approver, requested_by_id=new.requested_by_id)
    new.approved_by = approver
    return _configure_version(
        new,
        VendorTaxProfile.objects.filter(vendor=new.vendor),
        profile_snapshot,
        model=PROFILE_MODEL,
        action=PROFILE_CONFIGURED,
        ctx=ctx,
        dry_run=dry_run,
    )


def current_tax_profile(vendor: Vendor, at: dt.datetime | None = None) -> VendorTaxProfile:
    """The vendor's profile valid at `at` (default: now); a clear error when there is none."""
    at = at or timezone.now()
    try:
        return _valid_at(
            VendorTaxProfile.objects.filter(vendor=vendor),
            at,
            what=f"tax profile of vendor {vendor.public_id}",
        )
    except NoValidRow as exc:
        raise NoVendorTaxProfile(str(exc)) from exc


def legal_note_snapshot(note: VendorTaxProfileTranslation) -> dict[str, Any]:
    values: dict[str, Any] = {
        VENDOR_FIELD: str(note.profile.vendor.public_id),
        "profile": str(note.profile.public_id),
    }
    values.update({name: getattr(note, name) for name in LEGAL_NOTE_FIELDS})
    return values


def legal_note_error(note: VendorTaxProfileTranslation, *, at: dt.datetime) -> str | None:
    """Why `note` cannot be written at `at`; None when it can.

    A note belongs to one profile version for good, and the notes of a version whose period has
    ended are history: they are what documents of that period printed.
    """
    if note.pk is not None:
        stored = (
            VendorTaxProfileTranslation.objects.filter(pk=note.pk)
            .values_list("profile_id", flat=True)
            .first()
        )
        if stored is not None and stored != note.profile_id:
            return "A legal note stays with the tax profile version it was written for."
    valid_to = note.profile.valid_to
    if valid_to is not None and valid_to <= at:
        return (
            f"The tax profile version ended on {valid_to.isoformat()}: its legal notes are "
            "history and cannot be changed."
        )
    return None


def save_legal_note(
    note: VendorTaxProfileTranslation, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change a profile's legal note in one language, audited under its vendor.

    Refused when the note would move to another profile version or its version has ended
    (`legal_note_error`).
    """
    note.full_clean()
    error = legal_note_error(note, at=timezone.now())
    if error is not None:
        raise ValidationError({"profile": error})
    return save_audited(
        note,
        legal_note_snapshot,
        key=str(note.public_id),
        ctx=ctx,
        created="tax.legal_note.created",
        updated="tax.legal_note.updated",
        dry_run=dry_run,
    )


# --- Regime change workflow (TAX.2)
#
# Vendor owner requests (`request_regime_change`) -> staff review: `approve_regime_change` adds
# the new profile version and, when the request moves it, the vendor's declared fiscal regime
# (VEN.12), in one transaction; `reject_regime_change` records why. The tax app orchestrates
# because it sits above vendors in the layers contract (spec 01 §1.3): it may call the vendor
# services, while vendors never import tax.

REQUEST_MODEL = "tax.taxregimechangerequest"
REGIME_CHANGE_REQUESTED = "tax.regime_change.requested"
REGIME_CHANGE_APPROVED = "tax.regime_change.approved"
REGIME_CHANGE_REJECTED = "tax.regime_change.rejected"
# Checked here, not only in the admin: every adapter (admin, API, MCP, shell) reviews through
# these services, and admin login alone (`is_staff`) does not allow a review.
REVIEW_PERMISSION = "tax.review_taxregimechangerequest"
REQUEST_FIELDS = (
    "status",
    "regime",
    "exempt_reason",
    "exempt_country",
    "oss_registered_since",
    "fiscal_regime",
    "effective_from",
    "note",
    "document_reference",
    "requested_at",
    "reviewed_at",
    "review_reason",
)


def business_date(at: dt.datetime) -> dt.date:
    """The date of `at` in business time (`settings.BUSINESS_TIME_ZONE`), as invoices see it."""
    return timezone.localdate(at, ZoneInfo(settings.BUSINESS_TIME_ZONE))


def _public_id(obj: Any) -> str | None:
    return str(obj.public_id) if obj is not None else None


def request_snapshot(request: TaxRegimeChangeRequest) -> dict[str, Any]:
    values: dict[str, Any] = {VENDOR_FIELD: str(request.vendor.public_id)}
    values.update({name: getattr(request, name) for name in REQUEST_FIELDS})
    values["requested_by"] = _public_id(request.requested_by)
    values["reviewed_by"] = _public_id(request.reviewed_by)
    values["profile"] = _public_id(request.profile)
    return values


def _same_regime(request: TaxRegimeChangeRequest, profile: VendorTaxProfile | None) -> bool:
    return profile is not None and (
        profile.regime,
        profile.exempt_reason,
        profile.exempt_country,
        profile.oss_registered_since,
    ) == (
        request.regime,
        request.exempt_reason,
        request.exempt_country,
        request.oss_registered_since,
    )


def _clear_review(request: TaxRegimeChangeRequest) -> None:
    request.reviewed_by = None
    request.reviewed_at = None
    request.review_reason = ""
    request.profile = None


def request_regime_change(
    request: TaxRegimeChangeRequest, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Store `request` (new, unsaved) as a pending request of its vendor; audited.

    `request.requested_by` must be an active owner of the vendor (role matrix, CORE.2: vendor
    staff cannot change the tax regime). The status and review fields are reset: a new request
    is pending and unreviewed. The request must be consistent with the fiscal regime
    the vendor will declare (`engine.request_errors`), start after the vendor's latest profile
    and change something. Raises `engine.RegimeChangeNotAllowed`,
    `engine.PendingRequestExists` or `ValidationError`, also on dry-run.
    """
    if not request._state.adding:
        raise ValueError("request_regime_change takes a new request, not a saved one")
    user = request.requested_by
    if not has_vendor_permission(user, request.vendor, VendorAction.TAX_REGIME):
        raise engine.RegimeChangeNotAllowed(
            f"{user} may not change the tax regime of vendor {request.vendor.public_id}"
        )
    with transaction.atomic():
        vendor = Vendor.objects.select_for_update().get(pk=request.vendor.pk)
        request.vendor = vendor
        if TaxRegimeChangeRequest.objects.filter(vendor=vendor, status=engine.PENDING).exists():
            raise engine.PendingRequestExists(
                f"vendor {vendor.public_id} already has a request waiting for review"
            )
        request.status = engine.PENDING
        request.requested_at = timezone.now()
        _clear_review(request)  # a new request is unreviewed, whatever the caller set
        request.full_clean(validate_constraints=False)
        latest = latest_tax_profile(vendor)
        error = next_start_error(request.effective_from, latest)
        if error is not None:
            raise ValidationError({"effective_from": error})
        resulting = engine.resulting_fiscal_regime(
            request.regime,
            request.exempt_reason,
            requested=request.fiscal_regime,
            current=vendor.fiscal_regime,
        )
        if _same_regime(request, latest) and resulting == vendor.fiscal_regime:
            raise ValidationError({"regime": "This is already the vendor's VAT regime."})

        changeset = ChangeSet()
        changeset.create(REQUEST_MODEL, str(request.public_id), request_snapshot(request))
        return run_mutation(
            changeset, request.save, ctx=ctx, action=REGIME_CHANGE_REQUESTED, dry_run=dry_run
        )


def _lock_for_review(request: TaxRegimeChangeRequest, reviewer: User) -> TaxRegimeChangeRequest:
    """The stored request, row locked, once the reviewer and its status allow a review."""
    current = (
        TaxRegimeChangeRequest.objects.select_for_update(of=("self",))
        .select_related("vendor", "requested_by", "reviewed_by", "profile")
        .get(pk=request.pk)
    )
    _check_reviewer(reviewer, requested_by_id=current.requested_by_id)
    engine.check_reviewable(current.status)
    return current


def _review(
    current: TaxRegimeChangeRequest,
    reviewer: User,
    *,
    status: str,
    reason: str,
    profile: VendorTaxProfile | None,
    ctx: AuditContext,
    action: str,
    dry_run: bool,
) -> ChangeSet:
    before = request_snapshot(current)
    current.status = status
    current.reviewed_by = reviewer
    current.reviewed_at = timezone.now()
    current.review_reason = reason
    current.profile = profile
    changeset = ChangeSet()
    changeset.update(REQUEST_MODEL, str(current.public_id), before, request_snapshot(current))

    def apply() -> None:
        current.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_reason",
                "profile",
                "updated_at",
            ]
        )

    return run_mutation(changeset, apply, ctx=ctx, action=action, dry_run=dry_run)


def _sync_review(request: TaxRegimeChangeRequest, current: TaxRegimeChangeRequest) -> None:
    for name in ("status", "reviewed_by", "reviewed_at", "review_reason", "profile"):
        setattr(request, name, getattr(current, name))


def approve_regime_change(
    request: TaxRegimeChangeRequest,
    reviewer: User,
    *,
    reason: str = "",
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Approve a pending request: one transaction adds the new profile version (closing the
    current one) and, when the request moves it, sets the vendor's fiscal regime.

    The checks run against the stored request and vendor, rows locked: the vendor's fiscal
    regime may have changed since the request, and the requester must still hold the
    `TAX_REGIME` permission (an owner removed or demoted since asking cannot have the request
    approved; it can still be rejected). A change of the declared fiscal regime is
    approved on or after its business-time date only (`engine.fiscal_regime_change_error`).
    Each write is
    audited by its own service (`vendor.updated`, `tax.vendor_tax_profile.configured`) plus
    `tax.regime_change.approved`; the returned ChangeSet holds them all. Raises
    `engine.RegimeChangeNotAllowed`, `engine.RequestNotPending`,
    `engine.FiscalRegimeChangeNotDue` or `ValidationError`, also on dry-run.
    """
    changeset = ChangeSet()
    with transaction.atomic():
        current = _lock_for_review(request, reviewer)
        vendor = Vendor.objects.select_for_update().get(pk=current.vendor_id)
        requester = current.requested_by
        if not has_vendor_permission(requester, vendor, VendorAction.TAX_REGIME):
            raise engine.RegimeChangeNotAllowed(
                f"{requester} may no longer change the tax regime of vendor {vendor.public_id}: "
                "reject the request instead."
            )
        errors = engine.request_errors(
            current.regime,
            current.exempt_reason,
            requested_fiscal_regime=current.fiscal_regime,
            current_fiscal_regime=vendor.fiscal_regime,
            oss_registered_since=current.oss_registered_since,
            exempt_country=current.exempt_country,
            vat_country=vat_registration_country(vendor),
        )
        if errors:
            raise ValidationError(errors)
        fiscal_regime = engine.resulting_fiscal_regime(
            current.regime,
            current.exempt_reason,
            requested=current.fiscal_regime,
            current=vendor.fiscal_regime,
        )
        if fiscal_regime != vendor.fiscal_regime:
            error = engine.fiscal_regime_change_error(
                business_date(current.effective_from), today=business_date(timezone.now())
            )
            if error is not None:
                raise engine.FiscalRegimeChangeNotDue(error)
            vendor.fiscal_regime = fiscal_regime
            changeset.changes.extend(save_vendor(vendor, ctx=ctx, dry_run=dry_run).changes)

        profile = VendorTaxProfile(
            vendor=vendor,
            regime=current.regime,
            exempt_reason=current.exempt_reason,
            exempt_country=current.exempt_country,
            oss_registered_since=current.oss_registered_since,
            valid_from=current.effective_from,
            requested_by=requester,
        )
        changeset.changes.extend(
            configure_vendor_tax_profile(
                profile, approver=reviewer, ctx=ctx, dry_run=dry_run
            ).changes
        )
        changeset.changes.extend(
            _review(
                current,
                reviewer,
                status=engine.APPROVED,
                reason=reason.strip(),
                profile=profile,
                ctx=ctx,
                action=REGIME_CHANGE_APPROVED,
                dry_run=dry_run,
            ).changes
        )
    if not dry_run:
        _sync_review(request, current)
    return changeset


def reject_regime_change(
    request: TaxRegimeChangeRequest,
    reviewer: User,
    *,
    reason: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Reject a pending request with the reason the vendor will read; audited.

    Raises `ValidationError` without a reason, `engine.RegimeChangeNotAllowed` or
    `engine.RequestNotPending`, also on dry-run.
    """
    reason = reason.strip()
    if not reason:
        raise ValidationError({"review_reason": "Say why the request is rejected."})
    with transaction.atomic():
        current = _lock_for_review(request, reviewer)
        changeset = _review(
            current,
            reviewer,
            status=engine.REJECTED,
            reason=reason,
            profile=None,
            ctx=ctx,
            action=REGIME_CHANGE_REJECTED,
            dry_run=dry_run,
        )
    if not dry_run:
        _sync_review(request, current)
    return changeset


@dataclass(frozen=True)
class RegimeHistory:
    """A vendor's tax regime history: profile versions and requests, newest first."""

    profiles: list[VendorTaxProfile]
    requests: list[TaxRegimeChangeRequest]


def regime_history(vendor: Vendor) -> RegimeHistory:
    profiles = VendorTaxProfile.objects.filter(vendor=vendor).select_related("approved_by")
    requests = TaxRegimeChangeRequest.objects.filter(vendor=vendor).select_related(
        "requested_by", "reviewed_by"
    )
    return RegimeHistory(
        profiles=list(profiles.order_by("-valid_from")),
        requests=list(requests.order_by("-requested_at", "-id")),
    )


def pending_regime_changes() -> "QuerySet[TaxRegimeChangeRequest]":
    return TaxRegimeChangeRequest.objects.filter(status=engine.PENDING)


# --- Consistency with the declared fiscal regime (the re-check TAX.1 deferred)


@dataclass(frozen=True)
class ProfileInconsistency:
    """A current profile that disagrees with its vendor's declared fiscal regime or with the
    country of its VAT number (`errors`, TAX.13), or that staff must check by hand against the
    regime (`review`, `engine.profile_review_notes`)."""

    profile: VendorTaxProfile
    errors: dict[str, str]
    review: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        problems = " ".join([*self.errors.values(), *self.review.values()])
        finding = "does not match" if self.errors else "needs a manual review against"
        return (
            f"{self.profile.vendor}: the tax profile {finding} the fiscal regime or the VAT "
            f"number. {problems}"
        )


def inconsistent_tax_profiles(at: dt.datetime | None = None) -> list[ProfileInconsistency]:
    """Profiles valid at `at` (default: now) that disagree with the vendor's fiscal regime.

    The fiscal regime (VEN.12) can change outside the regime change workflow, e.g. in the
    vendor admin, and vendors cannot import tax to stop it; this finds the result so staff can
    fix it (a regime change request, or the fiscal regime). Profiles the rules cannot confirm
    (RF20, VEN.18) are listed too, with `review` notes and no errors.
    """
    at = at or timezone.now()
    current = (
        VendorTaxProfile.objects.filter(valid_from__lte=at)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
        .select_related("vendor")
        .order_by("vendor__display_name", "vendor_id")
    )
    found = []
    for profile in current:
        errors = engine.profile_errors(
            profile.regime,
            profile.exempt_reason,
            fiscal_regime=profile.vendor.fiscal_regime,
            oss_registered_since=profile.oss_registered_since,
            exempt_country=profile.exempt_country,
            vat_country=vat_registration_country(profile.vendor),
        )
        review = engine.profile_review_notes(
            profile.regime, profile.exempt_reason, fiscal_regime=profile.vendor.fiscal_regime
        )
        if errors or review:
            found.append(ProfileInconsistency(profile=profile, errors=errors, review=review))
    return found


def _log_inconsistency(item: ProfileInconsistency) -> None:
    logger.warning(
        "tax profile %s of vendor %s %s fiscal regime %r or VAT number: %s",
        item.profile.public_id,
        item.profile.vendor.public_id,
        "disagrees with its" if item.errors else "needs a manual review against its",
        item.profile.vendor.fiscal_regime,
        "; ".join(
            f"{name}: {message}" for name, message in [*item.errors.items(), *item.review.items()]
        ),
    )


def report_inconsistent_tax_profiles(at: dt.datetime | None = None) -> int:
    """Log a warning for each inconsistent current profile; return how many there are."""
    found = inconsistent_tax_profiles(at)
    for item in found:
        _log_inconsistency(item)
    return len(found)


def report_vendor_tax_profile(vendor: Vendor) -> ProfileInconsistency | None:
    """Check the vendor's current profile now and log a warning when it is inconsistent.

    Runs when the vendor's VAT number changes (TAX.13): moving an exempt vendor's VAT number to
    another country leaves an exemption reason of the wrong country. The vendor app cannot
    import tax, so the change is reported here; staff fix it with a new profile.
    """
    now = timezone.now()
    profile = (
        VendorTaxProfile.objects.filter(vendor=vendor, valid_from__lte=now)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=now))
        .first()
    )
    if profile is None:
        return None
    profile.vendor = vendor
    errors = engine.profile_errors(
        profile.regime,
        profile.exempt_reason,
        fiscal_regime=vendor.fiscal_regime,
        oss_registered_since=profile.oss_registered_since,
        exempt_country=profile.exempt_country,
        vat_country=vat_registration_country(vendor),
    )
    if not errors:
        return None
    item = ProfileInconsistency(profile=profile, errors=errors)
    _log_inconsistency(item)
    return item


# --- VIES answers


def _vat_key(country: str, vat_number: str) -> tuple[str, str]:
    return split_vat_number(vat_number, normalize_country(country))


def record_vat_validation(
    country: str,
    vat_number: str,
    result: str,
    *,
    name: str = "",
    address: str = "",
    request_id: str = "",
    checked_at: dt.datetime | None = None,
) -> VatValidation:
    """Store one VIES answer about a VAT number (prefix and separators removed). Append-only."""
    country, number = _vat_key(country, vat_number)
    check = VatValidation(
        country=country,
        vat_number=number,
        result=result,
        name=name,
        address=address,
        request_id=request_id,
        checked_at=checked_at or timezone.now(),
    )
    check.full_clean()
    check.save()
    return check


def latest_vat_validation(
    country: str, vat_number: str, *, since: dt.datetime | None = None
) -> VatValidation | None:
    """The most recent answer about the number, optionally not older than `since`.

    How old an answer may be, and what an `unavailable` answer means, is the policy of
    `verify_vat_number` (TAX.5).
    """
    country, number = _vat_key(country, vat_number)
    rows = VatValidation.objects.filter(country=country, vat_number=number)
    if since is not None:
        rows = rows.filter(checked_at__gte=since)
    return rows.order_by("-checked_at", "-id").first()


# --- The VIES check with cache, timeout and fail-closed policy (TAX.5)


class ViesUnavailable(Exception):
    """VIES gave no answer; raised by the background re-check so that the job is retried."""


@dataclass(frozen=True)
class VatCheck:
    """Whether a VAT number counts as verified for a B2B sale, and why (spec 05 §5.2).

    `verified` is true only for a VIES `valid` answer within the cache period. `result` is the
    VIES answer, None when VIES was not asked (`reason` says why); `validation` is the stored
    answer, fresh or reused (`from_cache`).
    """

    country: str
    vat_number: str
    verified: bool
    reason: str
    message: str
    result: str | None = None
    detail: str = ""
    validation: VatValidation | None = None
    from_cache: bool = False

    @property
    def checked_at(self) -> dt.datetime | None:
        return self.validation.checked_at if self.validation is not None else None

    @property
    def request_id(self) -> str:
        return self.validation.request_id if self.validation is not None else ""

    def snapshot(self) -> dict[str, Any]:
        """The JSON-ready record an order keeps of the check (spec 05: "and in the order
        snapshot")."""
        return {
            "country": self.country,
            "vat_number": self.vat_number,
            "verified": self.verified,
            "result": self.result,
            "reason": self.reason,
            "detail": self.detail,
            "request_id": self.request_id,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "validation": _public_id(self.validation),
        }


def _vat_check(
    query: engine.ViesQuery,
    row: VatValidation,
    *,
    detail: str = "",
    from_cache: bool,
) -> VatCheck:
    reason = engine.vat_check_reason(row.result, detail)
    return VatCheck(
        country=query.country,
        vat_number=query.number,
        verified=engine.is_verified(row.result),
        reason=reason,
        message=engine.vat_check_message(reason, country=query.country),
        result=row.result,
        detail=detail,
        validation=row,
        from_cache=from_cache,
    )


def _cached_vat_validation(query: engine.ViesQuery, now: dt.datetime) -> VatValidation | None:
    """The newest valid or invalid answer within `VIES_CACHE_SECONDS`; failing that, an
    `unavailable` answer within `VIES_UNAVAILABLE_CACHE_SECONDS`, so that an outage does not
    make every checkout wait for the timeout. Older answers are never used."""
    rows = VatValidation.objects.filter(country=query.country, vat_number=query.number)
    newest = ("-checked_at", "-id")
    definitive_since = now - dt.timedelta(seconds=settings.VIES_CACHE_SECONDS)
    definitive = (
        rows.filter(result__in=(engine.VAT_VALID, engine.VAT_INVALID))
        .filter(checked_at__gte=definitive_since)
        .order_by(*newest)
        .first()
    )
    if definitive is not None:
        return definitive
    down_since = now - dt.timedelta(seconds=settings.VIES_UNAVAILABLE_CACHE_SECONDS)
    return (
        rows.filter(result=engine.VAT_UNAVAILABLE, checked_at__gte=down_since)
        .order_by(*newest)
        .first()
    )


def _ask_vies(client: ViesClient, query: engine.ViesQuery) -> engine.ViesAnswer:
    try:
        answer = client.check(query)
    except Exception:  # a client must not raise; if it does, VIES is unavailable
        logger.exception("VIES client %s raised", type(client).__name__)
        return engine.ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_CLIENT_ERROR)
    if answer.result not in engine.VAT_RESULTS:
        logger.error("VIES client %s answered %r", type(client).__name__, answer.result)
        return engine.ViesAnswer(result=engine.VAT_UNAVAILABLE, detail=engine.DETAIL_BAD_RESPONSE)
    return answer


def _fit(value: str, field: str) -> str:
    max_length = getattr(VatValidation._meta.get_field(field), "max_length", None)
    return value[:max_length] if max_length else value


def verify_vat_number(
    country: str,
    vat_number: str,
    *,
    client: ViesClient | None = None,
    refresh: bool = False,
) -> VatCheck:
    """Check a customer's VAT number for reverse charge; fails closed (spec 05 §5.2, T2).

    A recent answer is reused (`_cached_vat_validation`) unless `refresh`; otherwise VIES is
    asked once through `client` (default: `get_vies_client()`), with its timeout and no retry,
    and the answer is stored. A number VIES cannot be asked about (country outside VIES,
    another country's prefix, malformed) is not verified and stores nothing: every
    `VatValidation` row is a VIES answer. Never raises because of VIES.
    """
    try:
        query = engine.vies_query(country, vat_number)
    except engine.NotCheckable as exc:
        return VatCheck(
            country=country.strip().upper(),
            vat_number=vat_number.strip(),
            verified=False,
            reason=exc.reason,
            message=exc.message,
        )
    if not refresh:
        cached = _cached_vat_validation(query, timezone.now())
        if cached is not None:
            return _vat_check(query, cached, from_cache=True)

    answer = _ask_vies(client if client is not None else get_vies_client(), query)
    if answer.result == engine.VAT_UNAVAILABLE:
        logger.warning(
            "VIES unavailable for %s%s: %s", query.prefix, query.number, answer.detail or "-"
        )
    row = record_vat_validation(
        query.country,
        query.full_number,
        answer.result,
        name=_fit(answer.name, "name"),
        address=answer.address,
        request_id=_fit(answer.request_id, "request_id"),
    )
    return _vat_check(query, row, detail=answer.detail, from_cache=False)


def recheck_vat_number(country: str, vat_number: str) -> VatCheck:
    """Ask VIES again, skipping the cache (for the background job). Raises `ViesUnavailable`
    when VIES gives no answer, so that the job is retried with backoff."""
    check = verify_vat_number(country, vat_number, refresh=True)
    if check.result == engine.VAT_UNAVAILABLE:
        raise ViesUnavailable(check.message)
    return check
