"""Tax configuration (spec 05 §5.2, TAX.1): rates, special territories, vendor VAT regimes and
the cache of VIES answers; the vendors' regime change requests (TAX.2).

Rates and vendor tax profiles are versioned configuration, like `PlatformOperator` (ARC.7):
rows are history, a change is a new row valid from a date that closes the current one (through
`apps.tax.services`), and only `valid_to` may be written on an existing row. Orders keep a
snapshot of what they used (spec 07), so past documents stay explainable.
"""

import logging
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import CountryField
from apps.core.models import BaseModel, TranslationModel, TsTzRange, Versioned, valid_period_check
from apps.core.postcodes import (
    MAX_PATTERN_LENGTH,
    InvalidPostcodePattern,
    PostcodePattern,
    normalize_postcode_pattern,
)
from apps.core.translation_fields import translation_language_field, translation_status_field
from apps.vendors.models import FiscalRegime, Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine

logger = logging.getLogger(__name__)

# --- Territories


class TerritoryCodeField(models.CharField[Any, Any]):
    """A territory code such as `ES-CN`, normalized to upper case."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", engine.TERRITORY_CODE_MAX_LENGTH)
        super().__init__(*args, **kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if not isinstance(value, str) or not value:
            return value
        try:
            return engine.normalize_territory_code(value)
        except engine.InvalidTerritoryCode as exc:
            raise ValidationError(str(exc), code="invalid_territory_code") from exc


class PostcodePatternsField(ArrayField[Any, Any]):
    """Postcode patterns of the grammar shared with shipping zones (`apps.core.postcodes`,
    TAX.11), each normalized on validation: exact `23041`, prefix `35*`, range `9000..9999`."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_field", models.CharField(max_length=MAX_PATTERN_LENGTH))
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        value = super().to_python(value)
        if not isinstance(value, list):
            return value
        try:
            return [
                normalize_postcode_pattern(item) if isinstance(item, str) else item
                for item in value
            ]
        except InvalidPostcodePattern as exc:
            raise ValidationError(str(exc), code="invalid_postcode_pattern") from exc


class TerritoryVatStatus(models.TextChoices):
    OUTSIDE_VAT_AREA = engine.OUTSIDE_VAT_AREA, "Outside the EU VAT area"
    OWN_RATES = engine.OWN_RATES, "Inside the VAT area, with rates of its own"
    SPECIAL = engine.SPECIAL, "Special status, handled by an explicit tax engine rule"


