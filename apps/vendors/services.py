"""Vendor services: vendor data and status changes (VEN.1, SEC.4), memberships with their
permissions (CORE.2), the legal identity with its payout requirements (VEN.12) and onboarding:
application, versioned contract, acceptance and the activation gates (VEN.5, the establishment
gate of CMP.13); per-vendor feature flag overrides (ARC.8); claiming an imported vendor
(VEN.24)."""

import dataclasses
import datetime as dt
import logging
from collections.abc import Collection, Mapping
from typing import Any

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import OuterRef, QuerySet, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.accounts import engine as accounts_engine
from apps.accounts.models import User, normalize_email_address
from apps.accounts.services import (
    ActionLinkCheck,
    ActionLinkSpent,
    ActionPurpose,
    Enrolment,
    email_bound_link,
    enrol_totp_key,
    has_second_factor,
    register_action_purpose,
    send_action_link_email,
    spend_email_bound_link,
    totp_key_time_step,
)
from apps.core.audit import AuditContext, record
from apps.core.changeset import Change, ChangeSet
from apps.core.models import AuditLog
from apps.core.services import (
    allowed_vendor_countries,
    audit_changes,
    consume_rate_limit,
    flags_changed,
    run_mutation,
    save_audited,
)
from apps.core.throttling import ip_bucket

from . import engine
from .models import (
    ContractAcceptance,
    CrossVendorConsent,
    LegalFieldRequirement,
    Vendor,
    VendorClaim,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorMember,
)
from .vies import VatNumberValidator

logger = logging.getLogger(__name__)

STATUS_CHANGED = "vendor.status_changed"
CREATED = "vendor.created"
UPDATED = "vendor.updated"
DELETED = "vendor.deleted"
MODEL_LABEL = "vendors.vendor"

# Everything staff can see about a vendor's identity, tax position and contacts (SEC.4), the
# whole legal identity included (VEN.12).
VENDOR_FIELDS = (
    "legal_name",
    "display_name",
    "slug",
    "status",
    "country",
    *engine.LEGAL_FIELDS,
    "contact_email",
    "phone",
    "sales_mode_default",
    "enquiries_enabled",
    "legacy_wp_user_id",
)


def storefront_vendors() -> QuerySet[Vendor]:
    """Vendors whose storefront is public: the active ones (spec 04). A storefront is public data,
    so this read needs no actor; the CMS vendor cards and their chooser use it (CMS.2)."""
    return Vendor.objects.filter(status=Vendor.Status.ACTIVE)


def has_public_storefront(vendor: Vendor) -> bool:
    """The same rule as `storefront_vendors`, on a vendor already loaded."""
    return bool(vendor.status == Vendor.Status.ACTIVE)


# Fields encrypted at rest (SEC.3) are masked in audit rows, or the log would hold them in clear.
MASKED_FIELDS = {"iban": engine.mask_iban, "tax_code": engine.mask_tax_code}


def vendor_snapshot(vendor: Vendor) -> dict[str, Any]:
    """The audited fields of `vendor`, with the encrypted ones masked (SEC.3)."""
    snapshot = {name: getattr(vendor, name) for name in VENDOR_FIELDS}
    for name, mask in MASKED_FIELDS.items():
        snapshot[name] = mask(getattr(vendor, name))
    return snapshot


def save_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Validate, create or update `vendor`, auditing its data with before/after (keyed by public
    id, IBAN and tax code masked). `before` is read from the database with the row locked.

    Incomplete legal data is always accepted: completeness is checked when a payout is
    requested (`assert_payout_allowed`), never here. A value that is present must be valid:
    `full_clean` (field rules plus `engine.identity_errors`) runs for every adapter, also on
    dry-run, and raises `ValidationError`; it also stores identifiers compact. The status is
    not changed here: it moves only through `change_status`.
    """
    vendor.normalize()
    vendor.full_clean()
    key = str(vendor.public_id)
    with transaction.atomic():
        stored = (
            None
            if vendor._state.adding
            else Vendor.objects.select_for_update().filter(pk=vendor.pk).first()
        )
        if stored is not None:  # a stale instance never undoes a status change or a reopen
            vendor.status, vendor.onboarding_round = stored.status, stored.onboarding_round
        after = vendor_snapshot(vendor)
        changes = ChangeSet()
        if stored is None:
            changes.create(MODEL_LABEL, key, after)
        else:
            before = vendor_snapshot(stored)
            for name in MASKED_FIELDS:  # same mask, different value
                if getattr(stored, name) != getattr(vendor, name) and before[name] == after[name]:
                    after[name] += " (replaced)"
            changes.update(MODEL_LABEL, key, before, after)
        flags = ChangeSet() if stored is None else establishment_review(stored, vendor)
        result = ChangeSet([*changes.changes, *flags.changes])
        if dry_run:
            return result
        vendor.save()
        audit_changes(changes, ctx=ctx, action=UPDATED if stored else CREATED)
        audit_changes(flags, ctx=ctx, action=ESTABLISHMENT_FLAGGED)
        for flag in flags.changes:
            warning = establishment_warning(flag.after or {}, str(vendor.status))
            logger.warning("vendor %s: %s", vendor.public_id, warning)
    return result


def delete_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `vendor` with its translations and memberships; the vendor and each membership
    are audited (translations are storefront texts, not audited).

    Apps above `vendors` cannot be imported here (spec 01 §1.3): each one whose rows the
    deletion cascades to names a `hook(vendor, changes)` in
    `settings.VENDOR_DELETION_AUDIT_HOOKS`, which adds one delete per row (the API keys,
    CORE.14). The vendor row is locked first, so no row is added between the hooks and the
    deletion.
    """
    with transaction.atomic():
        if not dry_run:
            _lock_vendor(vendor)
        members = list(VendorMember.objects.select_related("vendor", "user").filter(vendor=vendor))
        changes = ChangeSet()
        changes.delete(MODEL_LABEL, str(vendor.public_id), vendor_snapshot(vendor))
        for member in members:
            changes.delete(MEMBER_MODEL, str(member.public_id), member_snapshot(member))
        for path in settings.VENDOR_DELETION_AUDIT_HOOKS:
            import_string(path)(vendor, changes)
        run_mutation(changes, vendor.delete, ctx=ctx, action=DELETED, dry_run=dry_run)
    return changes


def _run_closure_hooks(
    vendor: Vendor, changes: ChangeSet, *, ctx: AuditContext, dry_run: bool
) -> None:
    """Revoke, audited, what only an active vendor may hold (its API keys, CORE.14): each hook
    in `settings.VENDOR_CLOSURE_HOOKS` returns its changes, added to `changes`."""
    for path in settings.VENDOR_CLOSURE_HOOKS:
        changes.changes.extend(import_string(path)(vendor, ctx=ctx, dry_run=dry_run).changes)


