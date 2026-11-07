"""Tax configuration services (TAX.1): versioned rates and vendor tax profiles, territories,
legal notes and the VIES answer cache.

Rates and profiles follow the versioned-configuration pattern of the platform operator (ARC.7,
docs/dev/audit-trail.md): a change is a new row valid from a date, which closes the current
open-ended row of the same key, with one audit entry per row and the replaced version as
`before`. Every mutation accepts `dry_run` and returns its ChangeSet. Recording a VIES answer is
a cache write, not a configuration change: no ChangeSet, no audit entry (the vendor's own VAT
check is audited by `apps.vendors.services.check_vat_number`).
"""

import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import normalize_country
from apps.core.services import run_mutation, save_audited
from apps.core.validity import NoValidRow, next_start_error, pick_valid
from apps.vendors.engine import split_vat_number
from apps.vendors.models import Vendor

from . import engine
from .models import (
    TaxRate,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
    Versioned,
)

TAX_RATE_MODEL = "tax.taxrate"
RATE_CONFIGURED = "tax.rate.configured"
RATE_FIELDS = ("country", "territory", "category", "rate", "source", "valid_from", "valid_to")

PROFILE_MODEL = "tax.vendortaxprofile"
PROFILE_CONFIGURED = "tax.vendor_tax_profile.configured"
PROFILE_FIELDS = (
    "regime",
    "exempt_reason",
    "oss_registered_since",
    "valid_from",
    "valid_to",
)

TERRITORY_FIELDS = ("code", "name", "country", "vat_status", "postcode_patterns")
LEGAL_NOTE_FIELDS = ("language", "status", "legal_note")


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


def save_territory(territory: Territory, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or change a territory, audited. Territories are not versioned: rates are."""
    territory.full_clean()
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


def latest_tax_profile(vendor: Vendor) -> VendorTaxProfile | None:
    return VendorTaxProfile.objects.filter(vendor=vendor).order_by("-valid_from").first()


def check_profile_start(new: VendorTaxProfile) -> None:
    """A new profile starts after the vendor's latest one; for forms, before the service runs."""
    _check_start(new, latest_tax_profile(new.vendor))


def configure_vendor_tax_profile(
    new: VendorTaxProfile, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Give the vendor a new VAT regime from `new.valid_from`, closing the current profile.

    `new.approved_by` is the staff member who approved it; the request and review workflow is
    TAX.2. The regime must agree with the vendor's declared fiscal regime (RF19, see
    `engine.profile_errors`).
    """
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


def save_legal_note(
    note: VendorTaxProfileTranslation, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change a profile's legal note in one language, audited under its vendor."""
    note.full_clean()
    return save_audited(
        note,
        legal_note_snapshot,
        key=str(note.public_id),
        ctx=ctx,
        created="tax.legal_note.created",
        updated="tax.legal_note.updated",
        dry_run=dry_run,
    )


# --- VIES answers


def _vat_key(country: str, vat_number: str) -> tuple[str, str]:
    return split_vat_number(vat_number, normalize_country(country))


def record_vat_validation(
    country: str,
    vat_number: str,
    result: str,
    *,
    source: str = engine.VAT_SOURCE_VIES,
    name: str = "",
    address: str = "",
    request_id: str = "",
    checked_at: dt.datetime | None = None,
) -> VatValidation:
    """Store one answer about a VAT number (prefix and separators removed). Append-only."""
    country, number = _vat_key(country, vat_number)
    check = VatValidation(
        country=country,
        vat_number=number,
        result=result,
        source=source,
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

    How old an answer may be, and what an `unavailable` answer means, is the VIES client's
    policy (TAX.5).
    """
    country, number = _vat_key(country, vat_number)
    rows = VatValidation.objects.filter(country=country, vat_number=number)
    if since is not None:
        rows = rows.filter(checked_at__gte=since)
    return rows.order_by("-checked_at", "-id").first()
