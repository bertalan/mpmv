"""Shipping configuration: carriers, country groups, zones, shipping methods, weight bands,
special areas and their treatments, pickup, versions (spec 06 §6.2, SHP.1, SHP.12, SHP.14).

The integrity rules are database constraints, so imports (SHP.5, SHP.13), the admin editor
(SHP.6) and the API cannot bypass them: at most one default method per vendor and zone, weight
bands of a method that never overlap (an exclusion constraint), non-negative net amounts. Rules
that span tables (a method uses a platform zone or its vendor's own zone; a zone's country groups
exist) are model validation, which every write runs through `apps.shipping.services`.

Vendor deletion: a vendor's zones, methods and pickup options protect it (PROTECT), like its
products and tax profiles. They are prices or decide them, so they leave only through the
audited `services.delete_config`, never as an unaudited cascade of the vendor's deletion.

Amounts are net of VAT: the tax engine adds VAT (spec 06 rule 2), following each method's stored
`tax_treatment`. Orders keep snapshots of what they used, never references to these rows.

Versioning: the configuration is versioned as a whole by `ShippingConfigVersion` snapshots
(spec 06 replaces the legacy option backups with it), not by validity periods on each row.
"""

from collections.abc import Collection
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DecimalRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.postcodes import split_country_qualifier
from apps.core.translation_fields import translation_language_field, translation_status_field
from apps.core.translations import Resolved
from apps.vendors.scoping import VendorScopedManager

from . import engine
from .fields import (
    CountryGroupCodesField,
    PostcodePatternsField,
    StatesField,
    TerritoryCodeField,
    ZoneCountriesField,
)

MIN_PRIORITY = 1
MAX_PRIORITY = 999


def weight_field(**kwargs: Any) -> models.DecimalField[Any, Any]:
    """Weight in kg with 3 decimals (spec 06: sum of weight x quantity, 3 decimals)."""
    return models.DecimalField(max_digits=9, decimal_places=3, **kwargs)


