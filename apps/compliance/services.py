"""Compliance services. GPSR product data, vendor defaults and the publication block (CMP.3).

Every write is validated, returned as a ChangeSet (dry-run) and audited: GPSR data is a legal
fact shown to consumers and to market surveillance authorities. The publication block is
registered with `apps.catalog.publishing` by `ComplianceConfig.ready()`, so the catalog
publishing services (CAT.10) refuse a product whose GPSR data is incomplete.

Also here: the warnings and safety information per language, the GPSR block of the product page
(`gpsr_display`, rendered by `{% product_safety %}`), and the article 22 safety cases (authority
orders, safety notices) with their working-day deadlines and the overdue alert (log, audit and an
email to the operator's compliance address), and the warnings-language check per destination
country (owner decisions 2026-11-09) with its staff-edited country -> languages map.

A published product never silently loses its GPSR data: a product edit, a product GPSR write or a
vendor defaults write that would make a published product incomplete is refused, and a nightly
job flags any published product that fails the check anyway
(`flag_published_products_failing_gpsr`).
"""

import datetime as dt
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language_info
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product, ProductStatus
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.countries import OFFICIAL_LANGUAGES, InvalidCountry, normalize_country
from apps.core.flags import TtlCache
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
from .models import (
    CountryWarningLanguages,
    GpsrInfo,
    GpsrSafetyText,
    SafetyCase,
    VendorGpsrDefaults,
)

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
    email or web address, or when a published product that uses the defaults would lose
    mandatory GPSR data, also on dry-run.
    """
    with transaction.atomic():
        stored = VendorGpsrDefaults.objects.select_for_update().filter(vendor=vendor).first()
        defaults = stored or VendorGpsrDefaults(vendor=vendor)
        before = _parties(stored)
        _assign(defaults, data, VENDOR_DEFAULTS_FIELDS)
        _refuse_breaking_published_products(vendor, before, _parties(defaults))
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
    `set_vendor_gpsr_defaults`, and when `product` is published and would lose mandatory GPSR
    data, also on dry-run.
    """
    with transaction.atomic():
        info = (
            GpsrInfo.objects.select_for_update()
            .select_related("product__vendor")
            .filter(product=product)
            .first()
        ) or GpsrInfo(product=product)
        published = Product.objects.filter(pk=product.pk, status=ProductStatus.PUBLISHED).exists()
        if published:
            stored_defaults = VendorGpsrDefaults.objects.filter(vendor_id=product.vendor_id)
            defaults = _parties(stored_defaults.first())
            before = gpsr_facts(product, info if info.pk else None, defaults)
        _assign(info, data, PRODUCT_GPSR_FIELDS)
        if published:
            _refuse_new_gaps(before, gpsr_facts(product, info, defaults))
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


def gpsr_facts(
    product: Product, info: GpsrInfo | None, defaults: engine.GpsrParties | None
) -> engine.GpsrFacts:
    """The GPSR data of `product` with its own row `info` and its vendor's `defaults`."""
    identifiers = [product.gtin, product.mpn]
    if info is not None:
        identifiers += [info.model_number, info.batch]
    return engine.GpsrFacts(
        parties=engine.merge_parties(_parties(info), defaults), identifiers=tuple(identifiers)
    )


def effective_gpsr(product: Product) -> engine.GpsrFacts:
    """The GPSR data `product` would be published with: its own, then its vendor's defaults."""
    info = GpsrInfo.objects.filter(product_id=product.pk).first()
    defaults = VendorGpsrDefaults.objects.filter(vendor_id=product.vendor_id).first()
    return gpsr_facts(product, info, _parties(defaults))


PUBLISHED_LOSES_GPSR: Final = _(
    "This product is published: the change would remove mandatory GPSR data. Unpublish it "
    "first or keep the data complete."
)
MAX_SKUS_IN_MESSAGE: Final = 20