class Territory(BaseModel):
    """A territory whose VAT treatment differs from its country's (Canary Islands, Livigno,
    Åland, Northern Ireland…), recognised by postcode (TAX.7).

    Data only: what each status means for a sale is the tax engine's job (TAX.3, TAX.7). The
    `special` status stores territories whose rule is an explicit engine rule.
    """

    VatStatus = TerritoryVatStatus

    code = TerritoryCodeField(
        unique=True,
        help_text=_("A short code that is unique, for example 'ES-CN' for the Canary Islands."),
    )
    name = models.CharField(
        max_length=100, help_text=_("The name of the territory, for example 'Canary Islands'.")
    )
    country = CountryField(
        help_text=_("The country the territory belongs to, as a two-letter code such as 'ES'.")
    )
    vat_status = models.CharField(
        "VAT status",
        max_length=24,
        choices=VatStatus.choices,
        help_text=_(
            "Whether the territory is outside the EU VAT area, inside it with rates of its "
            "own, or special and handled by its own rule. Ask your accountant if you are "
            "not sure."
        ),
    )
    postcode_patterns = PostcodePatternsField(
        default=list,
        blank=True,
        help_text=_(
            "The postcodes in the territory, separated by commas: an exact postcode such as"
            " '23041', a prefix such as '35*' or a range such as '22100..22199'."
        ),
    )

    class Meta:
        ordering = ["country", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(vat_status__in=engine.TERRITORY_STATUSES),
                name="tax_territory_vat_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.name})"

    # The patterns parsed by `parsed_postcode_patterns`, keyed by the list they were parsed from.
    _parsed_patterns: tuple[tuple[str, ...], tuple[PostcodePattern, ...]] | None = None

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Every save validates and normalizes the fields, postcode patterns included, so create()
        # and seeds cannot store a pattern the grammar refuses (TAX.11 review). Uniqueness and
        # constraints are left to the database. Writes that bypass save() are caught when the
        # patterns are loaded (`parsed_postcode_patterns`).
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    def parsed_postcode_patterns(self) -> tuple[PostcodePattern, ...]:
        """The postcode patterns, parsed once per list.

        Policy for an invalid stored row: log it and raise engine.TerritoryConfigError, so the
        tax decision gets a typed, controlled error rather than an arbitrary crash, the same for
        every address and whatever the order of the patterns.
        """
        key = tuple(self.postcode_patterns)
        if self._parsed_patterns is None or self._parsed_patterns[0] != key:
            try:
                parsed = engine.parse_territory_patterns(key, code=self.code)
            except engine.TerritoryConfigError:
                logger.error("Territory %s has an invalid stored postcode pattern", self.code)
                raise
            self._parsed_patterns = (key, parsed)
        return self._parsed_patterns[1]

    def matches(self, country: str, postcode: str) -> bool:
        """True when an address in `country` with `postcode` lies in this territory: same
        country, and the postcode matches one of the patterns."""
        return engine.postcode_in_territory(
            self.country, self.parsed_postcode_patterns(), country, postcode
        )


# --- Tax rates


class TaxRateCategory(models.TextChoices):
    STANDARD = engine.STANDARD, "Standard"
    REDUCED = engine.REDUCED, "Reduced"
    SUPER_REDUCED = engine.SUPER_REDUCED, "Super-reduced"
    ZERO = engine.ZERO, "Zero"
    BOOKS = engine.BOOKS, "Books"


class TaxRateSource(models.TextChoices):
    EC_TEDB = engine.RATE_SOURCE_TEDB, "EC TEDB"
    MANUAL = engine.RATE_SOURCE_MANUAL, "Manual"


class TaxRate(Versioned):
    """The VAT percentage of one category in a country (or one of its territories), valid for a
    period. Periods of the same country, territory and category never overlap.

    Read with `apps.tax.services.current_rate`, which fails loudly when no rate is configured:
    a missing rate is a configuration error, never a silent 0% (spec 05 T6).
    """

    Category = TaxRateCategory
    Source = TaxRateSource

    country = CountryField(
        help_text=_("The country the rate applies to, as a two-letter code such as 'IT'.")
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rates",
        help_text=_(
            "Only for the rate of a territory of the country. Leave it empty for the "
            "country's own rate."
        ),
    )
    category = models.CharField(
        max_length=16,
        choices=Category.choices,
        help_text=_(
            "Which kind of goods the rate is for: standard, reduced, super-reduced, zero or books."
        ),
    )
    rate = models.DecimalField(
        max_digits=engine.RATE_MAX_DIGITS,
        decimal_places=engine.RATE_DECIMAL_PLACES,
        validators=[MinValueValidator(engine.MIN_RATE), MaxValueValidator(engine.MAX_RATE)],
        help_text=_(
            "The VAT percentage, for example 25.5 for 25.5%. Ask your accountant if you are"
            " not sure."
        ),
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        help_text=_(
            "Where the rate comes from: the European Commission rate database (EC TEDB) or "
            "entered by hand."
        ),
    )

    class Meta:
        ordering = ["country", "territory_id", "category", "-valid_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(category__in=engine.CATEGORIES),
                name="tax_taxrate_category_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=engine.RATE_SOURCES),
                name="tax_taxrate_source_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gte=engine.MIN_RATE, rate__lte=engine.MAX_RATE),
                name="tax_taxrate_rate_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(category=engine.ZERO) | models.Q(rate=0),
                name="tax_taxrate_zero_category_is_zero",
            ),
            valid_period_check("tax_taxrate_valid_to_after_valid_from"),
            ExclusionConstraint(
                name="tax_taxrate_no_overlap",
                expressions=[
                    ("country", RangeOperators.EQUAL),
                    (Coalesce("territory", models.Value(0)), RangeOperators.EQUAL),
                    ("category", RangeOperators.EQUAL),
                    (TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS),
                ],
                violation_error_message=(
                    "Rates of the same country, territory and category must not overlap."
                ),
            ),
        ]

    def __str__(self) -> str:
        where = self.territory.code if self.territory else self.country
        return f"{where} {self.category} {self.rate}% from {self.valid_from:%Y-%m-%d}"

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.territory is not None and self.territory.country != self.country:
            errors["territory"] = f"{self.territory.code} is not in {self.country}."
        if self.rate is not None and self.category:
            message = engine.rate_error(self.category, self.rate)
            if message:
                errors["rate"] = message
            else:  # the same value the database will hold, for snapshots and audit entries
                self.rate = engine.quantize_rate(self.rate)
        if errors:
            raise ValidationError(errors)


