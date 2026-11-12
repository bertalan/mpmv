"""Vendor services: vendor data and status changes (VEN.1, SEC.4), memberships with their
permissions (CORE.2), the legal identity with its payout requirements (VEN.12) and onboarding:
application, versioned contract, acceptance and the activation gates (VEN.5, the establishment
gate of CMP.13); per-vendor feature flag overrides (ARC.8); claiming an imported vendor
(VEN.24)."""

import dataclasses
import datetime as dt
import hashlib
import logging
from collections.abc import Callable, Collection, Mapping
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import OuterRef, ProtectedError, QuerySet, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy
from wagtail.documents import get_document_model
from wagtail.images import get_image_model
from wagtail.models import Collection as ImageCollection

from apps.accounts import engine as accounts_engine
from apps.accounts.models import ActionLinkUse, User, normalize_email_address
from apps.accounts.services import (
    ActionLinkCheck,
    ActionLinkSpent,
    ActionPurpose,
    Enrolment,
    action_link_path,
    email_bound_link,
    enrol_totp_key,
    has_second_factor,
    issue_action_token,
    register_action_purpose,
    send_action_link_email,
    spend_email_bound_link,
    totp_key_time_step,
)
from apps.core.audit import AuditContext, record
from apps.core.changeset import Change, ChangeSet
from apps.core.fields import read_upload
from apps.core.models import AuditLog, ImageLicence, MediaImage, resolve_translation
from apps.core.services import (
    NoPlatformOperator,
    allowed_vendor_countries,
    audit_changes,
    consume_rate_limit,
    current_operator,
    enqueue_email,
    flags_changed,
    run_mutation,
    save_audited,
)
from apps.core.throttling import ip_bucket
from apps.core.uploads import UploadRefused, check_document

