"""Compliance services. GPSR product data, vendor defaults and the publication block (CMP.3).

Every write is validated, returned as a ChangeSet (dry-run) and audited: GPSR data is a legal
fact shown to consumers and to market surveillance authorities. The publication block is
registered with `apps.catalog.publishing` by `ComplianceConfig.ready()`, so the catalog
publishing services (CAT.10) refuse a product whose GPSR data is incomplete.
"""

from collections.abc import Mapping
from typing import Any, Final

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import save_audited
from apps.vendors.models import Vendor

from . import engine
from .models import GpsrInfo, VendorGpsrDefaults

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