# --- Vendor tax profiles


class VatRegime(models.TextChoices):
    OSS = engine.OSS, "OSS (destination-country VAT for EU consumers)"
    DOMESTIC_ONLY = engine.DOMESTIC_ONLY, "Domestic only (vendor-country VAT)"
    EXEMPT = engine.EXEMPT, "Exempt (no VAT charged)"


def vat_registration_country(vendor: Vendor) -> str:
    """The country of the vendor's VAT registration (TAX.13): see
    `engine.vat_registration_country`."""
    return engine.vat_registration_country(vendor.vat_number, vendor.country)


class ExemptReason(models.TextChoices):
    ART74_PUBLISHING = engine.ART74_PUBLISHING, "Publishing (IT art. 74)"
    FLAT_RATE_FORFETTARIO = engine.FLAT_RATE_FORFETTARIO, "Flat-rate scheme (IT forfettario, RF19)"
    OTHER = engine.OTHER_EXEMPTION, "Other"


class VendorTaxProfile(Versioned):
    """A vendor's VAT regime for a period: a new row on every approved change (TAX.2,
    `TaxRegimeChangeRequest`).

    The fiscal regime declared on invoices (`Vendor.fiscal_regime`, VEN.12) stays on the vendor;
    `clean()` keeps the two consistent (`engine.profile_errors`: RF19 ⇔ flat-rate exemption).
    """

    Regime = VatRegime
    ExemptReason = ExemptReason

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="tax_profiles",
        help_text=_("The vendor this VAT regime belongs to."),
    )
    regime = models.CharField(
        "VAT regime",
        max_length=16,
        choices=Regime.choices,
        help_text=_(
            "How the vendor charges VAT: OSS (VAT of the buyer's country for EU consumers),"
            " domestic only (VAT of the vendor's own country) or exempt (no VAT charged). "
            "Ask your accountant if you are not sure."
        ),
    )
    exempt_reason = models.CharField(
        max_length=24,
        choices=ExemptReason.choices,
        blank=True,
        help_text=_(
            "Only for the exempt regime: why the vendor charges no VAT, the flat-rate "
            "scheme or another reason. Ask your accountant if you are not sure."
        ),
    )
    exempt_country = CountryField(
        "country of the exemption law",
        blank=True,
        help_text=_(
            "Only for an 'Other' exemption: the country of the law named in the legal note,"
            " as a two-letter code. It must be the country of the vendor's VAT number. Ask "
            "your accountant if you are not sure."
        ),
    )
    exemption_nature = models.CharField(
        "exemption nature",
        max_length=4,
        choices=[(code, code) for code in engine.NATURE_CODES],
        blank=True,
        help_text=_(
            "Only for an 'Other' exemption: the nature code that the e-invoice needs. Ask "
            "your accountant which one applies."
        ),
    )
    oss_registered_since = models.DateField(
        "OSS registered since",
        null=True,
        blank=True,
        help_text=_("Only for the OSS regime: the date the vendor registered for OSS."),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who asked for this regime (the vendor's member, TAX.2).",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    class Meta:
        ordering = ["vendor_id", "-valid_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(regime__in=engine.REGIMES), name="tax_profile_regime_valid"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(regime=engine.EXEMPT, exempt_reason__in=engine.EXEMPT_REASONS)
                    | (~models.Q(regime=engine.EXEMPT) & models.Q(exempt_reason=""))
                ),
                name="tax_profile_exempt_reason_iff_exempt",
            ),
            valid_period_check("tax_profile_valid_to_after_valid_from"),
            ExclusionConstraint(
                name="tax_profile_no_overlap",
                expressions=[
                    ("vendor", RangeOperators.EQUAL),
                    (TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS),
                ],
                violation_error_message="Tax profiles of a vendor must not overlap.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} {self.regime} from {self.valid_from:%Y-%m-%d}"

    def clean(self) -> None:
        if self.vendor_id is None or not self.regime:
            return
        errors = engine.profile_errors(
            self.regime,
            self.exempt_reason,
            fiscal_regime=self.vendor.fiscal_regime,
            oss_registered_since=self.oss_registered_since,
            exempt_country=self.exempt_country,
            vat_country=vat_registration_country(self.vendor),
        )
        if errors:
            raise ValidationError(errors)


class VendorTaxProfileTranslation(TranslationModel):
    """The legal note a profile version prints on order documents, per language (e.g. the
    bilingual art. 74 wording). Texts follow the translation workflow (I18N.2)."""

    translation_of = "profile"
    translated_fields = ("legal_note",)

    language = translation_language_field()
    status = translation_status_field()

    vendor_scope: ClassVar[str] = "profile__vendor"
    objects = VendorScopedManager()

    profile = models.ForeignKey(
        VendorTaxProfile,
        on_delete=models.CASCADE,
        related_name="translations",
        help_text=_("The VAT regime this note belongs to."),
    )
    legal_note = models.TextField(
        blank=True,
        help_text=_(
            "The legal note printed on order documents for this regime, in this language. "
            "Ask your accountant for the exact wording."
        ),
    )

    class Meta(TranslationModel.Meta):
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["profile", "language"], name="tax_profile_translation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} [{self.language}]"


# --- Regime change requests (TAX.2)


class RegimeChangeStatus(models.TextChoices):
    PENDING = engine.PENDING, "Pending review"
    APPROVED = engine.APPROVED, "Approved"
    REJECTED = engine.REJECTED, "Rejected"


class TaxRegimeChangeRequest(BaseModel):
    """A vendor owner asks for a new VAT regime from a date; staff approve or reject it.

    Written only through `apps.tax.services` (`request_regime_change`, `approve_regime_change`,
    `reject_regime_change`), which check who may act and audit every step. An approval creates
    the new `VendorTaxProfile` version (`profile`) and, when the request moves it, the vendor's
    declared fiscal regime (VEN.12), in one transaction. Reviews are final: the rows are the
    history of the vendor's requests. A vendor has at most one pending request.
    """

    Status = RegimeChangeStatus
    Regime = VatRegime
    ExemptReason = ExemptReason

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="tax_regime_requests")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, editable=False
    )
    regime = models.CharField("VAT regime", max_length=16, choices=Regime.choices)
    exempt_reason = models.CharField(
        max_length=24, choices=ExemptReason.choices, blank=True, help_text="Exempt regime only."
    )
    exempt_country = CountryField(
        "country of the exemption law",
        blank=True,
        help_text=_(
            "Only for an 'Other' exemption: the country of the law named in the legal note,"
            " as a two-letter code. It must be the country of the vendor's VAT number. Ask "
            "your accountant if you are not sure."
        ),
    )
    oss_registered_since = models.DateField("OSS registered since", null=True, blank=True)
    fiscal_regime = models.CharField(
        max_length=4,
        choices=FiscalRegime.choices,
        blank=True,
        help_text=(
            "The fiscal regime the vendor will declare on invoices (FatturaPA RegimeFiscale). "
            "Empty: unchanged, or RF19 for the flat-rate exemption."
        ),
    )
    effective_from = models.DateTimeField(help_text="When the new regime applies.")
    note = models.TextField(blank=True, help_text="The vendor's explanation for staff.")
    document_reference = models.CharField(
        max_length=200,
        blank=True,
        help_text="Supporting document, e.g. an OSS registration or tax office reference.",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", editable=False
    )
    requested_at = models.DateTimeField(default=timezone.now, editable=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    review_reason = models.TextField(blank=True, editable=False)
    profile = models.OneToOneField(
        VendorTaxProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="request",
        editable=False,
        help_text="The profile version the approval created.",
    )

    class Meta:
        ordering = ["-requested_at", "-id"]
        permissions = [
            ("review_taxregimechangerequest", "Can approve or reject tax regime change requests")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=engine.REQUEST_STATUSES),
                name="tax_regime_request_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(regime__in=engine.REGIMES),
                name="tax_regime_request_regime_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(regime=engine.EXEMPT, exempt_reason__in=engine.EXEMPT_REASONS)
                    | (~models.Q(regime=engine.EXEMPT) & models.Q(exempt_reason=""))
                ),
                name="tax_regime_request_exempt_reason_iff_exempt",
            ),
            models.CheckConstraint(
                condition=models.Q(fiscal_regime__in=["", *FiscalRegime.values]),
                name="tax_regime_request_fiscal_regime_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=engine.PENDING, reviewed_by__isnull=True, reviewed_at__isnull=True
                    )
                    | (
                        ~models.Q(status=engine.PENDING)
                        & models.Q(reviewed_by__isnull=False, reviewed_at__isnull=False)
                    )
                ),
                name="tax_regime_request_reviewed_iff_not_pending",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=engine.APPROVED, profile__isnull=False)
                    | (~models.Q(status=engine.APPROVED) & models.Q(profile__isnull=True))
                ),
                name="tax_regime_request_profile_iff_approved",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=engine.REJECTED, review_reason=""),
                name="tax_regime_request_rejection_has_reason",
            ),
            models.UniqueConstraint(
                fields=["vendor"],
                condition=models.Q(status=engine.PENDING),
                name="tax_regime_request_one_pending_per_vendor",
                violation_error_message="The vendor already has a request waiting for review.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} {self.regime} from {self.effective_from:%Y-%m-%d} ({self.status})"

    def clean(self) -> None:
        if self.vendor_id is None or not self.regime:
            return
        errors = engine.request_errors(
            self.regime,
            self.exempt_reason,
            requested_fiscal_regime=self.fiscal_regime,
            current_fiscal_regime=self.vendor.fiscal_regime,
            oss_registered_since=self.oss_registered_since,
            exempt_country=self.exempt_country,
            vat_country=vat_registration_country(self.vendor),
        )
        if errors:
            raise ValidationError(errors)


# --- VIES answers


class VatResult(models.TextChoices):
    VALID = engine.VAT_VALID, "Valid"
    INVALID = engine.VAT_INVALID, "Invalid"
    UNAVAILABLE = engine.VAT_UNAVAILABLE, "VIES unavailable"


class VatValidation(BaseModel):
    """One answer about a VAT number (ADR-0003: the cached side of VIES). Append-only.

    Written by `apps.tax.services.record_vat_validation`; `verify_vat_number` (TAX.5) adds the
    VIES client, the cache policy and the fail-closed handling. The number is stored without
    its country prefix; `country` is ISO (GR, never EL). Every row is a VIES answer:
    staff-recorded answers are out of scope until a task gives them an actor and an audited
    service.
    """

    Result = VatResult

    country = CountryField(help_text="ISO 3166-1 alpha-2 (GR for a VIES EL number).")
    vat_number = models.CharField("VAT number", max_length=32, help_text="Without the prefix.")
    result = models.CharField(max_length=16, choices=Result.choices)
    name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    request_id = models.CharField(
        max_length=64, blank=True, help_text="VIES consultation number, when given."
    )
    requester = models.CharField(
        max_length=32,
        blank=True,
        help_text=("VAT number with prefix sent to VIES as requester (the vendor's)."),
    )
    requester_kind = models.CharField(
        max_length=8,
        blank=True,
        help_text="vendor, operator (no vendor proof) or empty (anonymous).",
    )
    detail = models.CharField(max_length=64, blank=True, help_text="VIES error code or reason.")
    vies_request_date = models.DateTimeField(
        null=True, blank=True, help_text="VIES requestDate, next to the local checked_at."
    )
    checked_at = models.DateTimeField()

    class Meta:
        ordering = ["-checked_at", "-id"]
        indexes = [
            models.Index(
                fields=["country", "vat_number", "-checked_at"], name="tax_vatvalidation_lookup"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(result__in=engine.VAT_RESULTS),
                name="tax_vatvalidation_result_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country}{self.vat_number} {self.result} at {self.checked_at:%Y-%m-%d %H:%M}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("VatValidation is append-only")
        super().save(*args, **kwargs)