class Carrier(BaseModel):
    """A way of shipping (main courier, the vendor's own carrier, an aggregator...).

    An inactive carrier offers no rates at all (spec 06 §6.3 case 10, SHP.3).
    """

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text=_("A short name without spaces that is unique, for example 'dhl'."),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_(
            "Untick it to switch the carrier off: an inactive carrier offers no rates at all."
        ),
    )
    tracking_url_template = models.CharField(
        max_length=500,
        blank=True,
        help_text=_(
            "Not used yet: saved for a future feature. The carrier's tracking link, with "
            "{tracking_number} where the parcel number goes. Leave it empty if there is none."
        ),
    )
    supports_api = models.BooleanField(
        "supports API",
        default=False,
        help_text=_(
            "Not used yet: saved for a future feature. Tick it if the carrier offers labels and "
            "tracking through an API."
        ),
    )

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code

    def translation(self, language: str) -> "Resolved[CarrierTranslation]":
        """The label to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class CarrierTranslation(TranslationModel):
    translation_of = "carrier"
    translated_fields = ("label",)

    language = translation_language_field()
    status = translation_status_field()

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="translations")
    label = models.CharField(
        max_length=100,
        help_text=_(
            "The carrier's name in this language, for example 'Standard courier'. "
            "Buyers will see it once checkout uses shipping."
        ),
    )

    class Meta(TranslationModel.Meta):
        ordering = ["carrier", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["carrier", "language"], name="shipping_carriertranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.carrier} [{self.language}]"


class CountryGroup(BaseModel):
    """A named set of countries zones can use instead of listing them (SHP.12): the EU member
    states, Europe, the UN M49 continents and regions (seeded), or groups staff add.

    Zones reference a group by its code (`ShippingZone.country_groups`), so a group in use keeps
    its code and cannot be deleted (`apps.shipping.services.delete_config`). Zones store the codes
    in lower case, so the group's code is lower-cased on validation and the database refuses
    capitals.
    """

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text=_(
            "A short name in lower case that is unique, for example 'eu' or 'dach'. Zones "
            "refer to a group by this code, so a group that a zone uses keeps its code and "
            "cannot be deleted."
        ),
    )
    countries = ZoneCountriesField(
        help_text=_(
            "The countries of the group: two-letter country codes such as 'IT' or 'DE', "
            "separated by commas."
        )
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code=Lower("code")), name="shipping_group_code_lower_case"
            ),
            models.CheckConstraint(
                condition=models.Q(countries__len__gte=1), name="shipping_group_has_countries"
            ),
            models.CheckConstraint(
                condition=~models.Q(countries__contains=[engine.WORLD]),
                name="shipping_group_no_world_marker",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def translation(self, language: str) -> "Resolved[CountryGroupTranslation]":
        return resolve_translation(self.translations.all(), language)

    def clean_fields(self, exclude: Collection[str] | None = None) -> None:
        if isinstance(self.code, str):
            self.code = self.code.strip().lower()  # as CountryGroupCodesField stores references
        super().clean_fields(exclude=exclude)

    def clean(self) -> None:
        if self.pk is None:
            return
        stored = CountryGroup.objects.filter(pk=self.pk).values_list("code", flat=True).first()
        if stored is not None and stored != self.code and zones_using_groups([stored]).exists():
            raise ValidationError(
                {"code": f"Zones use the group {stored!r}: its code cannot change."}
            )


class CountryGroupTranslation(TranslationModel):
    translation_of = "country_group"
    translated_fields = ("name",)

    language = translation_language_field()
    status = translation_status_field()

    country_group = models.ForeignKey(
        CountryGroup, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(
        max_length=100,
        help_text=_(
            "The name of the group in this language, for example 'German-speaking countries'."
        ),
    )

    class Meta(TranslationModel.Meta):
        ordering = ["country_group", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["country_group", "language"], name="shipping_grouptranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country_group} [{self.language}]"


def zones_using_groups(codes: list[str]) -> "models.QuerySet[ShippingZone]":
    """Zones that reference any of the country groups `codes`."""
    return ShippingZone.objects.filter(country_groups__overlap=codes)


class ShippingZone(BaseModel):
    """A destination area: countries and country groups, optionally narrowed by postcode
    patterns or provinces / states.

    A platform zone (`vendor` empty) is shared by every vendor; a vendor zone belongs to one
    vendor, is visible only to it (tenant scoping) and, for that vendor, wins over platform
    zones that match the same address. Precedence is in `engine.resolve_zone` (spec 06 §6.2);
    `["*"]` is the rest of the world, used only when no other zone matches.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shipping_zones",
        help_text=_(
            "Leave it empty for a zone that every vendor can use. Choose a vendor for a "
            "zone that belongs to that vendor only."
        ),
    )
    code = models.SlugField(
        max_length=50,
        help_text=_(
            "A short name without spaces for the zone. The same vendor cannot use it twice."
        ),
    )
    name = models.CharField(
        max_length=100, help_text=_("The name of the zone, for example 'Italy, mainland'.")
    )
    priority = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_PRIORITY), MaxValueValidator(MAX_PRIORITY)],
        help_text=_(
            "A number from 1 to 999. When several zones fit an address, the lowest number wins."
        ),
    )
    countries = ZoneCountriesField(
        default=list,
        blank=True,
        help_text=_(
            "The countries of the zone: two-letter country codes such as 'IT' or 'DE', "
            "separated by commas. Write '*' for the rest of the world."
        ),
    )
    country_groups = CountryGroupCodesField(
        default=list,
        blank=True,
        help_text=_(
            "Groups of countries to add to the zone, by their codes such as 'eu' or "
            "'europe', separated by commas."
        ),
    )
    postcode_patterns = PostcodePatternsField(
        default=list,
        blank=True,
        help_text=_(
            "To limit the zone to some postcodes, list them separated by commas: a prefix "
            "such as 'BT*', a range such as '70000..74999' or an exact postcode."
        ),
    )
    states = StatesField(
        default=list,
        blank=True,
        help_text=_(
            "To limit the zone to some provinces or states, list their codes separated by "
            "commas, for example 'SS' or 'CA'."
        ),
    )

    class Meta:
        ordering = ["priority", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "code"], nulls_distinct=False, name="shipping_zone_vendor_code"
            ),
            models.CheckConstraint(
                condition=models.Q(priority__gte=MIN_PRIORITY, priority__lte=MAX_PRIORITY),
                name="shipping_zone_priority_range",
            ),
            models.CheckConstraint(
                condition=models.Q(countries__len__gte=1) | models.Q(country_groups__len__gte=1),
                name="shipping_zone_has_countries",
            ),
            models.CheckConstraint(
                condition=~models.Q(countries__contains=[engine.WORLD])
                | models.Q(
                    countries=[engine.WORLD],
                    country_groups=[],
                    postcode_patterns=[],
                    states=[],
                ),
                name="shipping_zone_world_alone",
            ),
        ]

    def __str__(self) -> str:
        owner = "platform" if self.vendor_id is None else str(self.vendor)
        return f"{self.name} ({self.code}, {owner})"

    def clean(self) -> None:
        errors: dict[str, str] = {}
        known = set(
            CountryGroup.objects.filter(code__in=self.country_groups).values_list("code", flat=True)
        )
        unknown = sorted(set(self.country_groups) - known)
        if unknown:
            errors["country_groups"] = f"Unknown country groups: {', '.join(unknown)}."
        if (
            self.vendor_id is not None
            and self.pk is not None
            and self.methods.exclude(vendor_id=self.vendor_id).exists()
        ):
            errors["vendor"] = "Other vendors ship to this zone: it must stay a platform zone."
        errors.update(self._entry_errors())
        if errors:
            raise ValidationError(errors)

    def _entry_errors(self) -> dict[str, str]:
        """Patterns and provinces belong to one country: in a zone that covers several (or the
        world) each must start with `CC:`, and that country must be one the zone covers."""
        covered = self.effective_countries()
        single = engine.single_country(covered)
        errors: dict[str, str] = {}
        for name in ("postcode_patterns", "states"):
            entries: list[str] = getattr(self, name)
            try:
                countries = [split_country_qualifier(entry)[0] for entry in entries]
            except ValueError:
                continue  # a malformed entry is reported by the field itself
            loose = [e for e, country in zip(entries, countries, strict=True) if not country]
            outside = sorted({c for c in countries if c and c not in covered})
            if loose and not single:
                errors[name] = self._unqualified_message(loose, covered)
            elif outside:
                errors[name] = f"The zone does not cover {', '.join(outside)}."
        return errors

    @staticmethod
    def _unqualified_message(loose: list[str], covered: frozenset[str]) -> str:
        if engine.WORLD in covered:
            return "The rest of the world takes no postcode patterns or provinces."
        example = f" (for example {sorted(covered)[0]}:{loose[0]})" if covered else ""
        return f"{', '.join(loose)}: a zone with several countries needs a country on every entry{example}."  # noqa: E501

    def effective_countries(self) -> frozenset[str]:
        """Its explicit countries plus the members of its country groups."""
        groups = CountryGroup.objects.filter(code__in=self.country_groups)
        return frozenset(self.countries).union(*(group.countries for group in groups))


