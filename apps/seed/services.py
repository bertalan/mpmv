"""The synthetic demo seed (ARC.10): build the data set of `apps.seed.data` through the services.

`seed` creates what is missing and changes nothing that exists, so a second run is a no-op; it
goes through the apps' services (application, contract acceptance, activation, tax profile,
product creation, shipping import...), so the seed exercises the same code paths and writes the
same audit entries as staff and vendors would. Where an app has no service yet (taxonomy terms,
translations, CMS pages, event pages), the rows are written as the admin would write them.

`reset` takes the seed out again, and only the seed: rows are found by the stable keys of
`apps.seed.data`. Rows the database keeps as legal evidence cannot be deleted (VEN.5: contract
acceptances and an accepted contract; legal page versions; SEC.4: the audit log), so a
seed vendor is closed instead (its storefront disappears) and a seed legal page is unpublished;
the next `seed` reopens and republishes them.

Accounts: vendor owners and the seed operator (the staff identity that approves tax profiles and
reopens vendors) get unusable passwords and `example.org` addresses; the operator is active only
while a seed runs. No account the seed creates can log in.
"""

import dataclasses
import datetime as dt
import io
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.core.files.images import ImageFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from wagtail.models import Locale

from apps.accounts.models import User
from apps.catalog import engine as catalog_engine
from apps.catalog import services as catalog_services
from apps.catalog.attributes import (
    Attribute,
    AttributeTranslation,
    AttributeValue,
    AttributeValueTranslation,
)
from apps.catalog.models import Product, ProductAttribute, VariantOption
from apps.catalog.taxonomy import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
)
from apps.cms.models import HomePage, LegalPage, StandardPage
from apps.cms.seed import seed_text
from apps.cms.services import seed_site
from apps.compliance import services as compliance_services
from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.models import ImageLicence, MediaImage, PlatformOperator, TranslationModel
from apps.core.services import (
    NoPlatformOperator,
    configure_platform_operator,
    current_operator,
    save_image,
)
from apps.core.text import slugify
from apps.events.models import EventIndexPage, EventPage
from apps.events.services import ensure_event_indexes
from apps.shipping import config_format as shipping_format
from apps.shipping.config_io import import_config
from apps.shipping.models import (
    Carrier,
    CarrierTranslation,
    PickupOption,
    ShippingMethod,
    ShippingZone,
)
from apps.shipping.services import delete_config, save_config
from apps.tax import services as tax_services
from apps.tax.models import TaxRate, VendorTaxProfile
from apps.vendors import engine as vendors_engine
from apps.vendors import services as vendors_services
from apps.vendors.models import (
    ContractAcceptance,
    Vendor,
    VendorContract,
    VendorContractTranslation,
    VendorMember,
    VendorTranslation,
)

from . import data
from .images import placeholder_webp

PUBLISHED = translations.PUBLISHED
SOURCE_LANGUAGE = "en"
SOLE_PROPRIETOR = vendors_engine.SOLE_PROPRIETORSHIP
# The audit context of the `demo_seed` test fixture (root conftest); the command has its own.
TEST_CONTEXT = AuditContext(actor_label="test:demo_seed", source="system")
OWN_WORK_LICENCE = "own-work"  # seeded by core migration 0013
OPERATOR_PERMISSIONS = (
    tax_services.REVIEW_PERMISSION,
    vendors_services.REOPEN_PERMISSION,
    "vendors.view_all_vendors",  # CORE.8: platform staff is a permission, not is_staff
)


@dataclasses.dataclass
class _Run:
    """What one seed or reset run needs: who acts, and the ChangeSet it fills."""

    ctx: AuditContext
    changes: ChangeSet = dataclasses.field(default_factory=ChangeSet)
    operator: User | None = None
    files: list[str] = dataclasses.field(default_factory=list)  # image files written

    def add(self, changes: ChangeSet) -> None:
        self.changes.changes.extend(changes.changes)

    def created(self, model: str, key: str, **after: Any) -> None:
        self.changes.create(model, key, after)


# --- Where the seed may run (never on LIVE)

ALLOW_ENV = "MPMV_ALLOW_SEED"  # read into settings.ALLOW_SEED (base settings)


class SeedNotAllowed(ImproperlyConfigured):
    """The seed was asked to run where it must not: LIVE, or STAGE without the opt-in."""


def seed_allowed() -> bool:
    """`settings.ALLOW_SEED` (dev, test, STAGE with MPMV_ALLOW_SEED=1), never with `LIVE_DATA`."""
    return bool(getattr(settings, "ALLOW_SEED", False)) and not getattr(
        settings, "LIVE_DATA", False
    )


def check_seed_allowed() -> None:
    if not seed_allowed():
        raise SeedNotAllowed(
            "The demo seed is not allowed here: it runs with the development or test settings, "
            f"or on STAGE with {ALLOW_ENV}=1. It never runs on LIVE (LIVE_DATA)."
        )


class SeedConflict(ValueError):
    """A row with one of the seed's keys exists but was not made by the seed: never adopted."""


# --- Public entry points


