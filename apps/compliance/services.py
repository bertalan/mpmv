"""Compliance services. GPSR product data, vendor defaults and the publication block (CMP.3).

Every write is validated, returned as a ChangeSet (dry-run) and audited: GPSR data is a legal
fact shown to consumers and to market surveillance authorities. The publication block is
registered with `apps.catalog.publishing` by `ComplianceConfig.ready()`, so the catalog
publishing services (CAT.10) refuse a product whose GPSR data is incomplete.

Also here: the warnings and safety information per language, the GPSR block of the product page
(`gpsr_display`, rendered by `{% product_safety %}`), and the article 22 safety cases (authority
orders, safety notices) with their working-day deadlines and the overdue alert (log, audit and an
email to the operator's compliance address), and the warnings-language check per destination
country (owner decisions 2026-11-09).
"""

import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import OFFICIAL_LANGUAGES, InvalidCountry
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.core.services import (
    NoPlatformOperator,
    audit_changes,
    current_operator,
    enqueue_email,
    save_audited,
)
from apps.vendors.models import Vendor

from . import engine
from .models import GpsrInfo, GpsrSafetyText, SafetyCase, VendorGpsrDefaults

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

VENDOR_DEFAULTS_CREATED: Final = "compliance.gpsr_defaults.created"
VENDOR_DEFAULTS_UPDATED: Final = "compliance.gpsr_defaults.updated"
PRODUCT_GPSR_CREATED: Final = "compliance.gpsr.created"
PRODUCT_GPSR_UPDATED: Final = "compliance.gpsr.updated"

VENDOR_DEFAULTS_FIELDS: Final = engine.PARTY_FIELDS
PRODUCT_GPSR_FIELDS: Final = (*engine.PARTY_FIELDS, "model_number", "batch")

MISSING_MESSAGES: Final = {
    "manufacturer_name": _("GPSR: the manufacturer's name is missing."),
    "manufacturer_address": _("GPSR: the manufacturer's postal address is missing."),
    "manufacturer_contact": _("GPSR: the manufacturer's email or web address is missing."),
    "manufacturer_country": _("GPSR: the manufacturer's country is missing."),
    engine.PRODUCT_IDENTIFIER: _(
        "GPSR: the product needs an identifier (GTIN, MPN, model or batch number)."
    ),
    "eu_responsible_name": _(
        "GPSR: the manufacturer is outside the EU; the EU responsible person is missing."
    ),
    "eu_responsible_address": _(
        "GPSR: the postal address of the EU responsible person is missing."
    ),
    "eu_responsible_contact": _(
        "GPSR: the email or web address of the EU responsible person is missing."
    ),
}


def vendor_defaults_snapshot(defaults: VendorGpsrDefaults) -> dict[str, Any]:
    return {
        "vendor": str(defaults.vendor.public_id),
        **{name: getattr(defaults, name) for name in VENDOR_DEFAULTS_FIELDS},
    }


def gpsr_snapshot(info: GpsrInfo) -> dict[str, Any]:
    return {
        "product": str(info.product.public_id),
        "vendor": str(info.product.vendor.public_id),
        **{name: getattr(info, name) for name in PRODUCT_GPSR_FIELDS},
    }


def _assign(
    instance: VendorGpsrDefaults | GpsrInfo, data: Mapping[str, Any], allowed: tuple[str, ...]
) -> None:
    """Set `data` on `instance` and validate it; raise ValidationError, also before a dry run."""
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValidationError({name: "This field cannot be set here." for name in unknown})
    for name, value in data.items():
        setattr(instance, name, "" if value is None else str(value).strip())
    errors: dict[str, list[str]] = {}
    for name in engine.CONTACT_FIELDS:
        message = engine.contact_error(getattr(instance, name))
        if message is not None:
            errors.setdefault(name, []).append(message)
    try:
        instance.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        for name, messages in exc.message_dict.items():
            errors.setdefault(name, []).extend(messages)
    if errors:
        raise ValidationError(errors)