from . import engine
from .models import (
    ContractAcceptance,
    CrossVendorConsent,
    LegalFieldRequirement,
    NoTinCountry,
    SelfCertification,
    Vendor,
    VendorApplication,
    VendorClaim,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorMember,
    VendorTranslation,
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
    "tin_same_as_vat",
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


@dataclasses.dataclass(frozen=True)
class StorefrontProfile:
    """A vendor's public texts in one language (VEN.10): tagline, bio and the non-empty policies
    as `(title, text)` pairs. `language` is the one actually shown, `is_fallback` is True when
    it is the source language standing in for the requested one."""

    tagline: str
    description: str
    policies: tuple[tuple[str, str], ...]
    language: str
    is_fallback: bool


def _clean_rich_text(html: str) -> str:
    """Wagtail's default editor allow-list (CMS.12). Loaded by path: cms sits above vendors."""
    clean: str = import_string("apps.cms.engine.sanitise_rich_text")(html, None)
    return clean


def media_problem(vendor: Vendor, html: str) -> str | None:
    """Why a stored rich text may not be published for `vendor`, or None: its links are https
    addresses, it links no document (waits for ARC.15), and every image it embeds is in the
    vendor's own collection (depth 2, named by `engine.vendor_collection_name`) or below it. A
    foreign, missing or non-numeric id is a problem, so crafted HTML cannot reach another
    vendor's files (D-34)."""
    if engine.has_page_link(html):
        return "Links to other pages of the site are not allowed here."
    if engine.has_unsafe_link(html):
        return "Links must be web addresses starting with https://"
    images, documents = engine.media_references(html)
    if documents:
        # A vendor PDF would be public by URL: no document link until ARC.15 (private storage).
        return "Links to documents are not available yet."
    if not images:
        return None
    if not all(ref.isdecimal() for ref in images):
        return "Choose images from your own library."
    own = ImageCollection.objects.filter(
        depth=2, name=engine.vendor_collection_name(str(vendor.public_id))
    ).first()
    if own is None:
        return "Choose images from your own library."
    allowed = ImageCollection.objects.descendant_of(own, inclusive=True)
    wanted = {int(ref) for ref in images}
    if get_image_model().objects.filter(pk__in=wanted, collection__in=allowed).count() != len(
        wanted
    ):
        return "Choose images from your own library."
    return None


def storefront_html(vendor: Vendor, stored: str) -> str:
    """A stored storefront text ready for `|richtext` (D-34): a legacy plain text becomes
    paragraphs, the HTML goes through the allow-list again (it was cleaned on save, but a write
    that skipped the hook must not reach a page), and a text that embeds another vendor's image
    or document is not shown at all."""
    html = _clean_rich_text(engine.plain_to_html(stored))
    if media_problem(vendor, html) is not None:
        logger.warning(
            "Storefront text of vendor %s hidden: media outside its collection", vendor.pk
        )
        return ""
    return html


def storefront_profile(vendor: Vendor, language: str) -> StorefrontProfile | None:
    """The published text of `vendor` in `language`, else in the source language, else None.
    Drafts and machine translations awaiting review are never public."""
    resolved = resolve_translation(vendor.translations.all(), language)
    text = resolved.translation
    if text is None or resolved.language is None:
        return None
    policies = (
        (gettext_lazy("Shipping"), storefront_html(vendor, text.shipping_policy)),
        (gettext_lazy("Returns and refunds"), storefront_html(vendor, text.refund_policy)),
        (gettext_lazy("Cancellation"), storefront_html(vendor, text.cancellation_policy)),
    )
    return StorefrontProfile(
        tagline=text.tagline,
        description=storefront_html(vendor, text.description),
        policies=tuple((str(title), body) for title, body in policies if body.strip()),
        language=resolved.language,
        is_fallback=resolved.is_fallback,
    )


# Fields encrypted at rest (SEC.3) are masked in audit rows, or the log would hold them in clear.
MASKED_FIELDS: dict[str, Callable[[Any], str]] = {
    "iban": engine.mask_iban,
    "tax_code": engine.mask_tax_code,
    "date_of_birth": engine.mask_personal,
    "place_of_birth": engine.mask_personal,
}


def vendor_snapshot(vendor: Vendor) -> dict[str, Any]:
    """The audited fields of `vendor`, with the encrypted ones masked (SEC.3)."""
    snapshot = {name: getattr(vendor, name) for name in VENDOR_FIELDS}
    for name, mask in MASKED_FIELDS.items():
        snapshot[name] = mask(getattr(vendor, name))
    return snapshot


def save_vendor(vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Validate, create or update `vendor`, auditing its data with before/after (keyed by public
    id, IBAN, tax code and personal data masked). `before` is read from the database with the
    row locked. With "same as the VAT number" the TIN is derived here (`fill_tin_from_vat`).

    Incomplete legal data is always accepted: completeness is checked when a payout is
    requested (`assert_payout_allowed`), never here. A value that is present must be valid:
    `full_clean` (field rules plus `engine.identity_errors`) runs for every adapter, also on
    dry-run, and raises `ValidationError`; it also stores identifiers compact. The status is
    not changed here: it moves only through `change_status`.
    """
    return _save_vendor(vendor, ctx=ctx, dry_run=dry_run)


def _save_vendor(
    vendor: Vendor, *, ctx: AuditContext, dry_run: bool, action: str | None = None
) -> ChangeSet:
    vendor.normalize()
    vendor.fill_tin_from_vat()
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
        audit_changes(changes, ctx=ctx, action=action or (UPDATED if stored else CREATED))
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


def memberships_of(user: AbstractBaseUser | AnonymousUser) -> list[VendorMember]:
    """The user's own memberships with their vendors, by vendor name (the Manager's vendor
    picker, CORE.17). Inactive and anonymous users have none."""
    if not (user.is_authenticated and user.is_active):
        return []
    return list(
        VendorMember.objects.for_member(user)
        .filter(user_id=user.pk)
        .select_related("vendor")
        .order_by("vendor__display_name", "vendor_id")
    )


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
    # "Same as the VAT number" (VEN.23): the TIN follows the VIES answer.
    tin = engine.tin_from_vat(dataclasses.replace(vendor.legal_identity(), vies_status=status))

    def state(vies_status: str, at: Any, tax_code: str, tin_country: str) -> dict[str, Any]:
        return {
            "vat_number": vendor.vat_number,
            "vies_status": vies_status,
            "vies_checked_at": at,
            "tax_code": engine.mask_tax_code(tax_code),
            "tin_country": tin_country,
        }

    changes = ChangeSet()
    changes.update(
        MODEL_LABEL,
        str(vendor.public_id),
        state(vendor.vies_status, vendor.vies_checked_at, vendor.tax_code, vendor.tin_country),
        state(status, checked_at, tin.tax_code, tin.tin_country),
    )

    def apply() -> None:
        Vendor.objects.filter(pk=vendor.pk).update(
            vies_status=status,
            vies_checked_at=checked_at,
            tax_code=tin.tax_code,
            tin_country=tin.tin_country,
            updated_at=checked_at,
        )

    run_mutation(changes, apply, ctx=ctx, action=VAT_CHECKED, dry_run=dry_run)
    if not dry_run:
        vendor.vies_status = status
        vendor.vies_checked_at = checked_at
        vendor.tax_code, vendor.tin_country = tin.tax_code, tin.tin_country
    return changes


def requirement_rules(purpose: str = engine.PAYOUT) -> list[engine.Requirement]:
    """The active `LegalFieldRequirement` rows for `purpose`, as pure rules."""
    rows = LegalFieldRequirement.objects.filter(active=True, purpose=purpose)
    return [row.as_rule() for row in rows]


def business_today() -> dt.date:
    """Today's calendar day in the business time zone (the day the no-TIN list is dated by)."""
    return timezone.localdate(timezone.now(), ZoneInfo(settings.BUSINESS_TIME_ZONE))


def no_tin_countries(on: dt.date | None = None) -> frozenset[str]:
    """The jurisdictions that issued no TIN on the day `on` (default today, business time zone),
    read from the dated table for the pure engine (VEN.27). Pass the day of the period a DAC7
    report covers to reproduce what was true then."""
    windows = [row.window() for row in NoTinCountry.objects.all()]
    return engine.no_tin_countries_on(windows, on or business_today())


def legal_profile_completeness(vendor: Vendor, purpose: str = engine.PAYOUT) -> engine.Completeness:
    """Which fields required for `purpose` the vendor is missing, or holds invalid values for.

    For a payout that is the active requirement rows plus the DAC7 dataset
    (`engine.payout_completeness`): no row can relax DAC7. The no-TIN list is read as of today.

    TOCTOU: the answer is true only at the moment of the read (staff may remove a no-TIN country,
    or the vendor change a field, a moment later). A payout task (VEN.6/VEN.7, not built yet) must
    call this, or `assert_payout_allowed`, inside the same transaction that creates the payout
    and lock the vendor row first, never on a result checked earlier.
    """
    identity = vendor.legal_identity()
    if purpose == engine.PAYOUT:
        return engine.payout_completeness(identity, requirement_rules(purpose), no_tin_countries())
    return engine.completeness(identity, requirement_rules(purpose), purpose=purpose)


def assert_payout_allowed(vendor: Vendor) -> None:
    """Guard for payout requests (VEN.6, VEN.7): raise `engine.PayoutNotAllowed` listing the
    required legal fields, the DAC7 dataset included, that are missing or invalid.

    Checks the stored vendor, not unsaved changes on the instance.
    """
    stored = Vendor.objects.get(pk=vendor.pk)
    result = legal_profile_completeness(stored, engine.PAYOUT)
    if not result.complete:
        raise engine.PayoutNotAllowed(str(stored), result)


# --- DAC7 dataset corrections by staff (VEN.23)

DAC7_CORRECTED = "vendor.dac7_corrected"


def correct_dac7_dataset(
    vendor: Vendor, *, ctx: AuditContext, stepped_up: bool, dry_run: bool = False
) -> ChangeSet:
    """Save a staff correction or completion of the vendor's DAC7 dataset, audited as
    `vendor.dac7_corrected` with before/after (TIN and personal data masked). A new vendor
    created with DAC7 data needs the same step-up; its entry stays `vendor.created` (SEC.4).

    `stepped_up` tells whether the staff member verified a second factor recently (CORE.4);
    without it the correction is refused with `engine.StepUpRequired` and nothing is saved.
    """
    if not stepped_up:
        raise engine.StepUpRequired(
            "Correcting the DAC7 dataset needs a fresh second-factor verification."
        )
    action = None if vendor._state.adding else DAC7_CORRECTED
    return _save_vendor(vendor, ctx=ctx, dry_run=dry_run, action=action)


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


# --- No-TIN jurisdictions (VEN.27, owner decision MQ-16): a staff-edited, audited list.

NO_TIN_MODEL = "vendors.notincountry"
NO_TIN_ADDED = "vendor.no_tin_country_added"
NO_TIN_REMOVED = "vendor.no_tin_country_removed"


def no_tin_snapshot(row: NoTinCountry) -> dict[str, Any]:
    return {
        "country": row.country,
        "reason": row.reason,
        "valid_from": row.valid_from.isoformat(),
        "valid_to": None if row.valid_to is None else row.valid_to.isoformat(),
    }


def no_tin_impact(country: str) -> int:
    """How many vendors fail the payout check once `country` stops counting as issuing no TIN:
    those whose TIN country it is and who have no TIN (they rely on the place of birth)."""
    return Vendor.objects.filter(tin_country=country, tax_code="").count()


def add_no_tin_country(
    country: str, *, reason: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """List `country` as issuing no TIN from today (business time zone), with the reason
    (audited). A new row is created: earlier periods of the same country keep their own rows.
    Raises `ValidationError` for an unknown country, one already listed today, and an empty
    reason."""
    row = NoTinCountry(country=country, reason=reason.strip(), valid_from=business_today())
    row.full_clean(exclude=["public_id"])
    changes = ChangeSet()
    changes.create(NO_TIN_MODEL, row.country, no_tin_snapshot(row))

    def apply() -> None:
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:  # a concurrent add of the same country won the race
            raise ValidationError({"country": f"{row.country} is already on the list."}) from None

    return run_mutation(changes, apply, ctx=ctx, action=NO_TIN_ADDED, dry_run=dry_run)


def remove_no_tin_country(
    country: str, *, reason: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Stop listing `country` from today (business time zone), with the reason (audited): the open
    row gets its `valid_to`, it is never deleted, so past periods stay reproducible. Its
    individuals need a TIN again. Raises `ValidationError` for a country that is not listed and
    for an empty reason."""
    why = reason.strip()
    if not why:
        raise ValidationError({"reason": "A reason is required."})
    code = NoTinCountry._meta.get_field("country").to_python(country)
    with transaction.atomic():
        row = (
            NoTinCountry.objects.select_for_update()
            .filter(country=code, valid_to__isnull=True)
            .first()
        )
        if row is None:
            raise ValidationError({"country": f"{code} is not on the no-TIN list."})
        before = no_tin_snapshot(row)
        today = business_today()
        changes = ChangeSet()
        changes.update(
            NO_TIN_MODEL,
            row.country,
            before,
            {
                **before,
                "valid_to": today.isoformat(),
                "removal_reason": why,
                "vendors_without_tin": no_tin_impact(row.country),
            },
        )

        def apply() -> None:
            row.valid_to = max(today, row.valid_from)  # added today and removed today: empty
            row.removal_reason = why
            row.save(update_fields=["valid_to", "removal_reason", "updated_at"])

        return run_mutation(changes, apply, ctx=ctx, action=NO_TIN_REMOVED, dry_run=dry_run)


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
    vendor: Vendor,
    applicant: User,
    *,
    ctx: AuditContext,
    dry_run: bool = False,
    pending_confirmation: bool = False,
) -> ChangeSet:
    """Create `vendor` (new, unsaved) as an applicant with `applicant` as its owner; audited.

    The application data is validated like any vendor profile (`full_clean`); legal data may be
    incomplete. The VIES result and the legacy user id are reset: only the VIES check and the
    legacy migration set them. Being an owner, the applicant must enrol a second factor (CORE.4).
    Raises `ValidationError`, or `ValueError` for a saved vendor or an inactive user, also on
    dry-run. `pending_confirmation` (the public application, VEN.22) accepts the inactive account
    of an applicant who has yet to confirm the email address.
    """
    if not vendor._state.adding:
        raise ValueError("submit_application takes a new vendor, not a saved one")
    if not applicant.is_active and not pending_confirmation:
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


SELF_CERTIFIED = "vendor.self_certified"
SELF_CERTIFICATION_MODEL = "vendors.selfcertification"


def is_self_certified(vendor: Vendor) -> bool:
    """The vendor's owner certified the current text version in the vendor's current round."""
    return SelfCertification.objects.filter(
        vendor=vendor,
        version=engine.SELF_CERTIFICATION_VERSION,
        onboarding_round=vendor.onboarding_round,
    ).exists()


def platform_duties_missing(vendor: Vendor) -> tuple[str, ...]:
    """What the stored vendor still lacks for activation under CMP.25: the `activation`
    requirement fields, then the self-certification (`engine.platform_duties_missing`).

    The self-certification is asked of every vendor while the field rules are EU-only; that is
    harmless while D-21 admits only EU vendors (follow-up: scope it to the EU if that changes)."""
    stored = Vendor.objects.get(pk=vendor.pk)
    return engine.platform_duties_missing(
        stored.legal_identity().values(),
        requirement_rules(engine.ACTIVATION),
        certified=is_self_certified(stored),
    )


def certify_platform_duties(
    vendor: Vendor,
    user: User,
    *,
    language: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Record that `user`, an owner, certifies for `vendor` the DSA article 30(1)(e) statement
    shown in `language` (CMP.25).

    Stores the text version, the language and the hash of the text.
    Certifying again in the same onboarding round changes nothing. Raises
    `engine.SelfCertificationError` (vendor status, not an owner, no text in `language`), also
    on dry-run.
    """
    with transaction.atomic():
        locked = (
            Vendor.objects.select_for_update()
            .only("status", "public_id", "onboarding_round")
            .get(pk=vendor.pk)
        )
        role = member_role(user, locked) if user.is_active else None
        engine.check_can_certify(locked.status, role)
        text_hash = engine.self_certification_hash(language)
        if SelfCertification.objects.filter(
            vendor=locked,
            version=engine.SELF_CERTIFICATION_VERSION,
            onboarding_round=locked.onboarding_round,
        ).exists():
            return ChangeSet()
        certification = SelfCertification(
            vendor=locked,
            user=user,
            version=engine.SELF_CERTIFICATION_VERSION,
            language=language,
            text_hash=text_hash,
            accepted_at=timezone.now(),
            onboarding_round=locked.onboarding_round,
        )
        changes = ChangeSet()
        changes.create(
            SELF_CERTIFICATION_MODEL,
            str(certification.public_id),
            {
                "vendor": str(locked.public_id),
                "user": str(user.public_id),
                "version": certification.version,
                "language": language,
                "text_hash": text_hash,
                "accepted_at": certification.accepted_at,
                "onboarding_round": certification.onboarding_round,
            },
        )
        return run_mutation(
            changes, certification.save, ctx=ctx, action=SELF_CERTIFIED, dry_run=dry_run
        )


def onboarding_checklist(vendor: Vendor) -> engine.OnboardingChecklist:
    """The stored vendor's onboarding checklist: contract in force accepted and the platform
    duties (CMP.25: DSA article 30 data and self-certification) block activation; DAC7 dataset
    and payout data are listed only (VEN.12 gates payouts with them). Only an acceptance of the
    vendor's current onboarding round counts (VEN.21)."""
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
        dac7_missing=engine.dac7_missing(stored.legal_identity(), no_tin_countries()),
        payout=legal_profile_completeness(stored),
        platform_duties_missing=platform_duties_missing(stored),
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
    claim: VendorClaim,
    status: str,
    action: str,
    *,
    ctx: AuditContext,
    reviewed_version: int | None,
    reviewed_claimant_id: int | None,
    dry_run: bool,
) -> ChangeSet:
    version = claim.version if reviewed_version is None else reviewed_version
    claimant_id = claim.claimant_id if reviewed_claimant_id is None else reviewed_claimant_id
    with transaction.atomic():
        locked = (
            VendorClaim.objects.select_for_update(of=("self",))
            .select_related("vendor", "claimant")
            .get(pk=claim.pk)
        )
        if locked.status != VendorClaim.Status.SUBMITTED or locked.claimant is None:
            raise engine.ClaimError(f"{locked.vendor} has no claim waiting for approval")
        if locked.version != version or locked.claimant_id != claimant_id:
            # Another staff member decided, resent, or someone else claimed meanwhile: the
            # decision applies only to the claim (link version and claimant) that was reviewed.
            raise engine.ClaimError(
                f"{locked.vendor}: the claim changed since it was reviewed; review it again"
            )
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


def approve_claim(
    claim: VendorClaim,
    *,
    ctx: AuditContext,
    reviewed_version: int | None = None,
    reviewed_claimant_id: int | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Staff approve a submitted claim: the claimant becomes the vendor's owner, and only now
    may change its legal identity, tax profile and payout data. Raises `engine.ClaimError`
    when no claim waits for approval, the vendor meanwhile got an account holder, or the
    claimant's email is no longer the legal email of record (or the account is inactive).

    The decision is bound to the claim staff reviewed: `reviewed_version` and
    `reviewed_claimant_id` (by default those of `claim` as loaded) must still match under the
    row lock, or `engine.ClaimError` is raised and nothing changes."""
    return _decide(
        claim,
        VendorClaim.Status.APPROVED,
        CLAIM_APPROVED,
        ctx=ctx,
        reviewed_version=reviewed_version,
        reviewed_claimant_id=reviewed_claimant_id,
        dry_run=dry_run,
    )


def reject_claim(
    claim: VendorClaim,
    *,
    ctx: AuditContext,
    reviewed_version: int | None = None,
    reviewed_claimant_id: int | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Staff reject a submitted claim: nothing is granted, and every open claim link of the
    vendor stops working (audited `CLAIM_LINK_INVALIDATED`, reason "rejected").

    Only staff start a new claim afterwards (`send_claim_link` from the admin, behind step-up);
    the claimant cannot re-trigger one. The claimant's account stays: with a new link it claims
    again by signing in with its own password and second factor (`submit_claim_as_account`).

    Bound to the reviewed claim like `approve_claim`."""
    return _decide(
        claim,
        VendorClaim.Status.REJECTED,
        CLAIM_REJECTED,
        ctx=ctx,
        reviewed_version=reviewed_version,
        reviewed_claimant_id=reviewed_claimant_id,
        dry_run=dry_run,
    )


# --- Storefront profile (CORE.9)

PROFILE_CHANGED = "vendor.profile_changed"
PROFILE_TRANSLATION_STALE = "vendor.profile_translation_stale"
PROFILE_LABEL = "vendors.vendortranslation"
PROFILE_FIELDS = VendorTranslation.translated_fields
PROFILE_EMAIL_TEMPLATE = "vendors.profile_changed"
PROFILE_LOCKED = (Vendor.Status.SUSPENDED, Vendor.Status.CLOSED)
PROFILE_RATE_WINDOW = dt.timedelta(hours=1)


def _profile_texts(row: VendorTranslation) -> dict[str, str]:
    return {name: getattr(row, name) for name in PROFILE_FIELDS}


def _profile_identity(row: VendorTranslation, **extra: Any) -> dict[str, Any]:
    # `vendor` names the vendor in the audit trail (and the filter on it).
    return {"vendor": str(row.vendor.public_id), "language": row.language, **extra}


def _spend_profile_budget(actor: User, vendor: Vendor) -> None:
    """Count one real change to the storefront profile (texts or logo) against the owner's and
    the vendor's hourly limits, so that neither one owner nor several can flood the moderators.
    Raises `ValidationError` when a limit is reached (the caller's transaction rolls back)."""
    # Per owner, and per vendor so that several owners cannot multiply the moderator emails.
    waits = (
        consume_rate_limit(
            f"vendor-profile:user:{actor.pk}",
            limit=settings.VENDOR_PROFILE_SAVE_LIMIT,
            window=PROFILE_RATE_WINDOW,
        ),
        consume_rate_limit(
            f"vendor-profile:vendor:{vendor.pk}",
            limit=settings.VENDOR_PROFILE_VENDOR_SAVE_LIMIT,
            window=PROFILE_RATE_WINDOW,
        ),
    )
    wait = max(waits)
    if wait:
        raise ValidationError(
            f"Too many changes: try again in {max(1, int(wait.total_seconds() // 60))} minutes."
        )


def _lock_profile_vendor(vendor: Vendor, actor: User) -> Vendor:
    """The owner check, then the vendor row under lock: a suspension or closure that commits
    first is seen. Applicants and vendors in onboarding may set up their shop page."""
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner edits the storefront profile.")
    locked = Vendor.objects.select_for_update(of=("self",)).select_related("logo").get(pk=vendor.pk)
    if locked.status in PROFILE_LOCKED:
        raise PermissionDenied("A suspended or closed vendor cannot change its profile.")
    return locked


def save_storefront_profile(
    vendor: Vendor,
    language: str,
    values: Mapping[str, str],
    *,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """The vendor owner changes the storefront texts and policies in one language (CORE.9).

    Only an owner of `vendor` may (`profile` in the role matrix, CORE.2); staff, members of other
    vendors and strangers get `PermissionDenied` before anything is read, and so does a suspended
    or closed vendor. The tagline is plain text (HTML refused); the description and policies are
    rich text, cleaned with the CMS.12 allow-list, capped on the stored HTML
    (`engine.PROFILE_MAX_LENGTH`), and may embed only images and documents of the vendor's own
    collection (D-34). The change is live at once (post-moderation): the row becomes `published`
    only when a text really changes (saving a machine draft unchanged leaves it a draft). One
    `vendor.profile_changed` audit entry holds the changed fields before and after (so staff can
    revert); when the source-language row changes, the other languages sent back to review get a
    `vendor.profile_translation_stale` entry each. The moderators are told through the email
    outbox (the operator's support address until CORE.18; none configured only logs a warning).
    A save that changes nothing writes and sends nothing; a real change counts against the
    owner's hourly limit (`settings.VENDOR_PROFILE_SAVE_LIMIT`). `values` holds only
    `PROFILE_FIELDS`; a missing field is left as it is.
    """
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner edits the storefront profile.")
    if language not in dict(settings.LANGUAGES):
        raise ValidationError({"language": "Unknown language."})
    unknown = sorted(set(values) - set(PROFILE_FIELDS))
    if unknown:
        raise ValidationError(f"Not storefront profile fields: {', '.join(unknown)}.")
    # A bogus image format would make the page fail: refused on the raw text, before the cleaning
    # (which would drop such an embed without a word).
    formats = import_string("apps.cms.engine.image_format_names")()
    format_problems = {
        name: "Choose an image format from the editor."
        for name, value in values.items()
        if name in engine.RICH_TEXT_FIELDS
        and any(fmt not in formats for fmt in engine.image_embed_formats(value))
    }
    if format_problems:
        raise ValidationError(format_problems)
    # Rich text is cleaned first (the CMS.12 hook would do it on `save` anyway): the caps, the
    # media rule and the before/after audit all see the text that would be stored.
    values = {
        name: _clean_rich_text(value) if name in engine.RICH_TEXT_FIELDS else value
        for name, value in values.items()
    }
    problems = engine.profile_text_errors(values)
    if not problems:
        problems = {
            name: problem
            for name, value in values.items()
            if name in engine.RICH_TEXT_FIELDS
            if (problem := media_problem(vendor, value)) is not None
        }
    if problems:
        raise ValidationError(problems)
    with transaction.atomic():
        # Read under lock: a suspension or closure that commits first is seen. Applicants and
        # vendors in onboarding may set up their shop page.
        status = (
            Vendor.objects.select_for_update().values_list("status", flat=True).get(pk=vendor.pk)
        )
        if status in PROFILE_LOCKED:
            raise PermissionDenied("A suspended or closed vendor cannot change its profile.")
        stored = (
            VendorTranslation.objects.select_for_update()
            .filter(vendor=vendor, language=language)
            .first()
        )
        row = stored or VendorTranslation(vendor=vendor, language=language)
        before = _profile_texts(row)
        for name, value in values.items():
            setattr(row, name, value)
        after = _profile_texts(row)
        changed = [name for name in PROFILE_FIELDS if before[name] != after[name]]
        changes = ChangeSet()
        if changed:
            # The audit keeps the changed fields only: bounded, and enough to revert.
            key = f"{vendor.public_id}/{language}"
            old_status = None if stored is None else stored.status
            row.status = VendorTranslation.Status.PUBLISHED  # the owner's own words, live now
            row.full_clean()
            identity = _profile_identity(row)
            new = {**identity, "status": row.status, **{n: after[n] for n in changed}}
            if stored is None:
                changes.create(PROFILE_LABEL, key, new)
            else:
                old = {**identity, "status": old_status, **{n: before[n] for n in changed}}
                changes.update(PROFILE_LABEL, key, old, new)
        if dry_run or changes.is_empty:
            return changes
        _spend_profile_budget(actor, vendor)
        siblings = dict(
            VendorTranslation.objects.filter(vendor=vendor)
            .exclude(language=language)
            .values_list("language", "status")
        )
        try:
            with transaction.atomic():  # a savepoint: a lost race must not poison the outer one
                row.save()
        except IntegrityError as error:  # two first saves of one language at once
            raise ValidationError("The profile was changed at the same time: try again.") from error
        audit_changes(changes, ctx=ctx, action=PROFILE_CHANGED)
        _audit_stale_translations(vendor, language, siblings, ctx=ctx)
        _notify_profile_changed(vendor, language, changed)
    return changes


def _audit_stale_translations(
    vendor: Vendor, language: str, siblings: Mapping[str, str], *, ctx: AuditContext
) -> None:
    """Saving the source-language texts sends reviewed and published translations back to
    review with a queryset update (no audit): record each of those status changes."""
    stale = ChangeSet()
    now = VendorTranslation.objects.filter(vendor=vendor).exclude(language=language)
    for other, new_status in now.values_list("language", "status"):
        old_status = siblings.get(other)
        if old_status is not None and old_status != new_status:
            identity = {"vendor": str(vendor.public_id), "language": other}
            stale.update(
                PROFILE_LABEL,
                f"{vendor.public_id}/{other}",
                {**identity, "status": old_status},
                {**identity, "status": new_status},
            )
    audit_changes(stale, ctx=ctx, action=PROFILE_TRANSLATION_STALE)


def _notify_profile_changed(vendor: Vendor, language: str, changed: list[str]) -> None:
    """Post-moderation notice through the outbox, in the caller's transaction."""
    try:
        recipient = current_operator().support_email.strip()
    except NoPlatformOperator:
        recipient = ""
    if not recipient:
        logger.warning(
            "vendor %s changed its storefront profile and no platform support email is "
            "configured: no moderator notice sent",
            vendor.public_id,
        )
        return
    enqueue_email(
        recipient=recipient,
        template_key=PROFILE_EMAIL_TEMPLATE,
        language=settings.LANGUAGE_CODE,
        context={
            "vendor_name": vendor.display_name,
            "language": language,
            "fields": ", ".join(changed),
        },
    )


# --- Vendor logo (CORE.9 part 2b)

LOGO_CHANGED = "vendor.logo_changed"
LOGO_LICENCE_CODE = "own-work"


def _logo_snapshot(logo: Any, alt: str) -> dict[str, Any]:
    """The audited state: the image as `image:<id>` (its file is deleted when replaced, so the
    id only says that it was a different image) and the alt text."""
    return {"logo": None if logo is None else f"image:{logo.pk}", "logo_alt": alt}


def vendor_logo_collection(vendor: Vendor) -> ImageCollection:
    """The vendor's own image collection `vendor-<public id>` (created on first use, under the
    root; no group permissions, so only the superuser sees it in the Wagtail admin).

    Call it with the vendor row locked (the same vendor twice) and it also locks the root
    collection row: the collections are one shared tree, so two vendors' first uploads would
    otherwise compute the same child path. Found by name among the root's children, the oldest
    first: staff can rename or duplicate collections in the Wagtail admin, and a duplicate never
    changes which one an upload goes to.
    """
    name = f"vendor-{vendor.public_id}"
    root = ImageCollection.objects.select_for_update().get(
        pk=ImageCollection.get_first_root_node().pk
    )
    found = root.get_children().filter(name=name).order_by("pk").first()
    return found if found is not None else root.add_child(name=name)


def _new_logo_image(
    vendor: Vendor, upload: Any, actor: User, collection: ImageCollection
) -> MediaImage:
    """The unsaved image for `upload`, validated by `MediaImage.clean` (SEC.2: content checks,
    caps, safe name; the file is re-encoded when saved). Own work of the vendor."""
    licence = ImageLicence.objects.filter(code=LOGO_LICENCE_CODE).first()
    if licence is None:  # the seeded allow list (core migration 0013) is incomplete
        raise ValidationError({"logo": "Logos cannot be uploaded right now: contact support."})
    image = MediaImage(
        title=f"Logo of {vendor.display_name}"[:255],
        file=upload,
        collection=collection,
        licence=licence,
        author=vendor.display_name[:200],
        uploaded_by_user=actor,
    )
    try:
        image.full_clean()
    except ValidationError as error:
        messages = [m for items in error.message_dict.values() for m in items]
        raise ValidationError({"logo": messages}) from error
    return image


def set_vendor_logo(
    vendor: Vendor,
    *,
    upload: Any,
    alt: str,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """The vendor owner sets or replaces the logo, or only its alt text (`upload=None`).

    Owner only, and not for a suspended or closed vendor (`PermissionDenied`, before any file is
    read). The alt text is required, short plain text. A new file goes through the SEC.2 checks
    of `MediaImage` into the vendor's own collection; the previous image is deleted, and a
    refused file leaves the current logo as it is. One `vendor.logo_changed` audit entry holds
    the logo and alt text before and after. It counts against the profile save limits.
    """
    alt = alt.strip()
    spent = False
    if upload is not None and not dry_run:
        # A refused file costs decode work: count every attempt, in its own transaction so that
        # the refusal below does not roll the count back.
        if not has_vendor_permission(actor, vendor, "profile"):
            raise PermissionDenied("Only the vendor owner edits the storefront profile.")
        _spend_profile_budget(actor, vendor)
        spent = True
    stored: tuple[Any, str] | None = None
    try:
        with transaction.atomic():
            locked = _lock_profile_vendor(vendor, actor)
            problem = engine.logo_alt_errors(alt)
            if problem:
                raise ValidationError({"logo_alt": problem})
            if upload is None and locked.logo is None:
                raise ValidationError({"logo": "Choose an image."})
            before = _logo_snapshot(locked.logo, locked.logo_alt)
            image = locked.logo
            if upload is not None:
                collection = ImageCollection.get_first_root_node() if dry_run else None
                collection = collection or vendor_logo_collection(locked)
                image = _new_logo_image(locked, upload, actor, collection)
            after = _logo_snapshot(image if upload is None else _PendingImage(), alt)
            changes = ChangeSet()
            changes.update(MODEL_LABEL, str(vendor.public_id), before, after)
            if dry_run or changes.is_empty:
                return changes
            if not spent:
                _spend_profile_budget(actor, vendor)
            previous = locked.logo
            if upload is not None:
                image.save()  # type: ignore[union-attr]
                stored = (image.file.storage, image.file.name)  # type: ignore[union-attr]
                after = _logo_snapshot(image, alt)
                changes = ChangeSet()
                changes.update(MODEL_LABEL, str(vendor.public_id), before, after)
            Vendor.objects.filter(pk=vendor.pk).update(
                logo=image, logo_alt=alt, updated_at=timezone.now()
            )
            audit_changes(changes, ctx=ctx, action=LOGO_CHANGED)
            if upload is not None and previous is not None:
                _discard_logo_image(previous, vendor)
            _notify_profile_changed(vendor, "all languages", ["logo"])
    except BaseException:
        if stored is not None:  # the row rolls back; the file would stay on disk
            stored[0].delete(stored[1])
        raise
    vendor.logo, vendor.logo_alt = image, alt
    return changes


def _discard_logo_image(image: MediaImage, vendor: Vendor) -> None:
    """Delete a replaced or removed logo image, unless something else still uses it (another
    vendor, or a protected reference): then it is only unlinked from `vendor`."""
    if Vendor.objects.filter(logo=image).exclude(pk=vendor.pk).exists():
        return
    try:
        with transaction.atomic():  # a savepoint: a protected row must not poison the caller's
            image.delete()
    except ProtectedError:
        logger.info("logo image %s is still referenced: kept", image.pk)


def remove_vendor_logo(
    vendor: Vendor, *, actor: User, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """The vendor owner removes the logo: the image is deleted, `vendor.logo_changed` audited."""
    with transaction.atomic():
        locked = _lock_profile_vendor(vendor, actor)
        changes = ChangeSet()
        if locked.logo is None:
            return changes
        changes.update(
            MODEL_LABEL,
            str(vendor.public_id),
            _logo_snapshot(locked.logo, locked.logo_alt),
            _logo_snapshot(None, ""),
        )
        if dry_run:
            return changes
        _spend_profile_budget(actor, vendor)
        previous = locked.logo
        Vendor.objects.filter(pk=vendor.pk).update(
            logo=None, logo_alt="", updated_at=timezone.now()
        )
        audit_changes(changes, ctx=ctx, action=LOGO_CHANGED)
        _discard_logo_image(previous, vendor)
    vendor.logo, vendor.logo_alt = None, ""
    return changes


# --- Storefront media chooser (VEN.31, D-34)

MEDIA_UPLOADED = "vendor.media_uploaded"
MEDIA_DENIED = "vendor.media_denied"
MEDIA_DENIED_AUDIT_LIMIT = 20  # denial rows per vendor and hour
MEDIA_UPLOAD_LIMIT = 30  # uploads per owner and hour: every attempt costs image decode work


def vendor_media_collection(vendor: Vendor) -> ImageCollection | None:
    """The vendor's own collection `vendor-<public id>` (depth 2), or None before the first
    upload. Read only: the chooser creates nothing."""
    # The oldest by pk, as `vendor_logo_collection` finds it: reads and writes use one collection
    # even when staff create a duplicate name.
    return (
        ImageCollection.objects.filter(
            depth=2, name=engine.vendor_collection_name(str(vendor.public_id))
        )
        .order_by("pk")
        .first()
    )


def vendor_media_images(vendor: Vendor) -> QuerySet[Any]:
    """The images the vendor may list and choose: its collection and the ones below it."""
    own = vendor_media_collection(vendor)
    images: QuerySet[Any] = get_image_model().objects.all()
    if own is None:
        return images.none()
    allowed = ImageCollection.objects.descendant_of(own, inclusive=True)
    found: QuerySet[Any] = images.filter(collection__in=allowed)
    return found


def audit_media_denied(vendor: Vendor, *, ctx: AuditContext, what: str, target: str = "") -> None:
    """A chooser or upload request named something outside the vendor's own collection.

    Rate limited per vendor and hour: past the limit the request still answers 404, but no row
    is written, so a loop on random ids cannot grow the audit table."""
    if consume_rate_limit(
        f"vendor-media-denied:vendor:{vendor.pk}",
        limit=MEDIA_DENIED_AUDIT_LIMIT,
        window=PROFILE_RATE_WINDOW,
    ):
        return
    record(
        ctx,
        MEDIA_DENIED,
        MODEL_LABEL,
        str(vendor.public_id),
        None,
        {"request": what, "target": target[:100], "vendor": str(vendor.public_id)},
    )


def upload_storefront_image(
    vendor: Vendor,
    *,
    upload: Any,
    title: str,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> MediaImage:
    """The vendor owner uploads an image from the storefront editor, into the vendor's own
    collection only (the caller cannot name another). The SEC.2 checks of `MediaImage` apply
    (content type, caps, re-encoding without metadata, no SVG); the image is the vendor's own
    work (CMS.8). Owner only, and not for a suspended or closed vendor (`PermissionDenied`).
    Counted against an hourly limit per owner; one `vendor.media_uploaded` audit entry.
    Raises `ValidationError({"file": [...]})` for a refused file. With `dry_run` the file is
    checked and the unsaved image returned: nothing is written, counted or audited."""
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner uploads storefront images.")
    wait = (
        None
        if dry_run
        else consume_rate_limit(
            f"vendor-media:user:{actor.pk}", limit=MEDIA_UPLOAD_LIMIT, window=PROFILE_RATE_WINDOW
        )
    )
    if wait:
        minutes = max(1, int(wait.total_seconds() // 60))
        raise ValidationError({"file": f"Too many uploads: try again in {minutes} minutes."})
    stored: tuple[Any, str] | None = None
    try:
        with transaction.atomic():
            locked = _lock_profile_vendor(vendor, actor)
            licence = ImageLicence.objects.filter(code=LOGO_LICENCE_CODE).first()
            if licence is None:  # the seeded allow list (core migration 0013) is incomplete
                raise ValidationError({"file": "Uploads are not available: contact support."})
            root = ImageCollection.get_first_root_node()
            target = root if dry_run else vendor_logo_collection(locked)
            image = MediaImage(
                title=(title.strip() or "Image")[:255],
                file=upload,
                collection=target,
                licence=licence,
                author=vendor.display_name[:200],
                uploaded_by_user=actor,
            )
            try:
                image.full_clean()
            except ValidationError as error:
                messages = [m for items in error.message_dict.values() for m in items]
                raise ValidationError({"file": messages}) from error
            if dry_run:
                return image
            image.save()
            stored = (image.file.storage, image.file.name)
            record(
                ctx,
                MEDIA_UPLOADED,
                MODEL_LABEL,
                str(vendor.public_id),
                None,
                {
                    "image": f"image:{image.pk}",
                    "title": image.title,
                    "vendor": str(vendor.public_id),
                },
            )
    except BaseException:
        if stored is not None:  # the row rolls back; the file would stay on disk
            stored[0].delete(stored[1])
        raise
    return image


def vendor_media_documents(vendor: Vendor) -> QuerySet[Any]:
    """The documents the vendor may list and choose: its collection and the ones below it."""
    own = vendor_media_collection(vendor)
    documents: QuerySet[Any] = get_document_model().objects.all()
    if own is None:
        return documents.none()
    allowed = ImageCollection.objects.descendant_of(own, inclusive=True)
    found: QuerySet[Any] = documents.filter(collection__in=allowed)
    return found


def upload_storefront_document(
    vendor: Vendor,
    *,
    upload: Any,
    title: str,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Any:
    """The vendor owner uploads a PDF from the storefront editor, into the vendor's own
    collection only (the caller cannot name another). The SEC.2 document checks apply (PDF by
    content, size cap, no HTML or archive polyglot, a name the app makes). Owner only, and not
    for a suspended or closed vendor (`PermissionDenied`). Shares the hourly upload limit of the
    images; one `vendor.media_uploaded` audit entry (`after.document`). Raises
    `ValidationError({"file": [...]})` for a refused file. With `dry_run` the file is checked and
    the unsaved document returned: nothing is written, counted or audited."""
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner uploads storefront documents.")
    wait = (
        None
        if dry_run
        else consume_rate_limit(
            f"vendor-media:user:{actor.pk}", limit=MEDIA_UPLOAD_LIMIT, window=PROFILE_RATE_WINDOW
        )
    )
    if wait:
        minutes = max(1, int(wait.total_seconds() // 60))
        raise ValidationError({"file": f"Too many uploads: try again in {minutes} minutes."})
    limit = settings.WAGTAILDOCS_MAX_UPLOAD_SIZE
    try:
        upload.name = check_document(read_upload(upload, limit), upload.name, max_bytes=limit)
    except UploadRefused as refusal:
        raise ValidationError({"file": refusal.message}) from None
    stored: tuple[Any, str] | None = None
    try:
        with transaction.atomic():
            locked = _lock_profile_vendor(vendor, actor)
            root = ImageCollection.get_first_root_node()
            target = root if dry_run else vendor_logo_collection(locked)
            document = get_document_model()(
                title=(title.strip() or "Document")[:255],
                file=upload,
                collection=target,
                uploaded_by_user=actor,
            )
            if dry_run:
                return document
            document.save()
            stored = (document.file.storage, document.file.name)
            record(
                ctx,
                MEDIA_UPLOADED,
                MODEL_LABEL,
                str(vendor.public_id),
                None,
                {
                    "document": f"document:{document.pk}",
                    "title": document.title,
                    "vendor": str(vendor.public_id),
                },
            )
    except BaseException:
        if stored is not None:  # the row rolls back; the file would stay on disk
            stored[0].delete(stored[1])
        raise
    return document


class _PendingImage:
    """Stands for the image of a dry run, which is never saved."""

    pk = "new"


# --- Public "Sell with us" application (VEN.22, owner decision Q-55)

APPLICATION_PURPOSE = "vendors.application"
APPLICATION_CONFIRM_TEMPLATE = "vendors.application_confirm"
APPLICATION_REMINDER_TEMPLATE = "vendors.application_reminder"
APPLICATION_EXISTS_TEMPLATE = "vendors.application_exists"
APPLICATION_CANCEL_PURPOSE = "vendors.application_cancel"
APPLICATION_MODEL = "vendors.vendorapplication"
APPLICATION_PENDING = "vendor.application_pending"
APPLICATION_LINK_ROTATED = "vendor.application_link_rotated"
APPLICATION_CONFIRMED = "vendor.application_confirmed"
APPLICATION_DUPLICATE = "vendor.application_duplicate"
APPLICATION_PURGED = "vendor.application_purged"
APPLICATION_REMINDED = "vendor.application_reminded"
APPLICATION_CANCELLED = "vendor.application_cancelled"
APPLICATION_CANCEL_VERSION = "1"
APPLICATION_RATE_WINDOW = dt.timedelta(minutes=15)


class ApplicationLinkInvalid(ValueError):
    """The confirmation link is spent, expired, or no longer names a pending application."""


def _applicant_membership(user: User) -> VendorMember | None:
    """The applicant vendor `user` owns, if any (the first one: never a `get`, so an account
    with several memberships cannot turn a form submission into a server error)."""
    return (
        VendorMember.objects.select_related("vendor")
        .filter(user=user, role=engine.OWNER, vendor__status=engine.APPLICANT)
        .order_by("id")
        .first()
    )


def _pending_application(key: str) -> VendorApplication | None:
    """The pending application of the inactive account `key` (its public id).

    Pending means: the marker written by `apply_to_sell` still exists (it is deleted on
    confirmation and by the retention job), the account is inactive, and it owns an applicant
    vendor. Staff-blocked, migrated and staff-created accounts have no marker, so no form
    submission or link can reactivate them."""
    try:
        application = (
            VendorApplication.objects.select_related("user").filter(user__public_id=key).first()
        )
    except ValidationError:  # not a UUID
        return None
    if application is None or application.user.is_active:
        return None
    return application if _applicant_membership(application.user) else None


def _application_version(object_key: str) -> str | None:
    """The version links are bound to: a new link (a re-application before confirming) raises
    it, so the older link stops working. There is no password to change before activation."""
    application = _pending_application(object_key)
    return str(application.version) if application else None


def _application_email_of(object_key: str) -> str | None:
    application = _pending_application(object_key)
    return application.user.email if application else None


def _cancel_version(object_key: str) -> str | None:
    """The cancel link is bound to the pending state only, not to the confirmation link's
    version: a new confirmation link does not kill the "this was not me" link of the older
    mail. Once the account is confirmed or deleted nothing is pending, and it stops working."""
    return APPLICATION_CANCEL_VERSION if _pending_application(object_key) else None


def application_path(object_key: str) -> str:
    return reverse("vendors:apply_confirm", args=[object_key])


def application_cancel_path(object_key: str) -> str:
    return reverse("vendors:apply_cancel", args=[object_key])


def register_application_purpose() -> None:
    """Register the email-bound confirmation link with the CORE.15 action links."""
    register_action_purpose(
        ActionPurpose(
            name=APPLICATION_PURPOSE,
            version_of=_application_version,
            url_of=application_path,
            email_of=_application_email_of,
            max_age=settings.VENDOR_APPLICATION_LINK_MAX_AGE,
        )
    )
    register_action_purpose(
        ActionPurpose(
            name=APPLICATION_CANCEL_PURPOSE,
            version_of=_cancel_version,
            url_of=application_cancel_path,
            email_of=_application_email_of,
            max_age=settings.VENDOR_APPLICATION_LINK_MAX_AGE,
        )
    )


def application_link(session: SessionBase, object_key: str) -> ActionLinkCheck:
    """The confirmation link opened in this session for the account `object_key`."""
    return email_bound_link(session, APPLICATION_PURPOSE, object_key)


def application_cancel_link(session: SessionBase, object_key: str) -> ActionLinkCheck:
    """The "this was not me" link opened in this session for the account `object_key`."""
    return email_bound_link(session, APPLICATION_CANCEL_PURPOSE, object_key)


def application_retry_after(ip: str | None) -> dt.timedelta:
    """Count one confirm-page POST from `ip`; zero when allowed, else the wait (Retry-After)."""
    return consume_rate_limit(
        f"vendor-apply:ip:{ip_bucket(ip) or 'unknown'}",
        limit=settings.VENDOR_APPLICATION_RATE_LIMIT,
        window=APPLICATION_RATE_WINDOW,
    )


def _daily_key(kind: str, email: str) -> str:
    digest = hashlib.sha256(email.lower().encode()).hexdigest()[:32]
    return f"vendors.application.{kind}:{digest}:{timezone.now().date().isoformat()}"


def _language_of(language: str) -> str:
    return language if language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE


def application_snapshot(application: VendorApplication) -> dict[str, Any]:
    """Public ids and the link version only."""
    return {"user": str(application.user.public_id), "version": application.version}


def _confirmation_email(
    application: VendorApplication,
    *,
    ctx: AuditContext,
    key: str | None,
    template_key: str = APPLICATION_CONFIRM_TEMPLATE,
) -> ChangeSet:
    """Queue a mail with the confirmation link and the "cancel / this was not me" link. It
    names nothing the applicant typed: anyone can send it to any address, so it must not
    carry their words."""
    object_key = str(application.user.public_id)
    cancel_token = issue_action_token(
        purpose=APPLICATION_CANCEL_PURPOSE,
        object_key=object_key,
        version=APPLICATION_CANCEL_VERSION,
        expires_in=settings.VENDOR_APPLICATION_LINK_MAX_AGE,
    )
    return send_action_link_email(
        purpose=APPLICATION_PURPOSE,
        object_key=object_key,
        version=application.version,
        template_key=template_key,
        context={"cancel_url": settings.SITE_BASE_URL.rstrip("/") + action_link_path(cancel_token)},
        language=application.user.preferred_language,
        expires_in=settings.VENDOR_APPLICATION_LINK_MAX_AGE,
        idempotency_key=key,
        ctx=ctx,
    )


def _resend_confirmation(application: VendorApplication, *, ctx: AuditContext) -> bool:
    """A new link for a pending application: it raises the version, so the old link dies. At
    most one per address and day: when none is queued the version is not raised either, so the
    link already delivered keeps working. Returns whether a link was sent."""
    with transaction.atomic():
        locked = (
            VendorApplication.objects.select_related("user")
            .select_for_update()
            .get(pk=application.pk)
        )
        before = application_snapshot(locked)
        locked.version += 1
        locked.link_sent_at = timezone.now()
        locked.save(update_fields=["version", "link_sent_at", "updated_at"])
        changes = _confirmation_email(locked, ctx=ctx, key=_daily_key("resend", locked.user.email))
        if changes.is_empty:
            transaction.set_rollback(True)
            return False
        record(
            ctx,
            APPLICATION_LINK_ROTATED,
            APPLICATION_MODEL,
            str(locked.public_id),
            before,
            application_snapshot(locked),
        )
    return True


def _answer_duplicate(user: User, *, ctx: AuditContext) -> None:
    """Someone applied with the address of an existing account: the caller gets the same answer
    as a new applicant. A pending applicant gets a fresh confirmation link, any other account a
    notice with no link; at most one such email per address and day. Nothing else changes."""
    application = _pending_application(str(user.public_id))
    if application is not None:
        _resend_confirmation(application, ctx=ctx)
    else:
        enqueue_email(
            recipient=user.email,
            template_key=APPLICATION_EXISTS_TEMPLATE,
            language=user.preferred_language,
            context={"sign_in_url": settings.SITE_BASE_URL.rstrip("/") + reverse("login")},
            idempotency_key=_daily_key("exists", user.email),
        )
    record(
        ctx,
        APPLICATION_DUPLICATE,
        "accounts.user",
        str(user.public_id),
        None,
        {"outcome": "resent" if application is not None else "notified"},
    )


def apply_to_sell(
    *,
    full_name: str,
    email: str,
    business_name: str,
    country: str,
    language: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """The public "Sell with us" application: account and vendor application in one step.

    A new address gets an INACTIVE account with an UNUSABLE password, an `applicant` vendor it
    owns, a pending marker (`VendorApplication`) and a confirmation link by email through the
    ARC.16 outbox. Nobody chooses a password here: whoever opens the link sets it on the confirm
    page (`confirm_application`), so typing someone else's address gains nothing, not even a
    secret to use after they click. An address that already has an account changes nothing
    and the caller learns nothing (`_answer_duplicate`): the returned ChangeSet is empty. The
    vendor is validated before the address is looked up, so both paths refuse the same inputs.

    Raises `ValidationError` for invalid data, on dry-run too.
    """
    address = normalize_email_address(email)
    user = User(
        email=address,
        full_name=full_name.strip(),
        preferred_language=_language_of(language),
        is_active=False,
    )
    user.set_unusable_password()
    vendor = Vendor(
        legal_name=business_name.strip(),
        display_name=business_name.strip(),
        contact_email=address,
        country=country,
    )
    vendor.status = engine.APPLICANT
    vendor.normalize()
    vendor.full_clean()
    if vendor.country not in allowed_vendor_countries():  # CMP.13: nobody applies in vain
        raise ValidationError({"country": "We do not accept businesses established there."})
    existing = User.objects.filter(email__iexact=address).order_by("id").first()
    if existing is None:
        if dry_run:
            return submit_application(
                vendor, user, ctx=ctx, dry_run=True, pending_confirmation=True
            )
        try:
            with transaction.atomic():
                user.save()
                changes = submit_application(vendor, user, ctx=ctx, pending_confirmation=True)
                application = VendorApplication.objects.create(
                    user=user, link_sent_at=timezone.now()
                )
                record(
                    ctx,
                    APPLICATION_PENDING,
                    APPLICATION_MODEL,
                    str(application.public_id),
                    None,
                    application_snapshot(application),
                )
                _confirmation_email(application, ctx=ctx, key=None)
        except IntegrityError:  # a concurrent request took the address first
            existing = User.objects.filter(email__iexact=address).order_by("id").first()
            if existing is None:
                raise
        else:
            return changes
    if not dry_run:
        _answer_duplicate(existing, ctx=ctx)
    return ChangeSet()


def confirm_application(link: ActionLinkCheck, *, password: str, ctx: AuditContext) -> User:
    """The applicant opened the emailed link and chose a password: activate the account.

    One transaction: the password is validated (`ValidationError` leaves the link usable), the
    single-use link is spent, the password is set, the account is activated and the pending
    marker is deleted. It never signs anyone in: the applicant signs in with the password
    just chosen and enrols a second factor (CORE.4). The vendor stays `applicant` until staff
    review it (VEN.5). Raises `ApplicationLinkInvalid`.
    """
    claims = link.claims
    if link.refusal is not None or claims is None:
        raise ApplicationLinkInvalid(link.refusal or "link not opened")
    with transaction.atomic():
        application = _pending_application(claims.object_key)
        if application is None or str(application.version) != claims.version:
            raise ApplicationLinkInvalid("no pending application for this link")
        user = User.objects.select_for_update().get(pk=application.user_id)
        if user.is_active:
            raise ApplicationLinkInvalid("already confirmed")
        membership = _applicant_membership(user)
        if membership is None:
            raise ApplicationLinkInvalid("no applicant vendor")
        validate_password(password, user)
        try:
            spend_email_bound_link(link, user, ctx=ctx)
        except ActionLinkSpent as exc:
            raise ApplicationLinkInvalid(str(exc)) from exc
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        snapshot = {"user": str(user.public_id), "vendor": str(membership.vendor.public_id)}
        application.delete()
        record(
            ctx,
            APPLICATION_CONFIRMED,
            "accounts.user",
            str(user.public_id),
            {**snapshot, "is_active": False},
            {**snapshot, "is_active": True},
        )
    return user


def _delete_application(application: VendorApplication) -> bool:
    """Delete an unconfirmed application: the account, its applicant vendors, their memberships
    and link uses. An account that also belongs to a real vendor keeps it: only the stale marker
    goes. Returns whether the account was deleted."""
    user = application.user
    if VendorMember.objects.filter(user=user).exclude(vendor__status=engine.APPLICANT).exists():
        application.delete()
        return False
    for membership in VendorMember.objects.select_related("vendor").filter(
        user=user, vendor__status=engine.APPLICANT
    ):
        membership.vendor.delete()  # its memberships go with it
    ActionLinkUse.objects.filter(user=user).delete()
    user.delete()  # the marker goes with the account
    return True


APPLICATION_BATCH_SIZE = 200  # rows a job selects and processes at a time


def _lock_pending(pk: int, *, skip_locked: bool = True) -> VendorApplication | None:
    """Lock the marker and its account, then re-check under the locks that the application is
    still pending. Call inside `transaction.atomic()`.

    A job passes `skip_locked`: a row another transaction holds (an applicant confirming right
    now) is left for the next run. A cancel waits for the lock instead, and then finds nothing
    when the confirm won. Returns None when the application is gone or no longer pending: the
    account is active, the marker was deleted, or the vendor moved past `applicant`."""
    marker = VendorApplication.objects.select_for_update(skip_locked=skip_locked).filter(pk=pk)
    application = marker.first()
    if application is None:
        return None
    user = (
        User.objects.select_for_update(skip_locked=skip_locked)
        .filter(pk=application.user_id, is_active=False)
        .first()
    )
    if user is None:
        return None
    application.user = user
    if _pending_application(str(user.public_id)) is None:
        return None
    return application


def cancel_application(
    link: ActionLinkCheck, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """ "This was not me": delete the pending application at once (D-24). One-time signed link,
    no login; nothing is blocked afterwards and no address or hash is kept. Idempotent: returns
    an empty ChangeSet, changing nothing, when no application is pending (already cancelled,
    confirmed or purged, also when a confirm won a race with this call). The audit entry holds
    no personal data. The link is not spent: deleting the application is what stops it working,
    and it never touches a confirmed account."""
    claims = link.claims
    changes = ChangeSet()
    if link.refusal is not None or claims is None:
        return changes
    pending = _pending_application(claims.object_key)
    if pending is None:
        return changes
    if dry_run:
        changes.delete(APPLICATION_MODEL, str(pending.public_id), application_snapshot(pending))
        return changes
    with transaction.atomic():
        application = _lock_pending(pending.pk, skip_locked=False)
        if application is None:  # a confirm (or a purge) got there first
            return changes
        public_id = str(application.public_id)
        changes.delete(APPLICATION_MODEL, public_id, application_snapshot(application))
        _delete_application(application)
        record(ctx, APPLICATION_CANCELLED, APPLICATION_MODEL, public_id, None, {"deleted": 1})
    return changes


def _due_reminder_ids(cutoff: dt.datetime, after_id: int) -> list[int]:
    return list(
        VendorApplication.objects.filter(
            user__is_active=False,
            reminder_sent_at__isnull=True,
            created_at__lte=cutoff,
            id__gt=after_id,
        )
        .order_by("id")
        .values_list("id", flat=True)[:APPLICATION_BATCH_SIZE]
    )


def _remind_one(
    pk: int, cutoff: dt.datetime, now: dt.datetime, *, ctx: AuditContext
) -> dict[str, Any] | None:
    """Send the reminder of one application, under its locks and after a re-check; returns the
    marker's snapshot when a mail was queued."""
    with transaction.atomic():
        application = _lock_pending(pk)
        if (
            application is None
            or application.reminder_sent_at is not None
            or application.created_at > cutoff
        ):
            return None
        sent = _confirmation_email(
            application,
            ctx=ctx,
            key=f"vendors.application.reminder:{application.public_id}",
            template_key=APPLICATION_REMINDER_TEMPLATE,
        )
        application.reminder_sent_at = now
        application.save(update_fields=["reminder_sent_at", "updated_at"])
        if sent.is_empty:  # a retry of a mail already queued
            return None
        snapshot = application_snapshot(application)
        record(
            ctx, APPLICATION_REMINDED, APPLICATION_MODEL, str(application.public_id), None, snapshot
        )
        return snapshot


def send_application_reminders(
    *, ctx: AuditContext, now: dt.datetime | None = None, dry_run: bool = False
) -> ChangeSet:
    """The one reminder (D-24): `VENDOR_APPLICATION_REMINDER_HOURS` hours after the request, a
    pending application gets a mail with a fresh confirmation link (same version, so the first
    link keeps working) and the cancel link. Exactly one: the marker records it, and the outbox
    idempotency key stops a retry. Audited with the marker's public id only.

    Works in batches of `APPLICATION_BATCH_SIZE`; each application is locked and re-checked
    (still inactive, marker present, still an applicant vendor) in its own transaction, so a
    link is never mailed to an applicant who has just confirmed."""
    now = now or timezone.now()
    cutoff = now - dt.timedelta(hours=settings.VENDOR_APPLICATION_REMINDER_HOURS)
    changes = ChangeSet()
    last = 0
    while ids := _due_reminder_ids(cutoff, last):
        last = ids[-1]
        for pk in ids:
            if dry_run:
                application = VendorApplication.objects.select_related("user").filter(pk=pk).first()
                if (
                    application is None
                    or _pending_application(str(application.user.public_id)) is None
                ):
                    continue
                snapshot: dict[str, Any] | None = application_snapshot(application)
            else:
                snapshot = _remind_one(pk, cutoff, now, ctx=ctx)
            if snapshot is not None:
                changes.update(
                    APPLICATION_MODEL,
                    str(snapshot["user"]),
                    snapshot,
                    {**snapshot, "reminded": True},
                )
    return changes


def _stale_application_ids(cutoff: dt.datetime, after_id: int) -> list[int]:
    return list(
        VendorApplication.objects.filter(
            user__is_active=False, created_at__lt=cutoff, id__gt=after_id
        )
        .order_by("id")
        .values_list("id", flat=True)[:APPLICATION_BATCH_SIZE]
    )


def _purge_one(pk: int, cutoff: dt.datetime) -> dict[str, Any] | None:
    """Delete one unconfirmed application under its locks, after re-checking that it is still
    pending and still past the retention period; returns its snapshot when the account went."""
    with transaction.atomic():
        application = _lock_pending(pk)
        if application is None or application.created_at >= cutoff:
            return None
        snapshot = application_snapshot(application)
        return snapshot if _delete_application(application) else None


def purge_unconfirmed_applications(
    *, ctx: AuditContext, now: dt.datetime | None = None, dry_run: bool = False
) -> ChangeSet:
    """Delete applications nobody confirmed (D-24, GDPR): the account, its applicant vendor,
    memberships and link uses, `VENDOR_APPLICATION_RETENTION_DAYS` days (3) after the request;
    a later link does not extend it. The job runs hourly, so a deletion can lag up to an hour.
    Audited by count only (one entry per batch): no name or address is kept. Accounts that
    confirmed or never applied this way are never touched.

    Works in batches of `APPLICATION_BATCH_SIZE`. Each application is deleted in its own
    transaction, under a lock on the marker and the account, after a re-check (still inactive,
    marker present, still an applicant vendor, still past retention): an applicant who confirms
    between the selection and the deletion is never deleted, and a row being confirmed right
    now is skipped (`skip_locked`) and left for the next run.
    """
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.VENDOR_APPLICATION_RETENTION_DAYS)
    changes = ChangeSet()
    last = 0
    while ids := _stale_application_ids(cutoff, last):
        last = ids[-1]
        deleted = 0
        for pk in ids:
            if dry_run:
                application = VendorApplication.objects.select_related("user").filter(pk=pk).first()
                snapshot = application_snapshot(application) if application else None
            else:
                snapshot = _purge_one(pk, cutoff)
            if snapshot is None:
                continue
            deleted += 1
            changes.delete(APPLICATION_MODEL, str(snapshot["user"]), snapshot)
        if deleted and not dry_run:
            record(
                ctx,
                APPLICATION_PURGED,
                APPLICATION_MODEL,
                "purge",
                None,
                {"deleted": deleted, "retention_days": settings.VENDOR_APPLICATION_RETENTION_DAYS},
            )
    return changes
