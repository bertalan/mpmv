"""Vendor services: vendor data and status changes (VEN.1, SEC.4), memberships with their
permissions (CORE.2), the legal identity with its payout requirements (VEN.12) and onboarding:
application, versioned contract, acceptance and the activation gate (VEN.5); per-vendor feature
flag overrides (ARC.8)."""

import dataclasses
import datetime as dt
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import audit_changes, flags_changed, run_mutation, save_audited

from . import engine
from .models import (
    ContractAcceptance,
    LegalFieldRequirement,
    Vendor,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorMember,
)
from .vies import VatNumberValidator

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
        if dry_run:
            return changes
        vendor.save()
        audit_changes(changes, ctx=ctx, action=UPDATED if stored else CREATED)
    return changes


def delete_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete `vendor` with its translations and memberships; the vendor and each membership
    are audited (translations are storefront texts, not audited)."""
    with transaction.atomic():
        members = list(VendorMember.objects.select_related("vendor", "user").filter(vendor=vendor))
        changes = ChangeSet()
        changes.delete(MODEL_LABEL, str(vendor.public_id), vendor_snapshot(vendor))
        for member in members:
            changes.delete(MEMBER_MODEL, str(member.public_id), member_snapshot(member))
        run_mutation(changes, vendor.delete, ctx=ctx, action=DELETED, dry_run=dry_run)
    return changes


def change_status(
    vendor: Vendor, target: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Move `vendor` to `target` if the state machine allows it; audit the change.

    The check runs against the stored status (row locked), never a possibly stale instance.
    Activating an onboarding vendor also needs its onboarding checklist (VEN.5): the contract in
    force must be accepted. Raises `engine.InvalidTransition` (`engine.ActivationNotAllowed`
    for the checklist), also on dry-run.
    """
    with transaction.atomic():
        current = (
            Vendor.objects.select_for_update().only("status", "slug", "public_id").get(pk=vendor.pk)
        )
        engine.check_transition(current.status, target)
        if current.status == engine.ONBOARDING and target == engine.ACTIVE:
            engine.check_activation(onboarding_checklist(current))

        # Keyed by public_id: the slug is editable and can be reused by another vendor, so it
        # would not identify the vendor across its audit history. The slug is kept for reading.
        changes = ChangeSet()
        changes.update(
            MODEL_LABEL,
            str(current.public_id),
            before={"status": current.status, "slug": current.slug},
            after={"status": str(target), "slug": current.slug},
        )

        def apply() -> None:
            current.status = target
            current.save(update_fields=["status", "updated_at"])

        run_mutation(changes, apply, ctx=ctx, action=STATUS_CHANGED, dry_run=dry_run)

    if not dry_run:
        vendor.status = target
    return changes


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
    again changes nothing. Raises `engine.ContractNotAcceptable` (vendor status, not an owner),
    `engine.NoContract` or `engine.ContractChanged`, also on dry-run.
    """
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().only("status", "public_id").get(pk=vendor.pk)
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
        if ContractAcceptance.objects.filter(vendor=locked, contract=shown).exists():
            return ChangeSet()
        acceptance = ContractAcceptance(
            vendor=locked,
            contract=shown,
            user=user,
            language=text.language,
            text_hash=text_hash,
            accepted_at=timezone.now(),
            ip=ctx.ip,
        )
        changes = ChangeSet()
        changes.create(ACCEPTANCE_MODEL, str(acceptance.public_id), acceptance_snapshot(acceptance))
        return run_mutation(
            changes, acceptance.save, ctx=ctx, action=CONTRACT_ACCEPTED, dry_run=dry_run
        )


def onboarding_checklist(vendor: Vendor) -> engine.OnboardingChecklist:
    """The stored vendor's onboarding checklist: contract in force accepted (blocks activation),
    DAC7 dataset and payout data (listed only: VEN.12 gates payouts with them)."""
    stored = Vendor.objects.get(pk=vendor.pk)
    contract = current_contract()
    accepted = (
        contract is not None
        and ContractAcceptance.objects.filter(vendor=stored, contract=contract).exists()
    )
    return engine.onboarding_checklist(
        contract_version=contract.version if contract is not None and accepted else None,
        current_contract_version=contract.version if contract is not None else None,
        dac7_missing=engine.dac7_missing(stored.legal_identity()),
        payout=legal_profile_completeness(stored),
    )


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