def change_status(
    vendor: Vendor, target: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Move `vendor` to `target` if the state machine allows it; audit the change.

    The check runs against the stored status (row locked), never a possibly stale instance.
    Activating an onboarding vendor needs an establishment in an allowed country (CMP.13) and
    its onboarding checklist (VEN.5): the contract in force must be accepted. Closing a vendor
    revokes its API keys (`VENDOR_CLOSURE_HOOKS`, CORE.14), listed in the returned ChangeSet.
    Raises `engine.InvalidTransition` (`engine.EstablishmentNotAllowed` for the establishment,
    `engine.ActivationNotAllowed` for the checklist), also on dry-run. Reinstating a suspended
    vendor is a staff decision and is not gated. Every move to active records the
    establishment and whether it is allowed (`establishment_snapshot`) in the audit entry.
    """
    with transaction.atomic():
        current = (
            Vendor.objects.select_for_update()
            .only("status", "slug", "public_id", *ESTABLISHMENT_FIELDS)
            .get(pk=vendor.pk)
        )
        engine.check_transition(current.status, target)
        establishment: dict[str, Any] = {}
        if target == engine.ACTIVE:
            if current.status == engine.ONBOARDING:
                assert_establishment_allowed(current)
                engine.check_activation(onboarding_checklist(current))
            establishment = establishment_snapshot(current, allowed_vendor_countries())

        # Keyed by public_id: the slug is editable and can be reused by another vendor, so it
        # would not identify the vendor across its audit history. The slug is kept for reading.
        changes = ChangeSet()
        changes.update(
            MODEL_LABEL,
            str(current.public_id),
            before={"status": current.status, "slug": current.slug},
            after={"status": str(target), "slug": current.slug, **establishment},
        )

        def apply() -> None:
            current.status = target
            current.save(update_fields=["status", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=STATUS_CHANGED, dry_run=dry_run)
        if target == engine.CLOSED:
            _run_closure_hooks(current, changes, ctx=ctx, dry_run=dry_run)

    if not dry_run:
        vendor.status = target
    return changes


# --- Establishment gate (D-21, CMP.13)

# The identity fields `engine.vendor_establishment` reads.
ESTABLISHMENT_FIELDS = ("country", "registered_country", "establishment_country")


def vendor_establishment(vendor: Vendor) -> engine.Establishment:
    """Where `vendor` is established, from its VEN.12 registered office and establishment."""
    return engine.vendor_establishment(
        engine.LegalIdentity(**{name: getattr(vendor, name) for name in ESTABLISHMENT_FIELDS})
    )


def assert_establishment_allowed(vendor: Vendor) -> None:
    """Raise `engine.EstablishmentNotAllowed`, naming the countries, unless `vendor` is
    established in a country of `PlatformOperator.allowed_vendor_countries` now."""
    engine.check_establishment(vendor_establishment(vendor), allowed_vendor_countries())


ESTABLISHMENT_FLAGGED = "vendor.establishment_outside_allowed"

#: Vendors the activation gate already admitted: a later move out of the set is flagged.
ADMITTED_STATUSES = frozenset({engine.ACTIVE, engine.SUSPENDED})


def establishment_snapshot(vendor: Vendor, allowed: Collection[str]) -> dict[str, Any]:
    """Where `vendor` is established, and whether that is in the `allowed` countries."""
    place = vendor_establishment(vendor)
    return {
        "registered_office": place.registered_office,
        "permanent_establishment": place.permanent_establishment,
        "establishment_allowed": engine.establishment_error(place, allowed) is None,
    }


def establishment_review(stored: Vendor, vendor: Vendor) -> ChangeSet:
    """The staff flag for an identity change (spec 11 CMP.13): one change when `stored` is an
    admitted vendor and the edited `vendor` is established elsewhere, outside the allowed set.

    Nobody is suspended: staff decide, as when the owner shrinks the set.
    """
    flags = ChangeSet()
    if stored.status not in ADMITTED_STATUSES:
        return flags
    allowed = allowed_vendor_countries()
    before, after = establishment_snapshot(stored, allowed), establishment_snapshot(vendor, allowed)
    if not after["establishment_allowed"] and vendor_establishment(stored) != vendor_establishment(
        vendor
    ):
        flags.update(MODEL_LABEL, str(vendor.public_id), before, after)
    return flags


def establishment_flags(changes: ChangeSet) -> list[Change]:
    """The changes of `changes` that leave an admitted vendor outside the allowed set."""
    return [c for c in changes.changes if (c.after or {}).get("establishment_allowed") is False]


def establishment_warning(place: Mapping[str, Any], status: str) -> str:
    """The staff message for a vendor flagged by `establishment_review` or reinstated outside
    the allowed set; `place` is an `establishment_snapshot`."""
    establishment = place.get("permanent_establishment") or "none"
    return (
        "the vendor is established outside the allowed vendor countries "
        f"(registered office {place.get('registered_office')}, permanent establishment "
        f"{establishment}). It stays {status}: staff must review it."
    )


def report_vendors_outside_allowed() -> int:
    """Log a warning for each active vendor established outside the allowed set; return how
    many there are. Run every night (`vendors.check_establishments`)."""
    rows = active_vendors_outside(allowed_vendor_countries())
    for row in rows:
        logger.warning(
            "vendor %s: %s", row["vendor"], establishment_warning(row, str(engine.ACTIVE))
        )
    return len(rows)


def active_vendors_outside(countries: Collection[str]) -> list[dict[str, str]]:
    """The active vendors established outside `countries`, for staff to decide on.

    Named by `settings.VENDORS_OUTSIDE_COUNTRIES_HOOK`: `core.set_allowed_vendor_countries`
    lists them in its ChangeSet.
    """
    vendors = Vendor.objects.filter(status=engine.ACTIVE).only(
        "public_id", "display_name", *ESTABLISHMENT_FIELDS
    )
    rows = []
    for vendor in vendors.order_by("display_name", "id"):
        place = vendor_establishment(vendor)
        if engine.establishment_error(place, countries) is not None:
            rows.append(
                {
                    "vendor": str(vendor.public_id),
                    "name": vendor.display_name,
                    "registered_office": place.registered_office,
                    "permanent_establishment": place.permanent_establishment,
                }
            )
    return rows


def ensure_vendor_can_write(vendor: Vendor) -> str:
    """Re-read `vendor`'s status inside the caller's write transaction; raise
    `engine.VendorCannotWrite` unless the vendor is active (CORE.14). Returns the status.

    For writes made on behalf of a vendor's API key. The key's vendor status is checked when the
    request is authenticated; this closes the window between that check and the write. The row
    is read `FOR SHARE`: concurrent writes of the same vendor proceed, while `change_status`
    (`FOR UPDATE`) waits for them to commit, and a write that starts after a status change
    commits sees the new status. Must run inside `transaction.atomic()`, or the lock would end
    with the read.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("ensure_vendor_can_write must run inside the write transaction")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT status FROM {connection.ops.quote_name(Vendor._meta.db_table)} "  # noqa: S608
            "WHERE id = %s FOR SHARE",
            [vendor.pk],
        )
        row = cursor.fetchone()
    if row is None:
        raise Vendor.DoesNotExist(f"vendor {vendor.public_id} does not exist")
    status = str(row[0])
    engine.check_can_write(status)
    return status


# --- Memberships (CORE.2)

MEMBER_MODEL = "vendors.vendormember"
MEMBER_ADDED = "vendor.member_added"
MEMBER_ROLE_CHANGED = "vendor.member_role_changed"
MEMBER_REMOVED = "vendor.member_removed"


def member_snapshot(member: VendorMember) -> dict[str, Any]:
    """Public ids only: audit rows must stay readable after users or vendors change."""
    return {
        "vendor": str(member.vendor.public_id),
        "user": str(member.user.public_id),
        "role": member.role,
    }


def _lock_vendor(vendor: Vendor) -> Vendor:
    """Lock the vendor row: membership changes of one vendor run one at a time, so two
    concurrent removals cannot each see "another owner remains" and leave none."""
    return Vendor.objects.select_for_update().only("public_id").get(pk=vendor.pk)