class OverMaxWeight(models.TextChoices):
    LAST_BAND = engine.LAST_BAND, "Charge the last band"
    QUOTE = engine.QUOTE, "Ask for a quote"
    BLOCK = engine.BLOCK, "Block the checkout"


class ServiceLevel(models.TextChoices):
    STANDARD = engine.STANDARD, "Standard"
    EXPRESS = engine.EXPRESS, "Express"
    ECONOMY = engine.ECONOMY, "Economy"
    OTHER = engine.OTHER, "Other"


class ShippingTaxTreatment(models.TextChoices):
    FOLLOWS_GOODS = engine.FOLLOWS_GOODS, "Follows the goods (ancillary supply)"
    STANDARD_RATE = engine.STANDARD_RATE, "Standard rate"
    EXEMPT = engine.EXEMPT, "Exempt"


class ShippingMethod(BaseModel):
    """One way a vendor ships to a zone: a carrier, a service level, an indicative delivery
    time, its own weight bands and free-shipping threshold (SHP.12).

    A vendor may offer several methods to the same zone (owner decision superseding legacy case
    9, "one carrier per vendor and zone"); at most one of them is the default, preselected at
    checkout. Formerly `VendorZoneRate` (SHP.1): each stored rate became the default method of
    its vendor and zone.
    """

    OverMaxWeight = OverMaxWeight
    ServiceLevel = ServiceLevel
    TaxTreatment = ShippingTaxTreatment

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="shipping_methods",
        help_text=_("The vendor that offers this way of shipping."),
    )
    zone = models.ForeignKey(
        ShippingZone,
        on_delete=models.PROTECT,
        related_name="methods",
        help_text=_(
            "The zone this method ships to. A vendor can offer several methods to the same zone."
        ),
    )
    carrier = models.ForeignKey(
        Carrier,
        on_delete=models.PROTECT,
        related_name="methods",
        help_text=_("The carrier that carries the parcels with this method."),
    )
    key = models.SlugField(
        max_length=50,
        default=engine.STANDARD,
        help_text=_(
            "A short name without spaces for this method. The same vendor cannot use it "
            "twice in the same zone."
        ),
    )
    service_level = models.CharField(
        max_length=16,
        choices=ServiceLevel.choices,
        default=engine.STANDARD,
        help_text=_("How fast or how costly the service is: standard, express, economy or other."),
    )
    min_delivery_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "The fewest days a delivery takes. It is only an estimate. Not shown to buyers yet."
        ),
    )
    max_delivery_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "The most days a delivery takes. It is only an estimate. Not shown to buyers yet."
        ),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_(
            "Not used yet at checkout: marks the method to preselect once checkout uses shipping "
            "rates. A vendor can have only one default method per zone."
        ),
    )
    free_shipping_threshold = MoneyField(
        null=True,
        blank=True,
        help_text=_(
            "In the rate calculation, shipping is free when the goods in the package are worth at "
            "least this amount, without VAT. Leave it empty for no free shipping."
        ),
    )
    # Stored only (SHP.12): the tax engine applies it (TAX.4); D-03 is open.
    tax_treatment = models.CharField(
        "VAT treatment",
        max_length=16,
        choices=ShippingTaxTreatment.choices,
        default=engine.DEFAULT_TAX_TREATMENT,
        help_text=_(
            "How VAT is applied to the shipping price. Today only the choice that follows the "
            "goods works; the other choices are saved but block the shipping quote. Ask your "
            "accountant if you are not sure."
        ),
    )
    # Hook for D-06 (open): empty means "no policy of its own"; SHP.3 then applies the platform
    # default that D-06 decides. No policy is implemented here.
    over_max_weight = models.CharField(
        "weight above the last band",
        max_length=16,
        choices=OverMaxWeight.choices,
        blank=True,
        default=engine.PLATFORM_DEFAULT,
        help_text=_(
            "What the rate calculation should do for a parcel heavier than the last weight band: "
            "charge the last band, ask for a quote or block the checkout. Leave it empty to "
            "follow the platform default, which is to ask for a quote."
        ),
    )

    class Meta:
        ordering = ["vendor", "zone", "-is_default", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "zone", "key"], name="shipping_method_vendor_zone_key"
            ),
            models.UniqueConstraint(
                fields=["vendor", "zone"],
                condition=models.Q(is_default=True),
                name="shipping_method_one_default",
                violation_error_message="This vendor already has a default method for the zone.",
            ),
            models.CheckConstraint(
                condition=models.Q(service_level__in=engine.SERVICE_LEVELS),
                name="shipping_method_service_level_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_treatment__in=engine.TAX_TREATMENTS),
                name="shipping_method_tax_treatment_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    over_max_weight__in=(engine.PLATFORM_DEFAULT, *engine.OVER_MAX_WEIGHT_POLICIES)
                ),
                name="shipping_method_over_max_weight_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(min_delivery_days__isnull=True)
                | models.Q(max_delivery_days__isnull=True)
                | models.Q(min_delivery_days__lte=models.F("max_delivery_days")),
                name="shipping_method_delivery_days_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(free_shipping_threshold__isnull=True)
                | models.Q(free_shipping_threshold__gte=0),
                name="shipping_method_threshold_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} → {self.zone.code} via {self.carrier} ({self.key})"

    def translation(self, language: str) -> "Resolved[ShippingMethodTranslation]":
        """The name to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def clean(self) -> None:
        if self.zone_id is None or self.vendor_id is None:
            return
        zone_vendor = (
            ShippingZone.objects.filter(pk=self.zone_id).values_list("vendor_id", flat=True).first()
        )
        if zone_vendor not in (None, self.vendor_id):
            raise ValidationError({"zone": "This is another vendor's zone."})


class ShippingMethodTranslation(TranslationModel):
    translation_of = "method"
    translated_fields = ("name",)

    language = translation_language_field()
    status = translation_status_field()

    vendor_scope: ClassVar[str] = "method__vendor"
    objects = VendorScopedManager()

    method = models.ForeignKey(
        ShippingMethod, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(
        max_length=100,
        help_text=_(
            "The name of the method in this language, for example "
            "'Standard delivery'. Buyers will see it once checkout uses shipping."
        ),
    )

    class Meta(TranslationModel.Meta):
        ordering = ["method", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["method", "language"], name="shipping_methodtranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.method} [{self.language}]"


class NumRange(models.Func):
    """PostgreSQL `numrange(lower, upper, '(]')`: right-inclusive, NULL upper = unlimited."""

    function = "NUMRANGE"
    template = "%(function)s(%(expressions)s, '(]')"
    output_field = DecimalRangeField()


class WeightBand(BaseModel):
    """Net cost for package weights in (from_kg, to_kg] of one method; to_kg NULL = unlimited.

    Bands of a method never overlap (exclusion constraint); gaps are allowed and reported by the
    health check (SHP.8). Two methods of the same vendor and zone have independent bands.
    """

    vendor_scope: ClassVar[str] = "method__vendor"
    objects = VendorScopedManager()

    method = models.ForeignKey(ShippingMethod, on_delete=models.CASCADE, related_name="bands")
    from_kg = weight_field(
        default=0,
        help_text=_(
            "The band starts above this weight, in kg. A parcel of exactly this weight "
            "belongs to the band before."
        ),
    )
    to_kg = weight_field(
        null=True,
        blank=True,
        help_text=_(
            "The heaviest parcel of the band, in kg, included. Leave it empty for no upper limit."
        ),
    )
    cost_net = MoneyField(help_text=_("The shipping price for this band, without VAT."))
    surcharge_net = MoneyField(
        default=0,
        help_text=_("An extra amount added to the price of the band, without VAT. Use 0 for none."),
    )

    class Meta:
        ordering = ["method", "from_kg"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(from_kg__gte=0), name="shipping_band_from_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(to_kg__isnull=True) | models.Q(to_kg__gt=models.F("from_kg")),
                name="shipping_band_to_after_from",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_net__gte=0, surcharge_net__gte=0),
                name="shipping_band_amounts_not_negative",
            ),
            ExclusionConstraint(
                name="shipping_band_no_overlap",
                expressions=[
                    ("method", RangeOperators.EQUAL),
                    (NumRange("from_kg", "to_kg"), RangeOperators.OVERLAPS),
                ],
                violation_error_message="Weight bands of a method must not overlap.",
            ),
        ]

    def __str__(self) -> str:
        upper = "unlimited" if self.to_kg is None else f"{self.to_kg}"
        return f"{self.from_kg}-{upper} kg: {self.cost_net}"


class SpecialAreaKind(models.TextChoices):
    ISLAND = engine.ISLAND, "Island"
    REMOTE = engine.REMOTE, "Remote area"
    EXTRA_VAT = engine.EXTRA_VAT, "Extra-VAT territory"


class SpecialArea(BaseModel):
    """An island, a remote area or an extra-VAT territory: a postcode or province list inside
    its countries (SHP.14, spec 06 "Standard template and special areas").

    Platform data, like country groups: seeded with the lists of the standard template, edited
    by staff and written by platform files (a carrier's remote-area list is imported that way),
    every change audited; vendor files name them and never change them. An extra-VAT area names
    its TAX.1 territory by code (`territory_code`), never by a foreign key: tax and shipping are
    independent apps. Each shipping method sets the treatment it gives an area
    (`SpecialAreaTreatment`); without one, the default is an indicative cost subject to quote.
    """

    Kind = SpecialAreaKind

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text=_("A short name in lower case that is unique, for example 'it-sardinia'."),
    )
    name = models.CharField(
        max_length=100, help_text=_("The name of the area, for example 'Sardinia'.")
    )
    kind = models.CharField(
        max_length=16,
        choices=SpecialAreaKind.choices,
        help_text=_(
            "What kind of area it is: an island, a remote area or a territory with its own "
            "VAT rules."
        ),
    )
    countries = ZoneCountriesField(
        help_text=_(
            "The countries where the postcodes and provinces below apply: two-letter "
            "country codes such as 'IT' or 'DE', separated by commas."
        )
    )
    postcode_patterns = PostcodePatternsField(
        default=list,
        blank=True,
        help_text=_(
            "The postcodes in the area, separated by commas: a prefix such as '07*', a "
            "range such as '90000..98999' or an exact postcode."
        ),
    )
    states = StatesField(
        default=list,
        blank=True,
        help_text=_(
            "The provinces or states in the area, as codes separated by commas, for example"
            " 'SS' or 'NU'."
        ),
    )
    territory_code = TerritoryCodeField(
        blank=True,
        default="",
        help_text=_(
            "Only for a territory with its own VAT rules: the code of its tax territory, "
            "for example 'ES-CN'."
        ),
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code=Lower("code")), name="shipping_area_code_lower_case"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=engine.AREA_KINDS), name="shipping_area_kind_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(countries__len__gte=1), name="shipping_area_has_countries"
            ),
            models.CheckConstraint(
                condition=~models.Q(countries__contains=[engine.WORLD]),
                name="shipping_area_no_world_marker",
                violation_error_message='A special area lies in named countries, never "*".',
            ),
            models.CheckConstraint(
                condition=models.Q(postcode_patterns__len__gte=1) | models.Q(states__len__gte=1),
                name="shipping_area_has_list",
                violation_error_message="A special area needs postcode patterns or provinces.",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind=engine.EXTRA_VAT) & ~models.Q(territory_code="")
                    | ~models.Q(kind=engine.EXTRA_VAT) & models.Q(territory_code="")
                ),
                name="shipping_area_territory_iff_extra_vat",
                violation_error_message=(
                    "An extra-VAT area names its tax territory; other areas name none."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def clean_fields(self, exclude: Collection[str] | None = None) -> None:
        if isinstance(self.code, str):
            self.code = self.code.strip().lower()
        super().clean_fields(exclude=exclude)

    def snap(self) -> engine.SpecialAreaSnap:
        return engine.SpecialAreaSnap(
            code=self.code,
            kind=self.kind,
            countries=frozenset(self.countries),
            postcode_patterns=tuple(self.postcode_patterns),
            states=tuple(self.states),
            territory_code=self.territory_code,
        )


class AreaTreatmentMode(models.TextChoices):
    INDICATIVE = engine.INDICATIVE, "Indicative cost subject to quote"
    SURCHARGE = engine.SURCHARGE, "Rate with a surcharge"
    QUOTE_ONLY = engine.QUOTE_ONLY, "Quote only"
    UNAVAILABLE = engine.UNAVAILABLE, "Method not offered"
    BLOCKED = engine.BLOCKED, "Blocked"


class SurchargeType(models.TextChoices):
    FIXED = engine.FIXED, "Fixed amount"
    PERCENT = engine.PERCENT, "Percent of the cost"
    PER_KG = engine.PER_KG, "Amount per kg"


class SpecialAreaTreatment(BaseModel):
    """What one shipping method does for addresses in one special area (SHP.14).

    Without a row, the method gives the area the default: an indicative cost subject to quote.
    A row sets a surcharge (fixed, percent of the method's cost, or per kg; also allowed on an
    estimate), extra delivery days, quote only, unavailable (the method is not offered there) or
    blocked. The rules are `engine.treatment_errors` and the same check constraints.
    """

    Mode = AreaTreatmentMode
    SurchargeType = SurchargeType

    vendor_scope: ClassVar[str] = "method__vendor"
    objects = VendorScopedManager()

    method = models.ForeignKey(
        ShippingMethod, on_delete=models.CASCADE, related_name="area_treatments"
    )
    area = models.ForeignKey(
        SpecialArea,
        on_delete=models.PROTECT,
        related_name="treatments",
        help_text=_("The special area this treatment is for. A method has one treatment per area."),
    )
    mode = models.CharField(
        max_length=16,
        choices=AreaTreatmentMode.choices,
        default=engine.DEFAULT_TREATMENT_MODE,
        help_text=_(
            "What the rate calculation does for this method in this area: an estimated cost that "
            "needs a quote, a rate with a surcharge, quote only, not offered, or blocked. "
            "Without a treatment it gives an estimated cost that needs a quote."
        ),
    )
    surcharge_type = models.CharField(
        max_length=16,
        choices=SurchargeType.choices,
        blank=True,
        default=engine.NO_SURCHARGE,
        help_text=_(
            "How the surcharge is counted: a fixed amount, a percent of the method's cost, "
            "or an amount per kg. Needed for a rate with a surcharge."
        ),
    )
    surcharge = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=engine.ZERO_AMOUNT,
        help_text=_(
            "The surcharge: an amount without VAT for a fixed or per-kg surcharge, or a percentage."
        ),
    )
    extra_days = models.PositiveSmallIntegerField(
        default=0, help_text=_("Days added to the method's estimated delivery time in this area.")
    )

    class Meta:
        ordering = ["method", "area"]
        constraints = [
            models.UniqueConstraint(fields=["method", "area"], name="shipping_treatment_unique"),
            models.CheckConstraint(
                condition=models.Q(mode__in=engine.TREATMENT_MODES),
                name="shipping_treatment_mode_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    surcharge_type__in=(engine.NO_SURCHARGE, *engine.SURCHARGE_TYPES)
                ),
                name="shipping_treatment_surcharge_type_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(mode=engine.SURCHARGE, surcharge_type=engine.NO_SURCHARGE),
                name="shipping_treatment_surcharge_has_type",
            ),
            models.CheckConstraint(
                condition=models.Q(surcharge_type=engine.NO_SURCHARGE)
                | models.Q(mode__in=engine.PRICED_MODES),
                name="shipping_treatment_surcharge_only_priced",
            ),
            models.CheckConstraint(
                condition=models.Q(surcharge__gte=0)
                & (~models.Q(surcharge_type=engine.NO_SURCHARGE) | models.Q(surcharge=0)),
                name="shipping_treatment_surcharge_amount",
            ),
            models.CheckConstraint(
                condition=~models.Q(mode__in=engine.NO_DAYS_MODES) | models.Q(extra_days=0),
                name="shipping_treatment_no_days_without_delivery",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.method} in {self.area.code}: {self.mode}"

    def clean(self) -> None:
        if self.surcharge is None or self.extra_days is None:
            return
        errors = engine.treatment_errors(
            self.mode, self.surcharge_type, self.surcharge, self.extra_days
        )
        if errors:
            raise ValidationError(errors)

    def snap(self) -> engine.AreaTreatment:
        return engine.AreaTreatment(
            mode=self.mode,
            surcharge_type=self.surcharge_type,
            surcharge=self.surcharge,
            extra_days=self.extra_days,
        )


class PickupOption(BaseModel):
    """Local pickup at a vendor's address, offered in addition to the carrier (spec 06 rule 5)."""

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="pickup_options",
        help_text=_("The vendor whose address the parcel is collected from."),
    )
    key = models.SlugField(
        max_length=50,
        help_text=_(
            "A short name without spaces for this pickup point. The same vendor cannot use "
            "it twice."
        ),
    )
    cost_net = MoneyField(
        default=0, help_text=_("What pickup costs, without VAT. Use 0 for free pickup.")
    )
    address = models.TextField(
        help_text=_(
            "The address where buyers will collect their order, with street, number, town and "
            "postcode. Not shown to buyers yet."
        )
    )

    class Meta:
        ordering = ["vendor", "key"]
        constraints = [
            models.UniqueConstraint(fields=["vendor", "key"], name="shipping_pickup_vendor_key"),
            models.CheckConstraint(
                condition=models.Q(cost_net__gte=0), name="shipping_pickup_cost_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor}: {self.key}"

    def translation(self, language: str) -> "Resolved[PickupOptionTranslation]":
        return resolve_translation(self.translations.all(), language)


class PickupOptionTranslation(TranslationModel):
    translation_of = "pickup_option"
    translated_fields = ("label",)

    language = translation_language_field()
    status = translation_status_field()

    vendor_scope: ClassVar[str] = "pickup_option__vendor"
    objects = VendorScopedManager()

    pickup_option = models.ForeignKey(
        PickupOption, on_delete=models.CASCADE, related_name="translations"
    )
    label = models.CharField(
        max_length=100,
        help_text=_(
            "The name of the pickup point in this language, for example "
            "'Pick up at the workshop'. Buyers will see it once checkout uses shipping."
        ),
    )

    class Meta(TranslationModel.Meta):
        ordering = ["pickup_option", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["pickup_option", "language"], name="shipping_pickuptranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.pickup_option} [{self.language}]"


class ShippingTemplate(BaseModel):
    """A shipping configuration the platform proposes to vendors (SHP.13): zones, the country
    groups they use, methods with weight bands, VAT treatment and special-area treatments, and
    the special areas they name (SHP.14).

    `document` is a file of the configuration format (`apps.shipping.config_format`) without a
    vendor. A vendor copies it into its own configuration (`config_io.copy_template`): the
    template's zones become the vendor's zones, which it then customises; the template never
    changes with them. Saved only through `config_io.save_template`, which validates the document
    with the models' validators and audits the change.
    """

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    is_standard = models.BooleanField(
        default=False, help_text="The template the platform proposes; at most one."
    )
    document = models.JSONField(help_text="A shipping configuration file.")

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_standard"],
                condition=models.Q(is_standard=True),
                name="shipping_template_one_standard",
                violation_error_message="Another template is already the standard one.",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class ShippingConfigVersion(BaseModel):
    """A snapshot of the whole shipping configuration, for diff and one-click rollback.

    Replaces the legacy option backups. The editor (SHP.6) creates one on every save; rows are
    history and never edited.
    """

    snapshot = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Shipping configuration of {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("ShippingConfigVersion rows are history; never edited")
        super().save(*args, **kwargs)