def _new_gaps(before: engine.GpsrFacts, after: engine.GpsrFacts) -> tuple[str, ...]:
    """The mandatory GPSR data `after` lacks that `before` had."""
    had = set(engine.gpsr_missing(before))
    return tuple(code for code in engine.gpsr_missing(after) if code not in had)


def _refuse_new_gaps(before: engine.GpsrFacts, after: engine.GpsrFacts) -> None:
    gaps = _new_gaps(before, after)
    if gaps:
        raise ValidationError(
            {
                "gpsr": [
                    ValidationError(str(PUBLISHED_LOSES_GPSR), code="gpsr_published"),
                    *(ValidationError(str(MISSING_MESSAGES[code])) for code in gaps),
                ]
            }
        )


def _refuse_breaking_published_products(
    vendor: Vendor, before: engine.GpsrParties | None, after: engine.GpsrParties | None
) -> None:
    """Refuse new vendor defaults that would leave a published product of `vendor` without
    mandatory GPSR data it has now (products already incomplete are the nightly check's)."""
    products = list(Product.objects.filter(vendor=vendor, status=ProductStatus.PUBLISHED))
    infos = {info.product_id: info for info in GpsrInfo.objects.filter(product__in=products)}
    broken = sorted(
        product.sku
        for product in products
        if _new_gaps(
            gpsr_facts(product, infos.get(product.pk), before),
            gpsr_facts(product, infos.get(product.pk), after),
        )
    )
    if broken:
        shown = ", ".join(broken[:MAX_SKUS_IN_MESSAGE])
        more = len(broken) - MAX_SKUS_IN_MESSAGE
        raise ValidationError(
            {
                "gpsr": _(
                    "These defaults would remove mandatory GPSR data from published products "
                    "that use them: %(skus)s%(more)s. Complete those products' own GPSR data or "
                    "unpublish them first."
                )
                % {"skus": shown, "more": f" (+{more})" if more > 0 else ""}
            }
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
    its warnings in `text_language` ("" when it has none), named `text_language_name`."""

    parties: engine.GpsrParties
    identifiers: tuple[tuple[str, str], ...]
    needs_eu_responsible: bool
    warnings: str = ""
    safety_information: str = ""
    text_language: str = ""
    text_language_name: str = ""


def _language_name(code: str) -> str:
    try:
        return str(get_language_info(code)["name_local"])
    except KeyError:
        return code


def display_language(
    available: frozenset[str] | set[str], language: str, accepted: tuple[str, ...] = ()
) -> str:
    """Which of the `available` text languages the page shows: `language`, else one the
    destination accepts (`accepted`, in order), else the primary language, else the first
    available one; "" when there is none. The warnings always show when they exist (art. 19(c))."""
    for code in (language, *accepted, settings.LANGUAGE_CODE):
        if code in available:
            return code
    return min(available, default="")


def gpsr_display(product: Product, language: str, country: str = "") -> GpsrDisplay:
    """The GPSR data shown on `product`'s page in `language` for a visitor shipping to
    `country` (optional): its own data, then its vendor's defaults; texts as `display_language`
    picks them."""
    facts = effective_gpsr(product)
    info = GpsrInfo.objects.filter(product_id=product.pk).first()
    labelled = [("GTIN", product.gtin), ("MPN", product.mpn)]
    if info is not None:
        labelled += [("Model", info.model_number), ("Batch", info.batch)]
    texts = {
        row.language: row
        for row in GpsrSafetyText.objects.filter(product_id=product.pk)
        if row.warnings.strip() or row.safety_information.strip()
    }
    accepted: tuple[str, ...] = ()
    if country:
        try:
            accepted = country_warning_languages().get(normalize_country(country), ())
        except InvalidCountry:
            accepted = ()
    text = texts.get(display_language(set(texts), language, accepted))
    return GpsrDisplay(
        parties=facts.parties,
        identifiers=tuple((label, value) for label, value in labelled if value.strip()),
        needs_eu_responsible=engine.needs_eu_responsible(facts.parties.manufacturer_country),
        warnings=text.warnings if text else "",
        safety_information=text.safety_information if text else "",
        text_language=text.language if text else "",
        text_language_name=_language_name(text.language) if text else "",
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


#: How far in the future a receipt time may be (clocks of different machines).
RECEIPT_CLOCK_SKEW: Final = dt.timedelta(minutes=5)


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
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Record an authority order or a safety notice about `vendor` (and `product`), with its
    deadline: the 2nd (order) or 3rd (notice) working day after the day of receipt in business
    time. Raises ValidationError for another vendor's product, a receipt later than `now` (plus
    `RECEIPT_CLOCK_SKEW`: a future receipt would push the deadline out) or missing data, also on
    dry-run.
    """
    if received_at > (now or timezone.now()) + RECEIPT_CLOCK_SKEW:
        raise ValidationError({"received_at": "The receipt cannot be in the future."})
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
    is left alone. With no compliance or support address configured the case is still flagged
    (the log error is the alert): setting an address later does not resend it. The in-app
    notice for the Catalog and moderation role is a follow-up (it needs the Manager notices)."""
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

COUNTRY_LANGUAGES_MODEL: Final = "compliance.countrywarninglanguages"
COUNTRY_LANGUAGES_CREATED: Final = "compliance.country_languages.created"
COUNTRY_LANGUAGES_UPDATED: Final = "compliance.country_languages.updated"
_LANGUAGE_CODE: Final = re.compile(r"[a-z]{2,3}")

CountryLanguages = Mapping[str, tuple[str, ...]]
# This process's copy of the map, kept COMPLIANCE_COUNTRY_LANGUAGES_CACHE_SECONDS (0: no cache).
COUNTRY_LANGUAGES_CACHE: TtlCache[CountryLanguages] = TtlCache()


def load_country_warning_languages() -> CountryLanguages:
    """The stored map, over the official languages for a country without a row."""
    stored = {
        country: tuple(languages)
        for country, languages in CountryWarningLanguages.objects.values_list(
            "country", "languages"
        )
    }
    return {**OFFICIAL_LANGUAGES, **stored}


def country_warning_languages() -> CountryLanguages:
    """Country -> the languages its consumers accept warnings in (one is enough). Staff edit it
    (`set_country_warning_languages`); cached per process."""
    return COUNTRY_LANGUAGES_CACHE.get(
        load_country_warning_languages,
        ttl=getattr(settings, "COMPLIANCE_COUNTRY_LANGUAGES_CACHE_SECONDS", 0),
    )


def country_languages_changed_now() -> None:
    """Drop this process's cached map now (tests; the services wait for the commit)."""
    COUNTRY_LANGUAGES_CACHE.clear()


def country_languages_snapshot(row: CountryWarningLanguages) -> dict[str, Any]:
    return {"country": row.country, "languages": list(row.languages)}


def clean_languages(raw: Iterable[str]) -> list[str]:
    """ISO 639-1 codes, lower case, in the given order, without duplicates. Raises
    ValidationError for an empty list or a value that is not a language code."""
    languages: list[str] = []
    for value in raw:
        code = str(value).strip().lower()
        if not _LANGUAGE_CODE.fullmatch(code):
            raise ValidationError({"languages": f"{value!r} is not an ISO 639-1 language code."})
        if code not in languages:
            languages.append(code)
    if not languages:
        raise ValidationError({"languages": "Give at least one language."})
    return languages


def set_country_warning_languages(
    country: str, languages: Iterable[str], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Set the languages `country` accepts warnings in (staff only; audited). Warnings in any
    one of them unblock sale into `country`. Raises ValidationError for an invalid country or
    language list, also on dry-run."""
    try:
        code = normalize_country(country)
    except InvalidCountry as exc:
        raise ValidationError({"country": str(exc)}) from exc
    cleaned = clean_languages(languages)
    with transaction.atomic():
        row = CountryWarningLanguages.objects.select_for_update().filter(
            country=code
        ).first() or CountryWarningLanguages(country=code)
        row.languages = cleaned
        row.full_clean(validate_unique=False, validate_constraints=False)
        changes = save_audited(
            row,
            country_languages_snapshot,
            key=code,
            ctx=ctx,
            created=COUNTRY_LANGUAGES_CREATED,
            updated=COUNTRY_LANGUAGES_UPDATED,
            dry_run=dry_run,
        )
        if not dry_run:
            transaction.on_commit(COUNTRY_LANGUAGES_CACHE.clear)
    return changes


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
        return engine.warnings_sale_check(
            warning_languages(product), country, country_warning_languages()
        )
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
        for country, needed in engine.warnings_language_gaps(
            languages, country_warning_languages()
        ).items()
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


# --- Nightly consistency check: no published product without complete GPSR data


PUBLISHED_INCOMPLETE_EMAIL_TEMPLATE: Final = "compliance.gpsr_published_incomplete"
MAX_PRODUCTS_IN_EMAIL: Final = 200


def published_products_failing_gpsr() -> list[tuple[Product, tuple[str, ...]]]:
    """Every published product whose GPSR data is incomplete, with the missing codes. The
    publication gate and the write guards keep this empty; products published another way
    (an import that bypassed `save_product`, a raw write) show up here."""
    products = list(
        Product.objects.filter(status=ProductStatus.PUBLISHED)
        .select_related("vendor")
        .order_by("vendor_id", "sku")
    )
    infos = {info.product_id: info for info in GpsrInfo.objects.filter(product__in=products)}
    defaults = {
        row.vendor_id: _parties(row)
        for row in VendorGpsrDefaults.objects.filter(
            vendor_id__in={product.vendor_id for product in products}
        )
    }
    rows = []
    for product in products:
        missing = engine.gpsr_missing(
            gpsr_facts(product, infos.get(product.pk), defaults.get(product.vendor_id))
        )
        if missing:
            rows.append((product, missing))
    return rows


def published_incomplete_email_context(
    rows: list[tuple[Product, tuple[str, ...]]],
) -> dict[str, Any]:
    return {
        "count": len(rows),
        "products": [
            {
                "sku": product.sku,
                "vendor_name": product.vendor.display_name,
                "missing": ", ".join(missing),
            }
            for product, missing in rows[:MAX_PRODUCTS_IN_EMAIL]
        ],
        "more": max(0, len(rows) - MAX_PRODUCTS_IN_EMAIL),
    }


def flag_published_products_failing_gpsr(
    *, now: dt.datetime | None = None, dry_run: bool = False
) -> list[tuple[Product, tuple[str, ...]]]:
    """List the published products failing the GPSR check: an error in the log per product
    (monitoring alerts on it) and one email a day to the staff alert address. Nothing is
    unpublished automatically: staff decide. Dry-run only lists them."""
    rows = published_products_failing_gpsr()
    if dry_run or not rows:
        return rows
    moment = now or timezone.now()
    for product, missing in rows:
        logger.error(
            "Published product %s (vendor %s) fails the GPSR check: missing %s",
            product.sku,
            product.vendor.public_id,
            ", ".join(missing),
        )
    recipient = staff_alert_address(moment)
    if not recipient:
        logger.warning(
            "%d published product(s) fail the GPSR check and no compliance or support email "
            "address is configured on the platform operator: no email sent",
            len(rows),
        )
        return rows
    enqueue_email(
        recipient=recipient,
        template_key=PUBLISHED_INCOMPLETE_EMAIL_TEMPLATE,
        language=settings.LANGUAGE_CODE,
        context=published_incomplete_email_context(rows),
        idempotency_key=f"compliance:gpsr_published_incomplete:{_business_date(moment).isoformat()}",
    )
    return rows