def seed(*, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create what is missing of the demo data set; return everything created or changed.

    Idempotent: a second run returns an empty ChangeSet and writes nothing. A dry run does the
    whole seed in a transaction and rolls it back (placeholder image files are removed too).
    """
    check_seed_allowed()
    run = _Run(ctx=ctx)
    with _rolled_back_if(dry_run, run), _operator_session(run):
        _platform(run)
        contract = _contract(run)
        _tax_rates(run)
        catalog = _catalog_structure(run)
        vendors = [_vendor(run, index, spec, contract) for index, spec in enumerate(data.VENDORS)]
        _carriers(run)
        for vendor, spec in zip(vendors, data.VENDORS, strict=True):
            _products(run, vendor, spec, catalog)
            _shipping(run, vendor, spec)
        images = _images(run)
        _cms(run, images, vendors)
        _events(run, images, vendors)
    return run.changes


def reset(*, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Take the seed out again, never touching a row the seed does not own (module docstring)."""
    check_seed_allowed()
    run = _Run(ctx=ctx)
    with _rolled_back_if(dry_run, run), _operator_session(run):
        vendors = seed_vendors()
        _reset_events(run, vendors)
        _reset_cms(run)
        for vendor in vendors:
            _reset_vendor(run, vendor)
        _reset_catalog_structure(run)
        _reset_carriers(run)
    return run.changes


def seed_vendors() -> "list[Vendor]":
    """The seed's vendors that exist, in the order of `apps.seed.data.VENDORS`: the slug and the
    seed owner's membership together, so a real vendor with a similar slug is never one."""
    by_slug = {v.slug: v for v in Vendor.objects.filter(slug__in=[s.slug for s in data.VENDORS])}
    return [
        by_slug[spec.slug]
        for spec in data.VENDORS
        if spec.slug in by_slug and _owned_by_seed(by_slug[spec.slug], spec)
    ]


def _owned_by_seed(vendor: Vendor, spec: data.VendorSeed) -> bool:
    return VendorMember.objects.filter(vendor=vendor, user__email__iexact=spec.owner_email).exists()


# --- Run helpers


@contextmanager
def _rolled_back_if(dry_run: bool, run: _Run) -> Iterator[None]:
    """One transaction for the whole run; a dry run rolls it back and deletes its image files."""
    with transaction.atomic():
        yield
        if dry_run:
            transaction.set_rollback(True)
    if dry_run:
        storage = MediaImage._meta.get_field("file").storage
        for name in run.files:
            storage.delete(name)


@contextmanager
def _operator_session(run: _Run) -> Iterator[None]:
    """The seed operator, active and holding its permissions for the duration of the run only."""
    user = User.objects.filter(email__iexact=data.OPERATOR_EMAIL).first()
    if user is None:
        user = User.objects.create_user(
            data.OPERATOR_EMAIL, password=None, full_name="Seed operator (demo)", is_staff=True
        )
        run.created("accounts.user", data.OPERATOR_EMAIL, is_staff=True)
    for perm in OPERATOR_PERMISSIONS:
        app_label, codename = perm.split(".")
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        user.user_permissions.add(permission)
    User.objects.filter(pk=user.pk).update(is_active=True, is_staff=True)
    run.operator = User.objects.get(pk=user.pk)  # fresh permission cache
    try:
        yield
    finally:
        User.objects.filter(pk=user.pk).update(is_active=False)


def _operator(run: _Run) -> User:
    if run.operator is None:  # pragma: no cover - set by _operator_session
        raise RuntimeError("the seed operator is set for the whole run")
    return run.operator


def _publish_texts[T: TranslationModel](
    rows: Iterable[T], make: Callable[[str], T | None], languages: Iterable[str]
) -> list[T]:
    """Create the missing published texts of an entity, the source language first."""
    existing = {row.language: row for row in rows}
    created: list[T] = []
    ordered = sorted(set(languages), key=lambda code: (code != SOURCE_LANGUAGE, code))
    for language in ordered:
        if language in existing:
            continue
        row = make(language)
        if row is None:
            continue
        row.status = PUBLISHED
        row.full_clean()
        row.save()
        created.append(row)
    return created


def _publish_fields(
    rows: Iterable[TranslationModel],
    model: type[TranslationModel],
    parent: dict[str, Any],
    texts: dict[str, data.Texts],
    languages: Iterable[str],
) -> list[TranslationModel]:
    """`_publish_texts` for rows whose fields are `texts` (field name -> text by language)."""

    def make(language: str) -> TranslationModel:
        values = {name: _text(by_language, language) for name, by_language in texts.items()}
        return model(**parent, language=language, **values)

    return _publish_texts(rows, make, languages)


def _text(texts: data.Texts, language: str) -> str:
    return texts.get(language) or texts[SOURCE_LANGUAGE]


# --- Platform: operator, contract, VAT rates


def _platform(run: _Run) -> None:
    try:
        current_operator()
    except NoPlatformOperator:
        operator = PlatformOperator(
            legal_name=data.OPERATOR_NAME,
            country=data.ITALY,
            vat_number=f"{data.ITALY}{_italian_vat(900)}",
            tax_regime=PlatformOperator.TaxRegime.STANDARD,
            commission_vat_mode=PlatformOperator.CommissionVatMode.STANDARD,
            einvoice_channel=PlatformOperator.EInvoiceChannel.SDI,
            dac7_authority_country=data.ITALY,
            valid_from=data.SEED_EPOCH,
        )
        run.add(configure_platform_operator(operator, ctx=run.ctx))


def _contract(run: _Run) -> VendorContract:
    contract = vendors_services.current_contract()
    if contract is None:
        latest = VendorContract.objects.order_by("-version").first()
        contract = VendorContract(
            valid_from=data.SEED_EPOCH if latest is None else timezone.now(),
            note=data.CONTRACT_NOTE,
        )
        run.add(vendors_services.save_contract(contract, ctx=run.ctx))
    if contract.note == data.CONTRACT_NOTE and not contract.is_accepted:

        def text(language: str) -> VendorContractTranslation:
            title, body = data.CONTRACT_TEXTS[language]
            return VendorContractTranslation(
                contract=contract, language=language, title=title, body=body
            )

        for row in _publish_texts(contract.translations.all(), text, data.CONTRACT_TEXTS):
            run.created("vendors.vendorcontracttranslation", f"v{contract.version}:{row.language}")
    return contract


def _tax_rates(run: _Run) -> None:
    for country, rates in data.TAX_RATES.items():
        for category, rate in rates.items():
            if TaxRate.objects.filter(country=country, category=category).exists():
                continue
            new = TaxRate(
                country=country,
                category=category,
                rate=rate,
                source=TaxRate.Source.MANUAL,
                valid_from=data.SEED_EPOCH,
            )
            run.add(tax_services.configure_tax_rate(new, ctx=run.ctx))


# --- Catalog structure: attributes, categories, a landing taxonomy, brands


@dataclasses.dataclass
class _Catalog:
    categories: dict[str, Category]
    terms: dict[str, Term]
    brands: dict[str, Brand]


def _catalog_structure(run: _Run) -> _Catalog:
    languages = configured_languages()
    for code, (names, values) in data.ATTRIBUTES.items():
        attribute, made = Attribute.objects.get_or_create(
            code=code, defaults={"kind": Attribute.Kind.CHOICE}
        )
        if made:
            run.created("catalog.attribute", code)
        _publish_fields(
            attribute.translations.all(),
            AttributeTranslation,
            {"attribute": attribute},
            {"name": names},
            [lang for lang in languages if lang in names or lang == SOURCE_LANGUAGE],
        )
        for position, (value_code, labels) in enumerate(values):
            value, made = AttributeValue.objects.get_or_create(
                attribute=attribute, value=value_code, defaults={"position": position}
            )
            if made:
                run.created("catalog.attributevalue", f"{code}:{value_code}")
            _publish_fields(
                value.translations.all(),
                AttributeValueTranslation,
                {"value": value},
                {"label": labels},
                [lang for lang in languages if lang in labels or lang == SOURCE_LANGUAGE],
            )

    categories: dict[str, Category] = {}
    for node in data.CATEGORIES:
        root = _tree_node(run, Category, CategoryTranslation, "category", node, None, languages)
        categories[node.key] = root
        for child in node.children:
            categories[child.key] = _tree_node(
                run, Category, CategoryTranslation, "category", child, root, languages
            )

    taxonomy, made = Taxonomy.objects.get_or_create(
        code=data.TAXONOMY_CODE, defaults={"has_landing": True, "filterable": True}
    )
    if made:
        run.created("catalog.taxonomy", data.TAXONOMY_CODE)
    _publish_fields(
        taxonomy.translations.all(),
        TaxonomyTranslation,
        {"taxonomy": taxonomy},
        {"name": data.TAXONOMY_NAMES, "slug": data.TAXONOMY_SLUGS},
        languages,
    )
    terms = {
        node.key: _tree_node(run, Term, TermTranslation, "term", node, None, languages, taxonomy)
        for node in data.TERMS
    }

    brands: dict[str, Brand] = {}
    for name, description in data.BRANDS.items():
        brand = Brand.objects.filter(name__iexact=name).first()
        if brand is None:
            brand = Brand(name=name)
            brand.full_clean()
            brand.save()
            run.created("catalog.brand", name)
        brands[name] = brand
        _publish_fields(
            brand.translations.all(),
            BrandTranslation,
            {"brand": brand},
            {
                "slug": {SOURCE_LANGUAGE: slugify(name)},
                "description": {lang: f"<p>{text}</p>" for lang, text in description.items()},
            },
            description,
        )
    return _Catalog(categories=categories, terms=terms, brands=brands)


def _tree_node(
    run: _Run,
    model: Any,
    text_model: Any,
    owner: str,
    node: data.Node,
    parent: Any,
    languages: list[str],
    taxonomy: Taxonomy | None = None,
) -> Any:
    """The category or term `node` under `parent`, found by its EN slug or created."""
    siblings = model.get_root_nodes() if parent is None else parent.get_children()
    if taxonomy is not None:
        siblings = siblings.filter(taxonomy=taxonomy)
    found = siblings.filter(
        translations__language=SOURCE_LANGUAGE, translations__slug=node.key
    ).first()
    if found is None:
        fields = {"taxonomy": taxonomy} if taxonomy is not None else {}
        found = model.add_root(**fields) if parent is None else parent.add_child(**fields)
        run.created(f"catalog.{owner}", node.key)
    _publish_fields(
        found.translations.all(),
        text_model,
        {owner: found},
        {"name": node.names, "slug": node.slugs},
        languages,
    )
    return found


def _seed_categories() -> list[Category]:
    """The seed's categories that exist, children before their parents: its prefixed top-level
    categories, and the subcategories with a seed key under them (never a real category)."""
    found: list[Category] = []
    for node in data.CATEGORIES:
        root = _child_with_key(Category.get_root_nodes(), node.key)
        if root is None:
            continue
        for child in node.children:
            sub = _child_with_key(root.get_children(), child.key)
            if sub is not None:
                found.append(sub)
        found.append(root)
    return found


def _child_with_key(nodes: Any, key: str) -> Any:
    return nodes.filter(translations__language=SOURCE_LANGUAGE, translations__slug=key).first()


def _reset_catalog_structure(run: _Run) -> None:
    """Delete the seed's classification rows that no remaining product uses."""
    for category in _seed_categories():
        in_use = Product.objects.filter(
            Q(category__path__startswith=category.path)
            | Q(secondary_categories__path__startswith=category.path)
        ).exists()
        if in_use or category.get_children().exists():
            continue
        run.changes.delete("catalog.category", category.path, {})
        category.delete()
    taxonomy = Taxonomy.objects.filter(code=data.TAXONOMY_CODE).first()
    if taxonomy is not None:
        terms = Term.objects.filter(taxonomy=taxonomy)
        if not Product.objects.filter(terms__in=terms).exists():
            for term in terms.order_by("-depth"):
                term.delete()
            run.changes.delete("catalog.taxonomy", data.TAXONOMY_CODE, {})
            taxonomy.delete()
    for brand in Brand.objects.filter(name__in=list(data.BRANDS)):
        if not Product.objects.filter(brand=brand).exists():
            run.changes.delete("catalog.brand", brand.name, {})
            brand.delete()
    for attribute in Attribute.objects.filter(code__in=list(data.ATTRIBUTES)):
        if not _attribute_in_use(attribute):
            run.changes.delete("catalog.attribute", attribute.code, {})
            AttributeValue.objects.filter(attribute=attribute).delete()
            attribute.delete()


def _attribute_in_use(attribute: Attribute) -> bool:
    return (
        ProductAttribute.objects.filter(attribute=attribute).exists()
        or VariantOption.objects.filter(attribute=attribute).exists()
    )


# --- Vendors: application, contract, activation, tax profile, storefront texts


def _italian_vat(n: int) -> str:
    """A checksum-valid, synthetic Italian VAT number (11 digits)."""
    first_ten = f"{8_000_000 + n:07d}" + f"{n % 100 + 1:03d}"
    return first_ten + vendors_engine.italian_vat_check_digit(first_ten)


def _codice_fiscale(n: int) -> str:
    """A checksum-valid, synthetic personal codice fiscale."""
    first_fifteen = f"SMPBNC80A{41 + n:02d}Z{100 + n:03d}"
    return first_fifteen + vendors_engine.codice_fiscale_check_char(first_fifteen)


def _iban(country: str, bban: str) -> str:
    return country + vendors_engine.iban_check_digits(country, bban) + bban


def _identity(index: int, spec: data.VendorSeed) -> dict[str, Any]:
    """The legal identity fields of the vendor, with synthetic identifiers."""
    values: dict[str, Any] = {
        "legal_form": spec.legal_form,
        "fiscal_regime": spec.fiscal_regime,
        **spec.identity,
    }
    if spec.share_capital is not None:
        values["share_capital"] = spec.share_capital
    # DAC7 (VEN.23): the TIN's issuing country, and a registration number or a date of birth.
    values["tin_country"] = spec.country
    if spec.legal_form == SOLE_PROPRIETOR:
        # The date the synthetic codice fiscale encodes (`_codice_fiscale(index + 1)`).
        values["date_of_birth"] = dt.date(1980, 1, index + 2)
    else:
        values["business_registration_number"] = f"DEMO-{spec.letter}-{100_001 + index}"
    if spec.country == data.ITALY:
        vat = _italian_vat(index + 1)
        values.update(
            vat_number=vat,
            tax_code=_codice_fiscale(index + 1) if spec.legal_form == SOLE_PROPRIETOR else vat,
            tax_residence_country=data.ITALY,
            registered_country=data.ITALY,
            iban=_iban(data.ITALY, f"X0300003200{index + 1:012d}"),
            pec=f"{spec.slug}@pec.{data.EMAIL_DOMAIN}",
        )
        if spec.legal_form == SOLE_PROPRIETOR:
            values.update(
                legal_name=f"{spec.identity['first_name']} {spec.identity['last_name']}",
                sdi_recipient_code=vendors_engine.SDI_PEC_CODE,
            )
        else:
            values.update(
                sdi_recipient_code=f"SDI{index + 1:04d}",
                sole_shareholder=vendors_engine.SEVERAL_SHAREHOLDERS,
                liquidation_status=vendors_engine.NOT_IN_LIQUIDATION,
            )
    elif spec.country == "DE":
        values.update(
            vat_number=f"DE{300_000_000 + index:09d}",
            tax_code=f"{86_000_000_000 + index:011d}",  # an 11-digit Steuer-IdNr shape
            eori=f"DE{index + 1:013d}",
            iban=_iban("DE", f"10000000{index + 1:010d}"),
        )
    elif spec.country == "FR":
        siren = f"{700_000_000 + index:09d}"
        values.update(
            vat_number=f"FR12{siren}",
            tax_code=siren,
            cross_border_franchise_number=f"FR12{siren}EX",
            iban=_iban("FR", f"30004000031234567890{index + 1:03d}"),
        )
    return values


def _user(email: str, full_name: str, run: _Run) -> User:
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User.objects.create_user(email, password=None, full_name=full_name)
        run.created("accounts.user", email)
    return user


def _vendor(run: _Run, index: int, spec: data.VendorSeed, contract: VendorContract) -> Vendor:
    vendor = Vendor.objects.filter(slug=spec.slug).first()
    if vendor is not None and not _owned_by_seed(vendor, spec):
        raise SeedConflict(f"vendor {spec.slug!r} exists and is not the seed's: rename it first")
    owner = _user(spec.owner_email, f"{spec.display_name} owner (demo)", run)
    if vendor is None:
        identity = _identity(index, spec)
        vendor = Vendor(
            display_name=spec.display_name,
            legal_name=identity.pop("legal_name", spec.legal_name),
            slug=spec.slug,
            country=spec.country,
            contact_email=spec.contact_email,
            enquiries_enabled=spec.enquiries,
            **identity,
        )
        run.add(vendors_services.submit_application(vendor, owner, ctx=run.ctx))
    if vendor.status == vendors_engine.CLOSED:
        run.add(
            vendors_services.reopen_vendor(
                vendor, actor=_operator(run), reason=data.REOPEN_REASON, ctx=run.ctx
            )
        )
        vendor.refresh_from_db()
    if vendor.status == vendors_engine.APPLICANT:
        run.add(vendors_services.change_status(vendor, vendors_engine.ONBOARDING, ctx=run.ctx))
    if vendor.status == vendors_engine.ONBOARDING:
        accepted = ContractAcceptance.objects.filter(
            vendor=vendor, contract=contract, onboarding_round=vendor.onboarding_round
        ).exists()
        if not accepted:
            offer = vendors_services.contract_offer(SOURCE_LANGUAGE)
            run.add(
                vendors_services.accept_contract(
                    vendor,
                    owner,
                    contract=offer.contract,
                    language=offer.text.language,
                    text_hash=offer.text_hash,
                    ctx=run.ctx,
                )
            )
        run.add(vendors_services.change_status(vendor, vendors_engine.ACTIVE, ctx=run.ctx))
    if not tax_services.has_tax_profile(vendor):
        profile = VendorTaxProfile(
            vendor=vendor,
            regime=spec.vat_regime,
            exempt_reason=spec.exempt_reason,
            exempt_country=spec.exempt_country,
            oss_registered_since=spec.oss_since,
            valid_from=data.SEED_EPOCH,
        )
        run.add(
            tax_services.configure_vendor_tax_profile(profile, approver=_operator(run), ctx=run.ctx)
        )

    def text(language: str) -> VendorTranslation:
        policies = {name: _text(texts, language) for name, texts in data.POLICIES.items()}
        return VendorTranslation(
            vendor=vendor,
            language=language,
            tagline=_text(spec.tagline, language),
            description=_text(spec.description, language),
            **policies,
        )

    for row in _publish_texts(vendor.translations.all(), text, spec.tagline):
        run.created("vendors.vendortranslation", f"{spec.slug}:{row.language}")
    return vendor


def _reset_vendor(run: _Run, vendor: Vendor) -> None:
    """Delete the vendor's products and shipping, then close it (acceptances are append-only)."""
    for product in Product.objects.filter(vendor=vendor).order_by("-id"):
        run.add(catalog_services.delete_product(product, ctx=run.ctx))
    shipping: list[Any] = [
        *ShippingMethod.objects.filter(vendor=vendor),
        *PickupOption.objects.filter(vendor=vendor),
    ]
    if shipping:
        run.add(delete_config(shipping, ctx=run.ctx))
    zones = list(ShippingZone.objects.filter(vendor=vendor))
    if zones:
        run.add(delete_config(zones, ctx=run.ctx))
    vendor.refresh_from_db()
    if vendor.status != vendors_engine.CLOSED:  # every other status may close
        run.add(vendors_services.change_status(vendor, vendors_engine.CLOSED, ctx=run.ctx))


# --- Products


def _variant_sku(sku: str, options: tuple[str, ...]) -> str:
    return "-".join([sku, *(option.upper() for option in options)])


def _gpsr_defaults(run: _Run, vendor: Vendor) -> None:
    """The seed vendors make what they sell: their GPSR defaults name themselves (CMP.3)."""
    street = f"{vendor.registered_street} {vendor.registered_street_number}".strip()
    city = f"{vendor.registered_postal_code} {vendor.registered_city}".strip()
    run.add(
        compliance_services.set_vendor_gpsr_defaults(
            vendor,
            {
                "manufacturer_name": vendor.legal_name,
                "manufacturer_address": ", ".join(part for part in (street, city) if part)
                or vendor.country,
                "manufacturer_contact": vendor.contact_email,
                "manufacturer_country": vendor.country,
            },
            ctx=run.ctx,
        )
    )


def _products(run: _Run, vendor: Vendor, spec: data.VendorSeed, catalog: _Catalog) -> None:
    existing = set(Product.objects.filter(vendor=vendor).values_list("sku", flat=True))
    if not existing:
        _gpsr_defaults(run, vendor)
    for number, item in enumerate(data.PRODUCTS[spec.letter]):
        sku = f"{spec.sku_prefix}-{item.code}"
        if sku in existing:
            continue
        short, long = data.product_description(item.title, vendor=spec)
        texts = tuple(
            catalog_engine.TranslationIn(
                language=language,
                title=item.title[language],
                short_description=short[language],
                description=long[language],
            )
            for language in ("en", "it")
        )
        attributes: tuple[catalog_engine.AttributeIn, ...] = ()
        variants: tuple[catalog_engine.VariantIn, ...] = ()
        if item.axes:
            attributes = tuple(
                catalog_engine.AttributeIn(
                    code=axis,
                    values=tuple(dict.fromkeys(option[i] for option in item.options)),
                    axis=True,
                )
                for i, axis in enumerate(item.axes)
            )
            variants = tuple(
                catalog_engine.VariantIn(
                    sku=_variant_sku(sku, option),
                    options=dict(zip(item.axes, option, strict=True)),
                    stock_qty=3 + (number + i) % 9,
                    is_default=i == 0,
                )
                for i, option in enumerate(item.options)
            )
        run.add(
            catalog_services.create_product(
                vendor,
                catalog_engine.ProductIn(
                    sku=sku,
                    price_net=item.price,
                    kind="variable" if item.axes else "simple",
                    sales_mode=item.sales_mode,
                    fiscal_category=item.fiscal_category,
                    weight_kg=item.weight,
                    translations=texts,
                    attributes=attributes,
                    variants=variants,
                ),
                ctx=run.ctx,
            )
        )
        product = Product.objects.get(vendor=vendor, sku=sku)
        for row in product.translations.all():
            row.status = PUBLISHED
            row.save()
        product.category = catalog.categories[item.category]
        product.brand = catalog.brands[spec.brand]
        product.stock_qty = 0 if item.axes else 4 + (number * 7) % 30
        run.add(compliance_services.set_product_gpsr(product, {"model_number": sku}, ctx=run.ctx))
        product.status = Product.Status.PUBLISHED  # GPSR complete: the publication check passes
        run.add(catalog_services.save_product(product, ctx=run.ctx))
        before = catalog_services.relations_snapshot(product)
        product.terms.set([catalog.terms[item.term]])
        run.add(catalog_services.record_relations_change(product, before, ctx=run.ctx))
        if item.sale_price is not None:
            run.add(
                catalog_services.set_product_price(
                    product, price_net=item.price, sale_price_net=item.sale_price, ctx=run.ctx
                )
            )


# --- Shipping: carriers, then each vendor's zones and methods as a configuration file


def _carriers(run: _Run) -> None:
    for code, labels in data.CARRIERS.items():
        carrier = Carrier.objects.filter(code=code).first()
        if carrier is None:
            carrier = Carrier(code=code)
            run.add(save_config(carrier, ctx=run.ctx))
        _publish_fields(
            carrier.translations.all(),
            CarrierTranslation,
            {"carrier": carrier},
            {"label": labels},
            labels,
        )


def _reset_carriers(run: _Run) -> None:
    for carrier in Carrier.objects.filter(code__in=list(data.CARRIERS)):
        if not ShippingMethod.objects.filter(carrier=carrier).exists():
            run.add(delete_config([carrier], ctx=run.ctx))


def _bands(rows: Iterable[tuple[str, str | None, str]]) -> list[dict[str, Any]]:
    return [
        {"from_kg": low, "to_kg": high, "cost_net": cost, "surcharge_net": "0.00"}
        for low, high, cost in rows
    ]


def shipping_document(spec: data.VendorSeed) -> dict[str, Any]:
    """The vendor's shipping configuration as a file of the SHP.13 format (pure)."""
    treatments = [
        {
            "area": area,
            "mode": "surcharge",
            "surcharge_type": "fixed",
            "surcharge": data.ISLAND_SURCHARGE,
            "extra_days": 2,
        }
        for area in spec.island_areas
    ]
    treatments += [
        {
            "area": area,
            "mode": "quote_only",
            "surcharge_type": "",
            "surcharge": "0.00",
            "extra_days": 0,
        }
        for area in spec.quote_areas
    ]
    treatments += [
        {
            "area": area,
            "mode": "unavailable",
            "surcharge_type": "",
            "surcharge": "0.00",
            "extra_days": 0,
        }
        for area in spec.closed_areas
    ]

    def method(
        zone: str,
        key: str,
        carrier: str,
        level: str,
        days: tuple[int, int],
        bands: list[dict[str, Any]],
        default: bool,
    ) -> dict[str, Any]:
        return {
            "zone_scope": shipping_format.OWN,
            "zone": zone,
            "key": key,
            "carrier": carrier,
            "service_level": level,
            "min_delivery_days": days[0],
            "max_delivery_days": days[1],
            "is_default": default,
            "free_shipping_threshold": data.FREE_SHIPPING_THRESHOLD if key == "standard" else None,
            "tax_treatment": "follows_goods",
            "over_max_weight": "last_band",
            "bands": bands,
            "area_treatments": treatments if zone == "home" else [],
        }

    return {
        "format": shipping_format.FORMAT,
        "version": shipping_format.VERSION,
        "zones": [
            {
                "code": "home",
                "name": f"Domestic ({spec.shipping_country})",
                "priority": 10,
                "countries": [spec.shipping_country],
                "country_groups": [],
                "postcode_patterns": [],
                "states": [],
            },
            {
                "code": "eu",
                "name": "European Union",
                "priority": 1,
                "countries": [],
                "country_groups": ["eu"],
                "postcode_patterns": [],
                "states": [],
            },
        ],
        "methods": [
            method(
                "home",
                "standard",
                "demo-post",
                "standard",
                (2, 4),
                _bands(data.STANDARD_BANDS),
                True,
            ),
            method(
                "home",
                "express",
                "demo-express",
                "express",
                (1, 2),
                _bands(data.EXPRESS_BANDS),
                False,
            ),
            method("eu", "standard", "demo-post", "standard", (4, 8), _bands(data.EU_BANDS), True),
        ],
    }


def _shipping(run: _Run, vendor: Vendor, spec: data.VendorSeed) -> None:
    if ShippingZone.objects.filter(vendor=vendor).exists():
        return
    parsed = shipping_format.parse_json(shipping_document(spec))
    result = import_config(parsed, vendor=vendor, ctx=run.ctx, source="seed")
    if not result.ok:
        problems = [(row.ref, row.errors) for row in result.rows if row.errors]
        raise ValueError(f"the seed shipping file of {spec.slug} is invalid: {problems}")
    run.add(result.changeset)


# --- Images, CMS pages and events


def _images(run: _Run) -> dict[str, MediaImage]:
    licence = ImageLicence.objects.get(code=OWN_WORK_LICENCE)
    found: dict[str, MediaImage] = {}
    for key, (title, colour) in data.IMAGES.items():
        image = MediaImage.objects.filter(title=title).first()
        if image is None:
            content = placeholder_webp(key, colour)
            image = MediaImage(
                title=title,
                licence=licence,
                author=data.IMAGE_AUTHOR,
                file=ImageFile(_named_bytes(content), name=f"demo-{key}.webp"),
            )
            run.add(save_image(image))
            run.files.append(image.file.name)
        found[key] = image
    return found


def _named_bytes(content: bytes) -> io.BytesIO:
    return io.BytesIO(content)


def _publish(page: Any) -> Any:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _stripped(value: Any) -> Any:
    """A StreamField's stored value without the block ids, which change on every assignment."""
    if isinstance(value, list):
        return [_stripped(item) for item in value]
    if isinstance(value, dict):
        return {k: _stripped(v) for k, v in value.items() if k != "id"}
    return value


def _set_body(page: Any, body: list[Any]) -> bool:
    """Give `page` the `body` unless it already has it; True when it changed."""
    before = _stripped(page.body.get_prep_value())
    page.body = body
    return bool(_stripped(page.body.get_prep_value()) != before)


def _home_body(
    language: str, images: dict[str, MediaImage], how: Any, vendors: list[Vendor], terms: list[Term]
) -> list[Any]:
    heading, text, label = data.HOME_HERO.get(language) or data.HOME_HERO[SOURCE_LANGUAGE]
    return [
        (
            "hero",
            {
                "heading": heading,
                "text": text,
                "image": images["home-hero"],
                "alt_text": _text(data.HOME_HERO_ALT, language),
                "links": [{"page": how, "url": "", "label": label}],
            },
        ),
        ("paragraph", _text(data.DEMO_NOTICE, language)),
        (
            "featured",
            {
                "heading": _text(data.HOME_FEATURED, language),
                "cards": [("term", {"term": term, "label": "", "text": ""}) for term in terms],
            },
        ),
        (
            "featured",
            {
                "heading": _text(data.HOME_VENDORS, language),
                "cards": [
                    ("vendor", {"vendor": vendor, "label": "", "text": ""}) for vendor in vendors
                ],
            },
        ),
    ]


def _is_seed_home(page: HomePage, language: str) -> bool:
    """Whether the home still has the CMS.1 sample text or the seed's own body."""
    raw = _stripped(page.body.get_prep_value())
    sample = seed_text(language).home.body
    if raw == [{"type": "paragraph", "value": sample}] or raw == []:
        return True
    heading = (data.HOME_HERO.get(language) or data.HOME_HERO[SOURCE_LANGUAGE])[0]
    return bool(raw) and raw[0].get("type") == "hero" and raw[0]["value"].get("heading") == heading


def _translation(source: Any, locale: Locale) -> Any:
    existing = source.get_translation_or_none(locale)
    if existing is not None:
        return existing.specific
    return source.copy_for_translation(locale).specific


def _locales() -> dict[str, Locale]:
    return {locale.language_code: locale for locale in Locale.objects.all()}


def _cms(run: _Run, images: dict[str, MediaImage], vendors: list[Vendor]) -> None:
    for line in seed_site().created:
        run.created("cms.page", line)
    locales = _locales()
    home = HomePage.objects.get(locale=Locale.get_default(), depth=2)

    how = _standard_page(run, home, locales, images["how-it-works"])
    for kind, spec in data.LEGAL_PAGES.items():
        _legal_page(run, home, locales, kind, spec)

    terms = [
        Term.objects.get(
            taxonomy__code=data.TAXONOMY_CODE,
            translations__language=SOURCE_LANGUAGE,
            translations__slug=node.key,
        )
        for node in data.TERMS
    ]
    for page in HomePage.objects.filter(depth=2).select_related("locale"):
        language = page.locale.language_code
        if not _is_seed_home(page, language):
            continue
        local_how = how.get_translation_or_none(page.locale) or how
        if _set_body(page, _home_body(language, images, local_how, vendors, terms)):
            _publish(page)
            run.changes.update("cms.homepage", language, {}, {"body": "demo"})


def _standard_page(
    run: _Run, home: HomePage, locales: dict[str, Locale], image: MediaImage
) -> StandardPage:
    spec = data.HOW_IT_WORKS
    page = StandardPage.objects.child_of(home).filter(slug=spec.slug["en"]).first()

    def body(language: str) -> list[Any]:
        faq = data.HOW_IT_WORKS_FAQ.get(language) or data.HOW_IT_WORKS_FAQ["en"]
        return [
            ("paragraph", spec.body[language] + _text(data.DEMO_NOTICE, language)),
            ("faq", {"heading": "", "items": [{"question": q, "answer": a} for q, a in faq]}),
        ]

    if page is None:
        page = StandardPage(
            locale=home.locale,
            title=spec.title["en"],
            slug=spec.slug["en"],
            image=image,
            image_alt_text=_text(data.HOME_HERO_ALT, "en"),
            body=body("en"),
        )
        home.add_child(instance=page)
        page = _publish(page)
        run.created("cms.standardpage", page.url_path)
    for language in ("it",):
        locale = locales.get(language)
        if locale is None or page.get_translation_or_none(locale) is not None:
            continue
        copy = page.copy_for_translation(locale).specific
        copy.title = copy.draft_title = spec.title[language]
        copy.slug = spec.slug[language]
        copy.image_alt_text = _text(data.HOME_HERO_ALT, language)
        copy.body = body(language)
        copy = _publish(copy)
        run.created("cms.standardpage", copy.url_path)
    found: StandardPage = page
    return found


def _legal_page(
    run: _Run, home: HomePage, locales: dict[str, Locale], kind: str, spec: data.PageSeed
) -> None:
    page = LegalPage.objects.child_of(home).filter(slug=spec.slug["en"]).first()
    if page is None:
        page = LegalPage(
            locale=home.locale,
            title=spec.title["en"],
            slug=spec.slug["en"],
            kind=kind,
            effective_date=data.LEGAL_EFFECTIVE,
            body=[("paragraph", spec.body["en"])],
        )
        home.add_child(instance=page)
        page = _publish(page)
        run.created("cms.legalpage", page.url_path)
    elif not page.live:
        _publish(page)
        run.changes.update("cms.legalpage", page.url_path, {"live": False}, {"live": True})
    for language in ("it",):
        locale = locales.get(language)
        if locale is None:
            continue
        existing = page.get_translation_or_none(locale)
        if existing is not None:
            existing = existing.specific
            if not existing.live:
                _publish(existing)
                run.changes.update(
                    "cms.legalpage", existing.url_path, {"live": False}, {"live": True}
                )
            continue
        copy = page.copy_for_translation(locale).specific
        copy.title = copy.draft_title = spec.title[language]
        copy.slug = spec.slug[language]
        copy.body = [("paragraph", spec.body[language])]
        copy = _publish(copy)
        run.created("cms.legalpage", copy.url_path)


def _seed_pages(model: Any, slugs: Iterable[str]) -> list[Any]:
    """The seed's pages of `model` (sources and their translations), by the source slugs."""
    sources = model.objects.filter(locale=Locale.get_default(), slug__in=list(slugs))
    keys = list(sources.values_list("translation_key", flat=True))
    return list(model.objects.filter(translation_key__in=keys).order_by("-depth", "-path"))


def _reset_cms(run: _Run) -> None:
    for page in _seed_pages(StandardPage, [data.HOW_IT_WORKS.slug["en"]]):
        run.changes.delete("cms.standardpage", page.url_path, {})
        page.delete()
    for page in _seed_pages(LegalPage, [spec.slug["en"] for spec in data.LEGAL_PAGES.values()]):
        # Every publish recorded an append-only LegalPageVersion: the page is unpublished.
        if page.live:
            page.unpublish()
            run.changes.update("cms.legalpage", page.url_path, {"live": True}, {"live": False})
    for page in HomePage.objects.filter(depth=2).select_related("locale"):
        language = page.locale.language_code
        raw = _stripped(page.body.get_prep_value())
        heading = (data.HOME_HERO.get(language) or data.HOME_HERO[SOURCE_LANGUAGE])[0]
        if raw and raw[0].get("type") == "hero" and raw[0]["value"].get("heading") == heading:
            page.body = [("paragraph", seed_text(language).home.body)]
            _publish(page)
            run.changes.update("cms.homepage", language, {"body": "demo"}, {"body": "sample"})
    for title, _colour in data.IMAGES.values():
        for image in MediaImage.objects.filter(title=title, author=data.IMAGE_AUTHOR):
            run.changes.delete("core.mediaimage", title, {})
            image.delete()


def _events(run: _Run, images: dict[str, MediaImage], vendors: list[Vendor]) -> None:
    run.add(ensure_event_indexes())
    locales = _locales()
    index = EventIndexPage.objects.filter(locale=Locale.get_default()).first()
    if index is None:  # pragma: no cover - the home's index comes with seed_site
        return
    by_letter = {spec.letter: vendor for spec, vendor in zip(data.VENDORS, vendors, strict=True)}
    image_keys = ("event-fair", "event-sale", "event-workshop")
    for spec, image_key in zip(data.EVENTS, image_keys, strict=True):
        page = EventPage.objects.child_of(index).filter(slug=spec.slug["en"]).first()
        if page is None:
            page = EventPage(
                locale=index.locale,
                original_locale=index.locale,
                title=spec.title["en"],
                slug=spec.slug["en"],
                summary=spec.summary["en"],
                vendor=by_letter.get(spec.vendor) if spec.vendor else None,
                event_type=spec.event_type,
                listing_image=images[image_key],
                body=[("paragraph", f"<p>{spec.summary['en']}</p>")],
                **_event_when_where(spec),
            )
            index.add_child(instance=page)
            page = _publish(page)
            run.created("events.eventpage", page.url_path)
        locale = locales.get("it")
        if locale is None or page.get_translation_or_none(locale) is not None:
            continue
        copy = page.copy_for_translation(locale).specific
        copy.title = copy.draft_title = spec.title["it"]
        copy.slug = spec.slug["it"]
        copy.summary = spec.summary["it"]
        copy.body = [("paragraph", f"<p>{spec.summary['it']}</p>")]
        copy = _publish(copy)
        run.created("events.eventpage", copy.url_path)


def _event_when_where(spec: data.EventSeed) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if spec.days is not None:
        values.update(all_day=True, start_date=spec.days[0], end_date=spec.days[1])
    else:
        values.update(all_day=False, starts_at=spec.start, ends_at=spec.end)
    if spec.online_url:
        values.update(attendance=EventPage.Attendance.ONLINE, online_url=spec.online_url)
    elif spec.venue:
        values.update(attendance=EventPage.Attendance.IN_PERSON, **spec.venue)
    else:
        values.update(attendance="")
    return values


def _reset_events(run: _Run, vendors: list[Vendor]) -> None:
    slugs = [spec.slug["en"] for spec in data.EVENTS]
    for page in _seed_pages(EventPage, slugs):
        run.changes.delete("events.eventpage", page.url_path, {})
        page.delete()
    for page in EventPage.objects.filter(vendor__in=vendors).order_by("-depth"):
        run.changes.delete("events.eventpage", page.url_path, {})
        page.delete()