def _roles(vendor: Vendor, *, excluding: VendorMember | None = None) -> list[str]:
    members = VendorMember.objects.filter(vendor=vendor)
    if excluding is not None:
        members = members.exclude(pk=excluding.pk)
    return list(members.values_list("role", flat=True))


def add_member(
    vendor: Vendor, user: User, role: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Make `user` a member of `vendor` with `role`; a vendor's first member must be an owner.

    Raises `engine.UnknownRole`, `engine.AlreadyMember` or `engine.NoOwnerLeft`, also on dry-run.
    """
    engine.check_role(role)
    with transaction.atomic():
        locked = _lock_vendor(vendor)
        if VendorMember.objects.filter(vendor=locked, user=user).exists():
            raise engine.AlreadyMember(f"{user} is already a member of vendor {locked.public_id}")
        engine.check_keeps_an_owner([*_roles(locked), role])

        member = VendorMember(vendor=locked, user=user, role=role)
        changes = ChangeSet()
        changes.create(MEMBER_MODEL, str(member.public_id), member_snapshot(member))
        run_mutation(changes, member.save, ctx=ctx, action=MEMBER_ADDED, dry_run=dry_run)
    return changes


def change_member_role(
    member: VendorMember, role: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Give `member` a new role; demoting the vendor's last owner is refused.

    The check runs against the stored roles (vendor locked), never a possibly stale instance.
    Raises `engine.UnknownRole` or `engine.NoOwnerLeft`, also on dry-run.
    """
    engine.check_role(role)
    with transaction.atomic():
        _lock_vendor(member.vendor)
        current = VendorMember.objects.select_related("vendor", "user").get(pk=member.pk)
        engine.check_keeps_an_owner([*_roles(current.vendor, excluding=current), role])

        before = member_snapshot(current)
        changes = ChangeSet()
        changes.update(MEMBER_MODEL, str(current.public_id), before, {**before, "role": role})

        def apply() -> None:
            current.role = role
            current.save(update_fields=["role", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=MEMBER_ROLE_CHANGED, dry_run=dry_run)

    if not dry_run:
        member.role = role
    return changes


def remove_member(member: VendorMember, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """End `member`'s membership; removing the vendor's last owner is refused.

    Raises `engine.NoOwnerLeft`, also on dry-run.
    """
    with transaction.atomic():
        _lock_vendor(member.vendor)
        current = VendorMember.objects.select_related("vendor", "user").get(pk=member.pk)
        engine.check_keeps_an_owner(_roles(current.vendor, excluding=current))

        changes = ChangeSet()
        changes.delete(MEMBER_MODEL, str(current.public_id), member_snapshot(current))

        def apply() -> None:
            current.delete()

        run_mutation(changes, apply, ctx=ctx, action=MEMBER_REMOVED, dry_run=dry_run)
    return changes


# --- Permissions (CORE.2)


def member_role(user: User, vendor: Vendor) -> str | None:
    """The user's role in `vendor`, or None when they are not a member."""
    return (
        VendorMember.objects.filter(user=user, vendor=vendor).values_list("role", flat=True).first()
    )


def has_vendor_permission(user: User, vendor: Vendor, action: str) -> bool:
    """Whether `user` may perform `action` on `vendor` as one of its members.

    Only membership counts: platform staff reach vendors through the staff admin, not this
    check. Inactive users get nothing. An unknown action raises `engine.UnknownAction`.
    """
    checked = engine.to_action(action)
    if not user.is_active:
        return False
    role = member_role(user, vendor)
    return role is not None and engine.is_allowed(role, checked)


def is_vendor_member(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Whether `user` is a member (owner or staff) of at least one vendor.

    Listed in `settings.MFA_REQUIRED_POLICIES`: everyone who reaches a store's management area
    must use MFA (CORE.4, owner decision Q-09), whatever their role.
    """
    if not user.is_authenticated:
        return False
    return VendorMember.objects.filter(user_id=user.pk).exists()


# --- Legal and e-invoicing identity (VEN.12)

VAT_CHECKED = "vendor.vat_checked"


def check_vat_number(
    vendor: Vendor, validator: VatNumberValidator, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Ask VIES (through `validator`) about the vendor's VAT number; store and audit the answer.

    Raises `ValueError` when the vendor has no VAT number or the validator answers something
    that is not a VIES result.
    """
    if not vendor.vat_number:
        raise ValueError(f"vendor {vendor.public_id} has no VAT number to check")
    _, number = engine.split_vat_number(vendor.vat_number, vendor.country)
    status = validator.check(engine.vat_prefix(vendor.country), number)
    if status not in engine.VIES_RESULTS:
        raise ValueError(f"unexpected VIES result {status!r}")
    checked_at = timezone.now()

    def state(vies_status: str, at: Any) -> dict[str, Any]:
        return {"vat_number": vendor.vat_number, "vies_status": vies_status, "vies_checked_at": at}

    changes = ChangeSet()
    changes.update(
        MODEL_LABEL,
        str(vendor.public_id),
        state(vendor.vies_status, vendor.vies_checked_at),
        state(status, checked_at),
    )

    def apply() -> None:
        Vendor.objects.filter(pk=vendor.pk).update(
            vies_status=status, vies_checked_at=checked_at, updated_at=checked_at
        )

    run_mutation(changes, apply, ctx=ctx, action=VAT_CHECKED, dry_run=dry_run)
    if not dry_run:
        vendor.vies_status = status
        vendor.vies_checked_at = checked_at
    return changes


def requirement_rules(purpose: str = engine.PAYOUT) -> list[engine.Requirement]:
    """The active `LegalFieldRequirement` rows for `purpose`, as pure rules."""
    rows = LegalFieldRequirement.objects.filter(active=True, purpose=purpose)
    return [row.as_rule() for row in rows]


def legal_profile_completeness(vendor: Vendor, purpose: str = engine.PAYOUT) -> engine.Completeness:
    """Which fields required for `purpose` the vendor is missing, or holds invalid values for."""
    return engine.completeness(vendor.legal_identity(), requirement_rules(purpose), purpose=purpose)


def assert_payout_allowed(vendor: Vendor) -> None:
    """Guard for payout requests (VEN.6, VEN.7): raise `engine.PayoutNotAllowed` listing the
    required legal fields that are missing or invalid.

    Checks the stored vendor, not unsaved changes on the instance.
    """
    stored = Vendor.objects.get(pk=vendor.pk)
    result = legal_profile_completeness(stored, engine.PAYOUT)
    if not result.complete:
        raise engine.PayoutNotAllowed(str(stored), result)


REQUIREMENT_MODEL = "vendors.legalfieldrequirement"
REQUIREMENT_SAVED = "vendor.legal_requirement_saved"
REQUIREMENT_DELETED = "vendor.legal_requirement_deleted"
REQUIREMENT_FIELDS = (
    "country",
    "region",
    "legal_form",
    "field_name",
    "purpose",
    "active",
    "note",
)


def requirement_snapshot(requirement: LegalFieldRequirement) -> dict[str, Any]:
    return {name: getattr(requirement, name) for name in REQUIREMENT_FIELDS}


def save_requirement(
    requirement: LegalFieldRequirement, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change a requirement rule (validated, audited). Raises `ValidationError`."""
    requirement.full_clean()
    with transaction.atomic():
        stored = (
            LegalFieldRequirement.objects.select_for_update().filter(pk=requirement.pk).first()
            if requirement.pk
            else None
        )
        changes = ChangeSet()
        key = str(requirement.public_id)
        if stored is None:
            changes.create(REQUIREMENT_MODEL, key, requirement_snapshot(requirement))
        else:
            changes.update(
                REQUIREMENT_MODEL,
                key,
                requirement_snapshot(stored),
                requirement_snapshot(requirement),
            )
        return run_mutation(
            changes, requirement.save, ctx=ctx, action=REQUIREMENT_SAVED, dry_run=dry_run
        )


def delete_requirement(
    requirement: LegalFieldRequirement, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Delete a requirement rule (audited). Deactivating it keeps the history visible."""
    changes = ChangeSet()
    changes.delete(REQUIREMENT_MODEL, str(requirement.public_id), requirement_snapshot(requirement))

    def apply() -> None:
        requirement.delete()

    return run_mutation(changes, apply, ctx=ctx, action=REQUIREMENT_DELETED, dry_run=dry_run)


# --- Onboarding (VEN.5): application, versioned contract, acceptance, checklist
#
# Application → staff review (`change_status`: onboarding, or closed for a rejection) →
# onboarding (contract acceptance; DAC7 and payout data listed, gated at payout time) →
# activation by staff (`change_status`, gated by `onboarding_checklist`).

APPLICATION_SUBMITTED = "vendor.application_submitted"
CONTRACT_MODEL = "vendors.vendorcontract"
CONTRACT_SAVED = "vendor.contract_saved"
ACCEPTANCE_MODEL = "vendors.contractacceptance"
CONTRACT_ACCEPTED = "vendor.contract_accepted"


def submit_application(
    vendor: Vendor, applicant: User, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create `vendor` (new, unsaved) as an applicant with `applicant` as its owner; audited.

    The application data is validated like any vendor profile (`full_clean`); legal data may be
    incomplete. The VIES result and the legacy user id are reset: only the VIES check and the
    legacy migration set them. Being an owner, the applicant must enrol a second factor (CORE.4).
    Raises `ValidationError`, or `ValueError` for a saved vendor or an inactive user, also on
    dry-run.
    """
    if not vendor._state.adding:
        raise ValueError("submit_application takes a new vendor, not a saved one")
    if not applicant.is_active:
        raise ValueError("only an active user can apply as a vendor")
    vendor.status = engine.APPLICANT
    # Set by the VIES check and the legacy migration, never by an applicant.
    vendor.vies_status = Vendor.ViesStatus.UNCHECKED
    vendor.vies_checked_at = None
    vendor.legacy_wp_user_id = None
    vendor.normalize()
    vendor.full_clean()
    member = VendorMember(vendor=vendor, user=applicant, role=engine.OWNER)
    changes = ChangeSet()
    changes.create(MODEL_LABEL, str(vendor.public_id), vendor_snapshot(vendor))
    changes.create(MEMBER_MODEL, str(member.public_id), member_snapshot(member))

    def apply() -> None:
        vendor.save()
        member.vendor = vendor
        member.save()

    return run_mutation(changes, apply, ctx=ctx, action=APPLICATION_SUBMITTED, dry_run=dry_run)


def current_contract(at: dt.datetime | None = None) -> VendorContract | None:
    """The contract version in force at `at` (default: now): the latest one already started."""
    started = VendorContract.objects.filter(valid_from__lte=at or timezone.now())
    return started.order_by("-version").first()


def contract_snapshot(contract: VendorContract) -> dict[str, Any]:
    return {"version": contract.version, "valid_from": contract.valid_from, "note": contract.note}


def check_contract_start(contract: VendorContract) -> None:
    """Versions start in order: after the previous version and before the next one.

    A new contract (no version yet) is checked against the latest one. Raises `ValidationError`
    on `valid_from`.
    """
    others = VendorContract.objects.exclude(pk=contract.pk)
    following = None
    if contract.version is None:
        previous = others.order_by("-version").first()
    else:
        previous = others.filter(version__lt=contract.version).order_by("-version").first()
        following = others.filter(version__gt=contract.version).order_by("version").first()
    error = engine.contract_start_error(
        contract.valid_from, previous.valid_from if previous else None
    )
    if error is None and following is not None and contract.valid_from >= following.valid_from:
        error = (
            f"Must be before {following.valid_from.isoformat()}, "
            f"the start of version {following.version}."
        )
    if error:
        raise ValidationError({"valid_from": error})


def _lock_new_versions() -> None:
    """Serialize the creation of contract versions until the transaction ends.

    Locking the latest row is not enough: there is none before the first version, and a second
    creator waiting on it would still not see the row the first one inserted. Both would take
    the same number and one would end in an IntegrityError. This lock mode conflicts with
    itself and with writes, not with the row locks of an acceptance.
    """
    table = connection.ops.quote_name(VendorContract._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")


def save_contract(
    contract: VendorContract, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create the next contract version, or edit one nobody has accepted yet; audited.

    New versions are numbered after the latest one and must start after it. Raises
    `ValidationError`, or `engine.ContractLocked` for an accepted version, also on dry-run.
    """
    contract.full_clean(exclude={"version"})
    key = str(contract.public_id)
    changes = ChangeSet()
    with transaction.atomic():
        if contract._state.adding:
            _lock_new_versions()
            latest = VendorContract.objects.order_by("-version").first()
            check_contract_start(contract)
            contract.version = (latest.version if latest else 0) + 1
            changes.create(CONTRACT_MODEL, key, contract_snapshot(contract))
        else:
            stored = VendorContract.objects.select_for_update().get(pk=contract.pk)
            if stored.is_accepted:
                raise engine.ContractLocked(f"{stored} has been accepted; publish a new version")
            check_contract_start(contract)
            changes.update(
                CONTRACT_MODEL, key, contract_snapshot(stored), contract_snapshot(contract)
            )
        if dry_run:
            return changes
        contract.save()
        audit_changes(changes, ctx=ctx, action=CONTRACT_SAVED)
    return changes


def acceptance_snapshot(acceptance: ContractAcceptance) -> dict[str, Any]:
    return {
        "vendor": str(acceptance.vendor.public_id),
        "contract": str(acceptance.contract.public_id),
        "contract_version": acceptance.contract.version,
        "user": str(acceptance.user.public_id),
        "language": acceptance.language,
        "text_hash": acceptance.text_hash,
        "accepted_at": acceptance.accepted_at,
        "ip": acceptance.ip,
        "onboarding_round": acceptance.onboarding_round,
    }


@dataclasses.dataclass(frozen=True)
class ContractOffer:
    """The contract an onboarding page shows: the version in force, the text in the visitor's
    language (or English) and its hash. The page hands the version, `text.language` and
    `text_hash` back to `accept_contract`, so the acceptance is tied to what was shown."""

    contract: VendorContract
    text: VendorContractTranslation
    text_hash: str


def contract_offer(language: str) -> ContractOffer:
    """The contract version in force with its published text in `language`, or the English one.

    Raises `engine.NoContract` when no version is in force or it has no published text.
    """
    contract = current_contract()
    if contract is None:
        raise engine.NoContract("no vendor contract is in force")
    text = contract.translation(language).translation
    if text is None:
        raise engine.NoContract(f"{contract} has no published text")
    return ContractOffer(contract=contract, text=text, text_hash=text.text_hash())


def accept_contract(
    vendor: Vendor,
    user: User,
    *,
    contract: VendorContract,
    language: str,
    text_hash: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Record that `user`, an owner, accepts for `vendor` the contract text they were shown.

    `contract`, `language` and `text_hash` describe what the page showed (`contract_offer`): the
    version, the language of its text and the hash of that text. They must still be the version
    in force and its published text in that language, else `engine.ContractChanged` (show the
    page again). The version row is locked for the whole check, like every write to the version
    or its texts, so a concurrent edit either finishes first (and the hash no longer matches) or
    waits and is refused (`engine.ContractLocked`).

    The acceptance stores the language and hash, and the IP of `ctx` (`audit_context` takes it
    from the request through the trusted proxy, `apps.core.http.client_ip`). Accepting a version
    again in the same onboarding round changes nothing; after a reopen (VEN.21) it is accepted
    again. Raises `engine.ContractNotAcceptable` (vendor status, not an owner),
    `engine.NoContract` or `engine.ContractChanged`, also on dry-run.
    """
    with transaction.atomic():
        locked = (
            Vendor.objects.select_for_update()
            .only("status", "public_id", "onboarding_round")
            .get(pk=vendor.pk)
        )
        role = member_role(user, locked) if user.is_active else None
        engine.check_can_accept_contract(locked.status, role)
        shown = VendorContract.objects.select_for_update().get(pk=contract.pk)
        in_force = current_contract()
        if in_force is None:
            raise engine.NoContract("no vendor contract is in force")
        if in_force.pk != shown.pk:
            raise engine.ContractChanged(
                f"{shown} is no longer in force: {in_force} replaced it; show it to accept it"
            )
        text = shown.translation(language).translation
        if text is None or text.language != language:
            raise engine.ContractChanged(
                f"the {language} text of {shown} is no longer published; show the text again"
            )
        if text.text_hash() != text_hash:
            raise engine.ContractChanged(
                f"the {language} text of {shown} changed after it was shown; show it again"
            )
        round_ = locked.onboarding_round
        if ContractAcceptance.objects.filter(
            vendor=locked, contract=shown, onboarding_round=round_
        ).exists():
            return ChangeSet()
        acceptance = ContractAcceptance(
            vendor=locked,
            contract=shown,
            user=user,
            language=text.language,
            text_hash=text_hash,
            accepted_at=timezone.now(),
            ip=ctx.ip,
            onboarding_round=round_,
        )
        changes = ChangeSet()
        changes.create(ACCEPTANCE_MODEL, str(acceptance.public_id), acceptance_snapshot(acceptance))
        return run_mutation(
            changes, acceptance.save, ctx=ctx, action=CONTRACT_ACCEPTED, dry_run=dry_run
        )


def onboarding_checklist(vendor: Vendor) -> engine.OnboardingChecklist:
    """The stored vendor's onboarding checklist: contract in force accepted (blocks activation),
    DAC7 dataset and payout data (listed only: VEN.12 gates payouts with them). Only an
    acceptance of the vendor's current onboarding round counts (VEN.21)."""
    stored = Vendor.objects.get(pk=vendor.pk)
    contract = current_contract()
    accepted = (
        contract is not None
        and ContractAcceptance.objects.filter(
            vendor=stored, contract=contract, onboarding_round=stored.onboarding_round
        ).exists()
    )
    return engine.onboarding_checklist(
        contract_version=contract.version if contract is not None and accepted else None,
        current_contract_version=contract.version if contract is not None else None,
        dac7_missing=engine.dac7_missing(stored.legal_identity()),
        payout=legal_profile_completeness(stored),
    )


# --- Reopening a closed vendor (VEN.21, owner decision Q-17)

REOPENED = "vendor.reopened"
# Checked here, not only in the admin, so every adapter (admin, API, MCP, shell) is staff-only.
REOPEN_PERMISSION = "vendors.change_vendor"


def can_reopen(actor: User | AnonymousUser) -> bool:
    """Active platform staff with the vendor change permission; never a vendor member as such."""
    return bool(actor.is_active and actor.is_staff and actor.has_perm(REOPEN_PERMISSION))


def reopen_vendor(
    vendor: Vendor,
    *,
    actor: User | AnonymousUser,
    reason: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Staff move a closed `vendor` back to onboarding, saying why; audited with the reason.

    The vendor's onboarding round goes up, so an acceptance from before the closure no longer
    counts: an owner accepts the contract in force again, and activation re-runs the checklist
    and gates through `change_status`. The kept data (owner, ledger, contracts) are reused.
    No API key comes back: closing revoked them, and a key a closure from before CORE.14 left
    active is revoked here (`VENDOR_CLOSURE_HOOKS`); an owner issues new keys once the vendor is
    active again. Checked against the stored vendor (row locked). Raises `PermissionDenied` (see
    `can_reopen`), `engine.ReasonRequired` (blank reason), `engine.InvalidTransition` (not
    closed) or `engine.ReopenNotAllowed` (no active owner), also on dry-run.
    """
    if not can_reopen(actor):
        raise PermissionDenied("Only platform staff can reopen a closed vendor.")
    reason = reason.strip()
    with transaction.atomic():
        current = (
            Vendor.objects.select_for_update()
            .only("status", "slug", "public_id", "onboarding_round")
            .get(pk=vendor.pk)
        )
        engine.check_reopen(
            current.status,
            reason=reason,
            has_active_owner=VendorMember.objects.filter(
                vendor=current, role=engine.OWNER, user__is_active=True
            ).exists(),
        )
        round_ = current.onboarding_round + 1
        changes = ChangeSet()
        changes.update(
            MODEL_LABEL,
            str(current.public_id),
            before={
                "status": current.status,
                "slug": current.slug,
                "onboarding_round": current.onboarding_round,
            },
            after={
                "status": engine.ONBOARDING,
                "slug": current.slug,
                "onboarding_round": round_,
                "reason": reason,
            },
        )

        def apply() -> None:
            current.status = engine.ONBOARDING
            current.onboarding_round = round_
            current.save(update_fields=["status", "onboarding_round", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=REOPENED, dry_run=dry_run)
        _run_closure_hooks(current, changes, ctx=ctx, dry_run=dry_run)

    if not dry_run:
        vendor.status, vendor.onboarding_round = engine.ONBOARDING, round_
    return changes


# --- Per-vendor feature flag overrides (ARC.8, docs/dev/feature-flags.md)

VENDOR_FLAG_MODEL = "vendors.vendorfeatureflag"
VENDOR_FLAG_SAVED = "vendor.feature_flag_saved"
VENDOR_FLAG_DELETED = "vendor.feature_flag_deleted"


def vendor_flag_snapshot(override: VendorFeatureFlag) -> dict[str, Any]:
    return {
        "vendor": str(override.vendor.public_id),
        "flag": override.flag.name,
        "enabled": override.enabled,
        "note": override.note,
    }


def save_vendor_flag(
    override: VendorFeatureFlag, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change one vendor's override of a registered flag (validated, audited).

    Raises `ValidationError` for an unregistered flag or a second override of the same flag.
    """
    override.full_clean()
    changeset = save_audited(
        override,
        vendor_flag_snapshot,
        key=str(override.public_id),
        ctx=ctx,
        created=VENDOR_FLAG_SAVED,
        updated=VENDOR_FLAG_SAVED,
        dry_run=dry_run,
    )
    if not dry_run:
        flags_changed()
    return changeset


def delete_vendor_flag(
    override: VendorFeatureFlag, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Remove an override (audited): the vendor follows the platform value again."""
    changes = ChangeSet()
    changes.delete(VENDOR_FLAG_MODEL, str(override.public_id), vendor_flag_snapshot(override))

    def apply() -> None:
        override.delete()
        flags_changed()

    return run_mutation(changes, apply, ctx=ctx, action=VENDOR_FLAG_DELETED, dry_run=dry_run)


# --- Cross-vendor consent (CAT.14, owner decision Q-32): a versioned, audited vendor setting.

CONSENT_MODEL = "vendors.crossvendorconsent"
CONSENT_SET = "vendors.cross_vendor_consent.set"


def consent_snapshot(consent: CrossVendorConsent) -> dict[str, Any]:
    return {
        "vendor": str(consent.vendor.public_id),
        "in_suggestions": consent.in_suggestions,
        "in_bundles": consent.in_bundles,
        "valid_from": consent.valid_from,
        "set_by": consent.set_by,
    }


def current_cross_vendor_consent(
    vendor: Vendor, at: dt.datetime | None = None
) -> CrossVendorConsent | None:
    """The version in force at `at` (default: now): the latest one started by then."""
    at = at or timezone.now()
    return (
        CrossVendorConsent.objects.filter(vendor=vendor, valid_from__lte=at)
        .order_by("-valid_from")
        .first()
    )


def in_suggestions_allowed(vendor: Vendor, at: dt.datetime | None = None) -> bool:
    """Whether other vendors may suggest this vendor's products; no version: opted out."""
    consent = current_cross_vendor_consent(vendor, at)
    return consent is not None and consent.in_suggestions


def vendors_in_suggestions(at: dt.datetime | None = None) -> QuerySet[Vendor]:
    """The vendors whose version in force at `at` (default: now) allows other vendors'
    suggestions; usable as a subquery (`vendor__in=`)."""
    at = at or timezone.now()
    in_force = (
        CrossVendorConsent.objects.filter(vendor=OuterRef("pk"), valid_from__lte=at)
        .order_by("-valid_from")
        .values("in_suggestions")[:1]
    )
    return Vendor.objects.annotate(in_suggestions_now=Subquery(in_force)).filter(
        in_suggestions_now=True
    )


def set_cross_vendor_consent(
    vendor: Vendor,
    *,
    in_suggestions: bool | None = None,
    in_bundles: bool | None = None,
    valid_from: dt.datetime | None = None,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Record a new consent version from `valid_from` (default: now); None keeps a flag as in
    the latest version (opted out if there is none). Versions are never edited, and a new one
    starts after the latest, so the history of what was allowed when stays explainable."""
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
        latest = CrossVendorConsent.objects.filter(vendor=locked).order_by("-valid_from").first()
        start = valid_from or timezone.now()
        if latest is not None and start <= latest.valid_from:
            raise ValidationError({"valid_from": "A new version must start after the latest one."})
        consent = CrossVendorConsent(
            vendor=locked,
            in_suggestions=(
                in_suggestions
                if in_suggestions is not None
                else bool(latest and latest.in_suggestions)
            ),
            in_bundles=in_bundles if in_bundles is not None else bool(latest and latest.in_bundles),
            valid_from=start,
            set_by=ctx.actor_label,
        )
        changes = ChangeSet()
        changes.create(
            CONSENT_MODEL,
            str(consent.public_id),
            consent_snapshot(consent),
            supersedes=None if latest is None else consent_snapshot(latest),
        )
        return run_mutation(changes, consent.save, ctx=ctx, action=CONSENT_SET, dry_run=dry_run)


# --- Claiming an imported vendor (VEN.24, owner decision MQ-1, spec 04 "Flows")
#
# Staff send a link (an email-bound CORE.15 purpose) that goes only to the vendor's legal email
# of record, `Vendor.contact_email`. The claimant confirms the stored VAT number or TIN (wrong
# answers are counted, audited and lock the claim), then sets a password and enrols an
# authenticator app: the account is created and the claim submitted in one transaction, which
# spends the link. The claimant gets no membership until staff approve the claim, so before
# approval they cannot change the legal identity, the tax profile or the payout data.
# Regenerating the link raises the claim version: the old link is refused.

CLAIM_PURPOSE = "vendors.claim"
CLAIM_EMAIL_TEMPLATE = "vendors.claim_link"
CLAIM_MODEL = "vendors.vendorclaim"
CLAIM_LINK_SENT = "vendor.claim_link_sent"
CLAIM_LINK_REGENERATED = "vendor.claim_link_regenerated"
CLAIM_LINK_INVALIDATED = "vendor.claim_link_invalidated"
CLAIM_IDENTITY_CONFIRMED = "vendor.claim_identity_confirmed"
CLAIM_IDENTITY_REFUSED = "vendor.claim_identity_refused"
CLAIM_LOCKED = "vendor.claim_locked"
CLAIM_SUBMITTED = "vendor.claim_submitted"
CLAIM_APPROVED = "vendor.claim_approved"
CLAIM_REJECTED = "vendor.claim_rejected"
CLAIM_RATE_WINDOW = dt.timedelta(minutes=15)


class ClaimCodeInvalid(engine.ClaimError):
    """The authenticator-app code does not match the key shown on the claim page."""


class ClaimAccountExists(engine.ClaimError):
    """An account already uses the legal email of record: the claim never takes it over."""


def claim_snapshot(claim: VendorClaim) -> dict[str, Any]:
    """Public ids and counters only: never the typed VAT number or TIN, never the link."""
    return {
        "vendor": str(claim.vendor.public_id),
        "status": claim.status,
        "version": claim.version,
        "channel": claim.channel,
        "failed_attempts": claim.failed_attempts,
        "claimant": str(claim.claimant.public_id) if claim.claimant else None,
    }


def _open_claim(object_key: str) -> VendorClaim | None:
    try:
        return (
            VendorClaim.objects.select_related("vendor")
            .filter(vendor__public_id=object_key, status=VendorClaim.Status.OPEN)
            .first()
        )
    except ValidationError:  # not a UUID: no vendor
        return None


def _claim_version(object_key: str) -> str | None:
    """The version links of an open claim are bound to; None once the claim is not open."""
    claim = _open_claim(object_key)
    return None if claim is None else str(claim.version)


def claim_email(vendor: Vendor) -> str:
    """Where the vendor's claim link goes: its legal email of record, or the PEC address when
    staff picked it for the current link. Claim links are bound to it, and the claimant's
    account uses it."""
    claim = VendorClaim.objects.filter(vendor=vendor).only("channel").first()
    channel = claim.channel if claim else engine.CLAIM_CHANNEL_EMAIL
    return engine.claim_address(channel, contact_email=vendor.contact_email, pec=vendor.pec)


def _claim_email_of(object_key: str) -> str | None:
    """The address claim links of the vendor `object_key` are bound to (`claim_email`)."""
    try:
        vendor = Vendor.objects.filter(public_id=object_key).only("contact_email", "pec").first()
    except ValidationError:
        return None
    return claim_email(vendor) if vendor else None


def claim_path(object_key: str) -> str:
    return reverse("vendor_claim:claim", args=[object_key])


def register_claim_purpose() -> None:
    """Register the email-bound claim link with the CORE.15 action links (AppConfig.ready)."""
    register_action_purpose(
        ActionPurpose(
            name=CLAIM_PURPOSE,
            version_of=_claim_version,
            url_of=claim_path,
            email_of=_claim_email_of,
        )
    )


def claim_link(session: SessionBase, object_key: str) -> ActionLinkCheck:
    """The claim link opened in this session for the vendor `object_key`, checked again now."""
    return email_bound_link(session, CLAIM_PURPOSE, object_key)


def claim_retry_after(ip: str | None) -> dt.timedelta:
    """Count one claim-page POST from `ip`; zero when allowed, else the wait (Retry-After)."""
    return consume_rate_limit(
        f"vendor-claim:ip:{ip_bucket(ip) or 'unknown'}",
        limit=settings.VENDOR_CLAIM_RATE_LIMIT,
        window=CLAIM_RATE_WINDOW,
    )


def _reject_reference(claim: VendorClaim) -> dict[str, Any]:
    """What a new claim after a reject points back to: the rejected claim, when it was
    rejected and the audit entry of the reject."""
    entry = (
        AuditLog.objects.filter(action=CLAIM_REJECTED, target_key=str(claim.public_id))
        .order_by("-id")
        .first()
    )
    return {
        "claim": str(claim.public_id),
        "rejected_at": claim.decided_at.isoformat() if claim.decided_at else None,
        "audit_entry": str(entry.public_id) if entry else None,
    }


def send_claim_link(
    vendor: Vendor,
    *,
    ctx: AuditContext,
    channel: str = engine.CLAIM_CHANNEL_EMAIL,
    dry_run: bool = False,
) -> ChangeSet:
    """Send the claim link of `vendor` to its legal email of record, or a new one (resend /
    regenerate) that invalidates the link sent before; every step audited.

    `channel` is where it goes: the legal email of record, or the PEC address on file when
    staff pick it instead; the audit entry names the channel and the masked address. After a
    reject only staff (`ctx.source` admin, behind step-up) start a new claim, and its audit
    entry references the reject.

    Raises `engine.ClaimError` when the vendor already has an account holder, has no legal
    email (or no PEC for the PEC channel) or no VAT number or TIN to confirm, a claim waits for
    approval or was approved, or a rejected claim is restarted by anyone but staff.
    """
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
        if VendorMember.objects.filter(vendor=locked).exists():
            raise engine.ClaimError(f"{locked} already has an account holder")
        if not locked.contact_email.strip():
            raise engine.ClaimError(f"{locked} has no legal email of record")
        engine.check_claim_channel(channel, contact_email=locked.contact_email, pec=locked.pec)
        if not (locked.vat_number.strip() or locked.tax_code.strip()):
            raise engine.ClaimError(f"{locked} has no VAT number or TIN to confirm")
        claim = VendorClaim.objects.select_for_update().filter(vendor=locked).first()
        engine.check_claim_link_can_be_sent(
            claim.status if claim else None, by_staff=ctx.source == AuditLog.Source.ADMIN
        )
        destination: dict[str, Any] = {
            "address": engine.mask_email(
                engine.claim_address(channel, contact_email=locked.contact_email, pec=locked.pec)
            )
        }

        changes = ChangeSet()
        audits: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []
        if claim is None:
            claim = VendorClaim(vendor=locked, channel=channel)
            after = claim_snapshot(claim)
            changes.create(CLAIM_MODEL, str(claim.public_id), after)
            audits.append((CLAIM_LINK_SENT, None, {**after, **destination}))
        else:
            before = claim_snapshot(claim)
            if claim.status == VendorClaim.Status.REJECTED:
                destination["after_reject"] = _reject_reference(claim)
            claim.version += 1
            claim.channel = channel
            claim.status = VendorClaim.Status.OPEN
            claim.failed_attempts = 0
            claim.confirmed_link = ""
            claim.claimant = None
            claim.submitted_at = None
            claim.decided_at = None
            after = claim_snapshot(claim)
            changes.update(CLAIM_MODEL, str(claim.public_id), before, after)
            invalidated = _invalidated(locked, before["version"], "regenerated")
            audits.append((CLAIM_LINK_INVALIDATED, before, invalidated))
            audits.append((CLAIM_LINK_REGENERATED, before, {**after, **destination}))
        if dry_run:
            return changes

        claim.link_sent_at = timezone.now()
        claim.save()
        for action, before_snapshot, after_snapshot in audits:
            record(ctx, action, CLAIM_MODEL, str(claim.public_id), before_snapshot, after_snapshot)
        send_action_link_email(
            purpose=CLAIM_PURPOSE,
            object_key=str(locked.public_id),
            version=claim.version,
            template_key=CLAIM_EMAIL_TEMPLATE,
            context={"vendor_name": locked.legal_name},
            expires_in=settings.VENDOR_CLAIM_LINK_MAX_AGE,
            ctx=ctx,
        )
    return changes


def _invalidated(vendor: Vendor, version: int, reason: str) -> dict[str, Any]:
    """The audit `after` of `CLAIM_LINK_INVALIDATED`: links of `version` no longer work."""
    return {"vendor": str(vendor.public_id), "version": version, "reason": reason}


def claim_link_digest(link: ActionLinkCheck) -> str:
    """SHA-256 of the link's nonce: names the link without the token ("" without claims)."""
    return accounts_engine.action_nonce_digest(link.claims.nonce) if link.claims else ""


def _lock_open_claim(link: ActionLinkCheck) -> VendorClaim:
    """The claim a valid, opened link belongs to, locked; the link must still be current."""
    claims = link.claims
    if link.refusal is not None or claims is None or claims.purpose != CLAIM_PURPOSE:
        raise engine.ClaimError("the claim link is not valid")
    claim = (
        VendorClaim.objects.select_for_update(of=("self",))
        .select_related("vendor")
        .filter(vendor__public_id=claims.object_key)
        .first()
    )
    if (
        claim is None
        or claim.status != VendorClaim.Status.OPEN
        or str(claim.version) != claims.version
    ):
        raise engine.ClaimError("the claim link is not valid")
    return claim


def confirm_claim_identity(link: ActionLinkCheck, typed: str, *, ctx: AuditContext) -> bool:
    """Check the VAT number or TIN the claimant typed against the stored ones.

    A match records that this link confirmed the identity (the account step needs it). A
    mismatch is counted and audited (never the typed value); the claim locks after
    `settings.VENDOR_CLAIM_MAX_FAILURES`. Raises `engine.ClaimError` for a link that is not
    valid now (expired, regenerated, locked or used). A throttle counter: no dry-run.
    """
    with transaction.atomic():
        claim = _lock_open_claim(link)
        vendor = claim.vendor
        before = claim_snapshot(claim)
        key = str(claim.public_id)
        if engine.claim_identity_matches(
            typed, vat_number=vendor.vat_number, tax_code=vendor.tax_code, country=vendor.country
        ):
            claim.confirmed_link = claim_link_digest(link)
            claim.save(update_fields=["confirmed_link", "updated_at"])
            record(ctx, CLAIM_IDENTITY_CONFIRMED, CLAIM_MODEL, key, None, claim_snapshot(claim))
            return True
        claim.failed_attempts += 1
        locks = engine.claim_locks_after(
            claim.failed_attempts, max_failures=settings.VENDOR_CLAIM_MAX_FAILURES
        )
        if locks:
            claim.status = VendorClaim.Status.LOCKED
            claim.confirmed_link = ""
        claim.save(update_fields=["failed_attempts", "status", "confirmed_link", "updated_at"])
        record(ctx, CLAIM_IDENTITY_REFUSED, CLAIM_MODEL, key, before, claim_snapshot(claim))
        if locks:
            record(ctx, CLAIM_LOCKED, CLAIM_MODEL, key, before, claim_snapshot(claim))
    return False


def identity_confirmed(link: ActionLinkCheck) -> bool:
    """Whether this (valid) link already confirmed the VAT number or TIN of its open claim."""
    claims = link.claims
    if link.refusal is not None or claims is None:
        return False
    claim = _open_claim(claims.object_key)
    return (
        claim is not None
        and str(claim.version) == claims.version
        and bool(claim.confirmed_link)
        and claim.confirmed_link == accounts_engine.action_nonce_digest(claims.nonce)
    )


@dataclasses.dataclass(frozen=True)
class SubmittedClaim:
    claim: VendorClaim
    claimant: User
    enrolment: Enrolment


def submit_claim(
    link: ActionLinkCheck,
    *,
    full_name: str,
    password: str,
    totp_key: str,
    totp_code: str,
    language: str,
    ctx: AuditContext,
) -> SubmittedClaim:
    """Create the claimant's account with its password and authenticator app, spend the link
    and submit the claim for staff approval, all or nothing.

    The account's email is the address the link went to (`claim_email`: the legal email of
    record, or the PEC staff picked), whose control the link proved. The
    claimant gets no membership: staff approve first (`approve_claim`). Raises
    `engine.ClaimError` (link not valid, identity not confirmed with this link),
    `ClaimCodeInvalid`, `ClaimAccountExists`, or `ValidationError` for a weak password. Spends
    a one-time code and the link: no dry-run.
    """
    with transaction.atomic():
        claim = _lock_open_claim(link)
        nonce_digest = claim_link_digest(link)
        if not claim.confirmed_link or claim.confirmed_link != nonce_digest:
            raise engine.ClaimError("confirm the VAT number or TIN first")
        email = normalize_email_address(claim_email(claim.vendor))
        if User.objects.filter(email=email).exists():
            raise ClaimAccountExists("an account already uses this email address")
        user = User(email=email, full_name=full_name.strip(), preferred_language=language)
        validate_password(password, user)
        time_step = totp_key_time_step(totp_key, totp_code)
        if time_step is None:
            raise ClaimCodeInvalid("invalid authenticator code")

        user.set_password(password)
        user.save()
        try:
            spend_email_bound_link(link, user, ctx=ctx)
        except ActionLinkSpent as exc:
            raise engine.ClaimError("the claim link was already used") from exc
        enrolment = enrol_totp_key(user, totp_key, time_step=time_step, ctx=ctx)
        before = claim_snapshot(claim)
        claim.status = VendorClaim.Status.SUBMITTED
        claim.claimant = user
        claim.submitted_at = timezone.now()
        claim.confirmed_link = ""
        claim.save()
        record(
            ctx, CLAIM_SUBMITTED, CLAIM_MODEL, str(claim.public_id), before, claim_snapshot(claim)
        )
    return SubmittedClaim(claim, user, enrolment)


def claim_account(vendor: Vendor) -> User | None:
    """The account that already uses the claim address of `vendor` (`claim_email`), if any."""
    email = normalize_email_address(claim_email(vendor))
    return User.objects.filter(email=email).first() if email else None


def submit_claim_as_account(link: ActionLinkCheck, user: User, *, ctx: AuditContext) -> VendorClaim:
    """Submit the claim for an existing account with the legal email of record (a returning
    claimant, or one who already claimed another vendor with the same legal email).

    The caller has just checked the account's own password and second factor; the link only
    proved control of the mailbox and never signs anyone in. Nothing on the account changes.
    Raises `engine.ClaimError` (link not valid, identity not confirmed with this link, account
    not the legal email's, inactive or without a second factor). Spends the link: no dry-run.
    """
    with transaction.atomic():
        claim = _lock_open_claim(link)
        if not claim.confirmed_link or claim.confirmed_link != claim_link_digest(link):
            raise engine.ClaimError("confirm the VAT number or TIN first")
        if (
            not user.is_active
            or user.email != normalize_email_address(claim_email(claim.vendor))
            or not has_second_factor(user)
        ):
            raise engine.ClaimError("this account cannot claim the vendor")
        try:
            spend_email_bound_link(link, user, ctx=ctx)
        except ActionLinkSpent as exc:
            raise engine.ClaimError("the claim link was already used") from exc
        before = claim_snapshot(claim)
        claim.status = VendorClaim.Status.SUBMITTED
        claim.claimant = user
        claim.submitted_at = timezone.now()
        claim.confirmed_link = ""
        claim.save()
        record(
            ctx, CLAIM_SUBMITTED, CLAIM_MODEL, str(claim.public_id), before, claim_snapshot(claim)
        )
    return claim


def _decide(
    claim: VendorClaim, status: str, action: str, *, ctx: AuditContext, dry_run: bool
) -> ChangeSet:
    with transaction.atomic():
        locked = (
            VendorClaim.objects.select_for_update(of=("self",))
            .select_related("vendor", "claimant")
            .get(pk=claim.pk)
        )
        if locked.status != VendorClaim.Status.SUBMITTED or locked.claimant is None:
            raise engine.ClaimError(f"{locked.vendor} has no claim waiting for approval")
        if status == VendorClaim.Status.APPROVED:
            _check_claim_still_approvable(locked.vendor, locked.claimant)
        before = claim_snapshot(locked)
        locked.status = status
        if status == VendorClaim.Status.REJECTED:
            # Every open claim link of the vendor stops working (decision on PR #141).
            locked.version += 1
        changes = ChangeSet()
        changes.update(CLAIM_MODEL, str(locked.public_id), before, claim_snapshot(locked))
        if dry_run:
            return changes
        if status == VendorClaim.Status.APPROVED:
            add_member(locked.vendor, locked.claimant, engine.OWNER, ctx=ctx)
        locked.decided_at = timezone.now()
        locked.save(update_fields=["status", "version", "decided_at", "updated_at"])
        audit_changes(changes, ctx=ctx, action=action)
        if status == VendorClaim.Status.REJECTED:
            invalidated = _invalidated(locked.vendor, before["version"], "rejected")
            record(
                ctx, CLAIM_LINK_INVALIDATED, CLAIM_MODEL, str(locked.public_id), before, invalidated
            )
    claim.status, claim.version = locked.status, locked.version
    claim.decided_at = locked.decided_at
    return changes


def _check_claim_still_approvable(vendor: Vendor, claimant: User) -> None:
    """Approval re-checks what submission checked: the vendor has no account holder yet, and
    the claimant's email is still the legal email of record (staff may have changed it)."""
    if VendorMember.objects.filter(vendor=vendor).exists():
        raise engine.ClaimError(f"{vendor} already has an account holder: reject the claim")
    if not claimant.is_active or claimant.email != normalize_email_address(claim_email(vendor)):
        raise engine.ClaimError(
            f"{vendor}: the claimant's email is no longer the address the claim link went to"
        )


def approve_claim(claim: VendorClaim, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Staff approve a submitted claim: the claimant becomes the vendor's owner, and only now
    may change its legal identity, tax profile and payout data. Raises `engine.ClaimError`
    when no claim waits for approval, the vendor meanwhile got an account holder, or the
    claimant's email is no longer the legal email of record (or the account is inactive)."""
    return _decide(claim, VendorClaim.Status.APPROVED, CLAIM_APPROVED, ctx=ctx, dry_run=dry_run)


def reject_claim(claim: VendorClaim, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Staff reject a submitted claim: nothing is granted, and every open claim link of the
    vendor stops working (audited `CLAIM_LINK_INVALIDATED`, reason "rejected").

    Only staff start a new claim afterwards (`send_claim_link` from the admin, behind step-up);
    the claimant cannot re-trigger one. The claimant's account stays: with a new link it claims
    again by signing in with its own password and second factor (`submit_claim_as_account`)."""
    return _decide(claim, VendorClaim.Status.REJECTED, CLAIM_REJECTED, ctx=ctx, dry_run=dry_run)