def set_vendor_gpsr_defaults(
    vendor: Vendor, data: Mapping[str, Any], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or update `vendor`'s GPSR defaults with the fields in `data` (others are kept).

    Raises ValidationError for unknown fields, an invalid country or a contact that is not an
    email or web address, also on dry-run.
    """
    with transaction.atomic():
        defaults = VendorGpsrDefaults.objects.select_for_update().filter(
            vendor=vendor
        ).first() or VendorGpsrDefaults(vendor=vendor)
        _assign(defaults, data, VENDOR_DEFAULTS_FIELDS)
        return save_audited(
            defaults,
            vendor_defaults_snapshot,
            key=str(vendor.public_id),
            ctx=ctx,
            created=VENDOR_DEFAULTS_CREATED,
            updated=VENDOR_DEFAULTS_UPDATED,
            dry_run=dry_run,
        )


def set_product_gpsr(
    product: Product, data: Mapping[str, Any], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or update `product`'s own GPSR data with the fields in `data` (others are kept).

    Leave a whole block empty to use the vendor defaults for it. Raises ValidationError as
    `set_vendor_gpsr_defaults`, also on dry-run.
    """
    with transaction.atomic():
        info = (
            GpsrInfo.objects.select_for_update()
            .select_related("product__vendor")
            .filter(product=product)
            .first()
        ) or GpsrInfo(product=product)
        _assign(info, data, PRODUCT_GPSR_FIELDS)
        changes = save_audited(
            info,
            gpsr_snapshot,
            key=str(product.public_id),
            ctx=ctx,
            created=PRODUCT_GPSR_CREATED,
            updated=PRODUCT_GPSR_UPDATED,
            dry_run=dry_run,
        )
    if not dry_run:
        product.gpsr = info
    return changes


def _parties(row: VendorGpsrDefaults | GpsrInfo | None) -> engine.GpsrParties | None:
    if row is None:
        return None
    return engine.GpsrParties(**{name: getattr(row, name) for name in engine.PARTY_FIELDS})


def effective_gpsr(product: Product) -> engine.GpsrFacts:
    """The GPSR data `product` would be published with: its own, then its vendor's defaults."""
    info = GpsrInfo.objects.filter(product_id=product.pk).first()
    defaults = VendorGpsrDefaults.objects.filter(vendor_id=product.vendor_id).first()
    identifiers = [product.gtin, product.mpn]
    if info is not None:
        identifiers += [info.model_number, info.batch]
    return engine.GpsrFacts(
        parties=engine.merge_parties(_parties(info), _parties(defaults)),
        identifiers=tuple(identifiers),
    )


def gpsr_publication_blockers(product: Product) -> tuple[str, ...]:
    """Codes of the mandatory GPSR data `product` lacks (`engine.gpsr_missing`)."""
    return engine.gpsr_missing(effective_gpsr(product))


def gpsr_blocking_messages(product: Product) -> list[str]:
    """The publication check registered with `apps.catalog.publishing`."""
    return [str(MISSING_MESSAGES[code]) for code in gpsr_publication_blockers(product)]


def check_gpsr_publishable(product: Product) -> None:
    """Raise ValidationError (field "gpsr", code "gpsr_missing") unless GPSR data is complete."""
    messages = gpsr_blocking_messages(product)
    if messages:
        raise ValidationError(
            {"gpsr": [ValidationError(message, code="gpsr_missing") for message in messages]}
        )


# --- Warnings and safety information, per language (GPSR art. 19(c))

SAFETY_TEXT_CREATED: Final = "compliance.gpsr_text.created"
SAFETY_TEXT_UPDATED: Final = "compliance.gpsr_text.updated"
SAFETY_TEXT_FIELDS: Final = ("warnings", "safety_information")


def safety_text_snapshot(text: GpsrSafetyText) -> dict[str, Any]:
    return {
        "product": str(text.product.public_id),
        "vendor": str(text.product.vendor.public_id),
        "language": text.language,
        **{name: getattr(text, name) for name in SAFETY_TEXT_FIELDS},
    }


def set_product_safety_text(
    product: Product,
    language: str,
    data: Mapping[str, Any],
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Create or update `product`'s warnings and safety information in `language` with the
    fields in `data` (others are kept). Raises ValidationError for an unknown field or a language
    that is not configured, also on dry-run."""
    unknown = sorted(set(data) - set(SAFETY_TEXT_FIELDS))
    if unknown:
        raise ValidationError({name: "This field cannot be set here." for name in unknown})
    with transaction.atomic():
        text = (
            GpsrSafetyText.objects.select_for_update()
            .select_related("product__vendor")
            .filter(product=product, language=language)
            .first()
        ) or GpsrSafetyText(product=product, language=language)
        for name, value in data.items():
            setattr(text, name, "" if value is None else str(value).strip())
        text.full_clean(validate_unique=False, validate_constraints=False)
        return save_audited(
            text,
            safety_text_snapshot,
            key=f"{product.public_id}:{language}",
            ctx=ctx,
            created=SAFETY_TEXT_CREATED,
            updated=SAFETY_TEXT_UPDATED,
            dry_run=dry_run,
        )


# --- What the product page shows (GPSR art. 19; the page is CAT.7)


@dataclass(frozen=True)
class GpsrDisplay:
    """The GPSR block of a product page: who answers for the product, how to identify it, and
    its warnings in `text_language` ("" when it has none)."""

    parties: engine.GpsrParties
    identifiers: tuple[tuple[str, str], ...]
    needs_eu_responsible: bool
    warnings: str = ""
    safety_information: str = ""
    text_language: str = ""


def gpsr_display(product: Product, language: str) -> GpsrDisplay:
    """The GPSR data shown on `product`'s page in `language`: its own data, then its vendor's
    defaults; texts in `language`, else in the primary language."""
    facts = effective_gpsr(product)
    info = GpsrInfo.objects.filter(product_id=product.pk).first()
    labelled = [("GTIN", product.gtin), ("MPN", product.mpn)]
    if info is not None:
        labelled += [("Model", info.model_number), ("Batch", info.batch)]
    texts = {
        row.language: row
        for row in GpsrSafetyText.objects.filter(
            product_id=product.pk, language__in={language, settings.LANGUAGE_CODE}
        )
    }
    text = texts.get(language) or texts.get(settings.LANGUAGE_CODE)
    return GpsrDisplay(
        parties=facts.parties,
        identifiers=tuple((label, value) for label, value in labelled if value.strip()),
        needs_eu_responsible=engine.needs_eu_responsible(facts.parties.manufacturer_country),
        warnings=text.warnings if text else "",
        safety_information=text.safety_information if text else "",
        text_language=text.language if text else "",
    )


# --- GPSR article 22: authority orders and safety notices with working-day deadlines

SAFETY_CASE_OPENED: Final = "compliance.safety_case.opened"
SAFETY_CASE_CLOSED: Final = "compliance.safety_case.closed"
SAFETY_CASE_OVERDUE: Final = "compliance.safety_case.overdue"
SAFETY_CASE_MODEL: Final = "compliance.safetycase"
SAFETY_CASE_FIELDS: Final = (
    "kind",
    "product_sku",
    "reference",
    "authority",
    "summary",
    "received_at",
    "due_on",
    "status",
    "closed_at",
    "resolution",
    "overdue_alerted_at",
)
#: The overdue check runs as a background job (`compliance.flag_overdue_safety_cases`).
SYSTEM_CTX: Final = AuditContext(
    actor_label="task:compliance.flag_overdue_safety_cases", source=AuditLog.Source.TASK
)


def safety_case_snapshot(case: SafetyCase) -> dict[str, Any]:
    return {
        "vendor": str(case.vendor.public_id),
        "product": str(case.product.public_id) if case.product else None,
        **{name: getattr(case, name) for name in SAFETY_CASE_FIELDS},
    }


def public_holidays() -> frozenset[dt.date]:
    """The public holidays the deadlines skip: `settings.COMPLIANCE_PUBLIC_HOLIDAYS` (ISO dates).

    Empty by default: working days are Monday to Friday, which gives the earliest deadline
    (owner decision 2026-11-09). The engine still takes a holiday set for when that changes.
    """
    return frozenset(
        dt.date.fromisoformat(day) for day in getattr(settings, "COMPLIANCE_PUBLIC_HOLIDAYS", ())
    )


def _business_date(moment: dt.datetime) -> dt.date:
    """The calendar day of `moment` in business time (settings.BUSINESS_TIME_ZONE)."""
    return timezone.localtime(moment, ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()


def open_safety_case(
    *,
    vendor: Vendor,
    kind: str,
    received_at: dt.datetime,
    summary: str,
    ctx: AuditContext,
    product: Product | None = None,
    reference: str = "",
    authority: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Record an authority order or a safety notice about `vendor` (and `product`), with its
    deadline: the 2nd (order) or 3rd (notice) working day after the day of receipt in business
    time. Raises ValidationError for another vendor's product or missing data, also on dry-run.
    """
    case = SafetyCase(
        vendor=vendor,
        product=product,
        product_sku=product.sku if product is not None else "",
        kind=kind,
        reference=reference.strip(),
        authority=authority.strip(),
        summary=summary.strip(),
        received_at=received_at,
    )
    if product is not None and product.vendor_id != vendor.pk:
        raise ValidationError({"product": "The product belongs to another vendor."})
    if kind in engine.WORKING_DAYS_TO_ACT:
        case.due_on = engine.safety_case_due_on(
            kind, _business_date(received_at), public_holidays()
        )
    case.full_clean(validate_unique=False)
    changes = ChangeSet()
    changes.create(SAFETY_CASE_MODEL, str(case.public_id), safety_case_snapshot(case))
    if dry_run:
        return changes
    with transaction.atomic():
        case.save()
        audit_changes(changes, ctx=ctx, action=SAFETY_CASE_OPENED)
    return changes


def _locked(case: SafetyCase) -> SafetyCase:
    return (
        SafetyCase.objects.select_for_update(of=("self",))
        .select_related("vendor", "product")
        .get(pk=case.pk)
    )


def close_safety_case(
    case: SafetyCase,
    *,
    resolution: str,
    ctx: AuditContext,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Close an open case with what was done. Raises ValidationError when it is already closed
    or the resolution is empty, also on dry-run."""
    if not resolution.strip():
        raise ValidationError({"resolution": "Say what was done."})
    changes = ChangeSet()
    with transaction.atomic():
        stored = _locked(case)
        if stored.status == SafetyCase.Status.CLOSED:
            raise ValidationError("The case is already closed.")
        before = safety_case_snapshot(stored)
        stored.status = SafetyCase.Status.CLOSED
        stored.closed_at = now or timezone.now()
        stored.resolution = resolution.strip()
        changes.update(
            SAFETY_CASE_MODEL, str(stored.public_id), before, safety_case_snapshot(stored)
        )
        if dry_run:
            return changes
        stored.save(update_fields=["status", "closed_at", "resolution", "updated_at"])
        audit_changes(changes, ctx=ctx, action=SAFETY_CASE_CLOSED)
    case.status, case.closed_at, case.resolution = (
        stored.status,
        stored.closed_at,
        stored.resolution,
    )
    return changes


@dataclass(frozen=True)
class Deadline:
    """A case's deadline as staff see it."""

    due_on: dt.date
    working_days_left: int
    overdue: bool


def safety_case_deadline(case: SafetyCase, *, now: dt.datetime | None = None) -> Deadline:
    today = _business_date(now or timezone.now())
    return Deadline(
        due_on=case.due_on,
        working_days_left=engine.working_days_left(
            case.due_on, today=today, holidays=public_holidays()
        ),
        overdue=engine.is_overdue(
            case.due_on, today=today, closed=case.status == SafetyCase.Status.CLOSED
        ),
    )


def overdue_safety_cases(*, now: dt.datetime | None = None) -> "QuerySet[SafetyCase]":
    """Open cases whose due day has passed (business time)."""
    today = _business_date(now or timezone.now())
    return SafetyCase.objects.filter(status=SafetyCase.Status.OPEN, due_on__lt=today)


OVERDUE_EMAIL_TEMPLATE: Final = "compliance.safety_case_overdue"


def staff_alert_address(at: dt.datetime | None = None) -> str:
    """Where compliance alerts go: the operator's compliance address, else its support address;
    "" when neither is set (or no operator is configured)."""
    try:
        operator = current_operator(at)
    except NoPlatformOperator:
        return ""
    return (operator.compliance_email or operator.support_email).strip()


def overdue_email_context(case: SafetyCase) -> dict[str, Any]:
    """What the overdue alert email shows: the case, never personal data."""
    return {
        "case_id": str(case.public_id),
        "kind": str(case.get_kind_display()),
        "reference": case.reference,
        "authority": case.authority,
        "vendor_name": case.vendor.display_name,
        "product_sku": case.product_sku,
        "received_on": _business_date(case.received_at).isoformat(),
        "due_on": case.due_on.isoformat(),
    }


def _email_overdue(case: SafetyCase, recipient: str) -> None:
    if not recipient:
        logger.warning(
            "GPSR safety case %s is overdue and no compliance or support email address is "
            "configured on the platform operator: no email sent",
            case.public_id,
        )
        return
    enqueue_email(
        recipient=recipient,
        template_key=OVERDUE_EMAIL_TEMPLATE,
        language=settings.LANGUAGE_CODE,
        context=overdue_email_context(case),
        idempotency_key=f"compliance:safety_case:{case.public_id}:overdue",
    )


def flag_overdue_safety_cases(
    *, now: dt.datetime | None = None, ctx: AuditContext = SYSTEM_CTX, dry_run: bool = False
) -> ChangeSet:
    """Alert staff once about each overdue open case: an error in the log (monitoring alerts on
    it), an email to the operator's compliance address (else its support address, through the
    transactional outbox), `overdue_alerted_at` set and an audit entry. A case already flagged
    is left alone. The in-app notice for the Catalog and moderation role is a follow-up (it
    needs the Manager notices)."""
    moment = now or timezone.now()
    changes = ChangeSet()
    recipient = "" if dry_run else staff_alert_address(moment)
    with transaction.atomic():
        cases = (
            overdue_safety_cases(now=moment)
            .filter(overdue_alerted_at__isnull=True)
            .select_for_update(of=("self",))
            .select_related("vendor", "product")
        )
        for case in cases:
            before = safety_case_snapshot(case)
            case.overdue_alerted_at = moment
            key = str(case.public_id)
            changes.update(SAFETY_CASE_MODEL, key, before, safety_case_snapshot(case))
            if dry_run:
                continue
            case.save(update_fields=["overdue_alerted_at", "updated_at"])
            logger.error(
                "GPSR safety case %s (%s, vendor %s) is overdue: due on %s",
                key,
                case.kind,
                case.vendor.public_id,
                case.due_on.isoformat(),
            )
            _email_overdue(case, recipient)
        if not dry_run:
            audit_changes(changes, ctx=ctx, action=SAFETY_CASE_OVERDUE)
    return changes


# --- GPSR article 19(c): warnings in a language of the destination country (owner decision
# 2026-11-09). A product with warnings is not sold into a country until they exist in one of its
# languages; it stays published and on sale for the other countries.

SALE_CHECK_NAME: Final = "gpsr_warnings"


def warning_languages(product: Product) -> frozenset[str]:
    """The languages `product` has non-empty warnings in."""
    return frozenset(
        language
        for language, warnings in GpsrSafetyText.objects.filter(product_id=product.pk).values_list(
            "language", "warnings"
        )
        if warnings.strip()
    )


def warnings_sale_check(product: Product, country: str) -> engine.WarningsSaleCheck:
    """May `product` be sold into `country` as far as its warnings' language goes?
    Raises ValidationError for a value that is not a country code."""
    try:
        return engine.warnings_sale_check(warning_languages(product), country, OFFICIAL_LANGUAGES)
    except InvalidCountry as exc:
        raise ValidationError({"country": str(exc)}) from exc


def warnings_sale_messages(product: Product, country: str) -> list[str]:
    """The sale check registered with `apps.catalog.selling` (the checkout, ORD.3, asks)."""
    check = warnings_sale_check(product, country)
    if check.allowed:
        return []
    return [
        str(
            _(
                "This product cannot be shipped to %(country)s: its safety warnings are not "
                "available in a language of that country (%(languages)s)."
            )
            % {"country": check.country, "languages": ", ".join(check.country_languages)}
        )
    ]


@dataclass(frozen=True)
class WarningsGap:
    """A country `product` is not sold into: its `languages` (warnings in one is enough), and
    those among them the platform offers (`offered`): the vendor can add those right away; the
    others need the platform to add the language first."""

    country: str
    languages: tuple[str, ...]
    offered: tuple[str, ...]


def _gaps(languages: frozenset[str]) -> list[WarningsGap]:
    offered = set(configured_languages())
    return [
        WarningsGap(
            country=country,
            languages=needed,
            offered=tuple(code for code in needed if code in offered),
        )
        for country, needed in engine.warnings_language_gaps(languages, OFFICIAL_LANGUAGES).items()
    ]


def warnings_language_gaps(product: Product) -> list[WarningsGap]:
    """The countries `product` cannot be sold into for lack of warnings in their language."""
    return _gaps(warning_languages(product))


def vendor_warnings_language_gaps(vendor: Vendor) -> list[tuple[Product, list[WarningsGap]]]:
    """Per product of `vendor` with warnings, the countries it is not sold into (Manager shop
    space, CORE.17): only products with at least one gap."""
    by_product: dict[int, set[str]] = {}
    for product_id, language, warnings in GpsrSafetyText.objects.filter(
        product__vendor=vendor
    ).values_list("product_id", "language", "warnings"):
        if warnings.strip():
            by_product.setdefault(product_id, set()).add(language)
    products = Product.objects.filter(pk__in=by_product).order_by("sku")
    rows = []
    for product in products:
        gaps = _gaps(frozenset(by_product[product.pk]))
        if gaps:
            rows.append((product, gaps))
    return rows
