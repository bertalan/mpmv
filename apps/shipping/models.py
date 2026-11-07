"""Shipping configuration: carriers, country groups, zones, shipping methods, weight bands,
pickup, versions (spec 06 §6.2, SHP.1, SHP.12).

The integrity rules are database constraints, so imports (SHP.5, SHP.13), the admin editor
(SHP.6) and the API cannot bypass them: at most one default method per vendor and zone, weight
bands of a method that never overlap (an exclusion constraint), non-negative net amounts. Rules
that span tables (a method uses a platform zone or its vendor's own zone; a zone's country groups
exist) are model validation, which every write runs through `apps.shipping.services`.

Amounts are net of VAT: the tax engine adds VAT (spec 06 rule 2), following each method's stored
`tax_treatment`. Orders keep snapshots of what they used, never references to these rows.

Versioning: the configuration is versioned as a whole by `ShippingConfigVersion` snapshots
(spec 06 replaces the legacy option backups with it), not by validity periods on each row.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DecimalRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.fields import MoneyField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved
from apps.vendors.scoping import VendorScopedManager

from . import engine
from .fields import CountryGroupCodesField, PostcodePatternsField, StatesField, ZoneCountriesField

MIN_PRIORITY = 1
MAX_PRIORITY = 999


def weight_field(**kwargs: Any) -> models.DecimalField[Any, Any]:
    """Weight in kg with 3 decimals (spec 06: sum of weight x quantity, 3 decimals)."""
    return models.DecimalField(max_digits=9, decimal_places=3, **kwargs)


class Carrier(BaseModel):
    """A way of shipping (main courier, the vendor's own carrier, an aggregator...).

    An inactive carrier offers no rates at all (spec 06 §6.3 case 10, SHP.3).
    """

    code = models.SlugField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    tracking_url_template = models.CharField(
        max_length=500,
        blank=True,
        help_text="Tracking link, with {tracking_number} where the number goes.",
    )
    supports_api = models.BooleanField(
        "supports API", default=False, help_text="Labels and tracking through the carrier API."
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

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="translations")
    label = models.CharField(max_length=100)

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
    its code and cannot be deleted (`apps.shipping.services.delete_config`).
    """

    code = models.SlugField(max_length=50, unique=True)
    countries = ZoneCountriesField(help_text="ISO country codes of the members, comma separated.")

    class Meta:
        ordering = ["code"]
        constraints = [
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

    country_group = models.ForeignKey(
        CountryGroup, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(max_length=100)

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
        help_text="Empty: a platform zone for every vendor. Set: that vendor's own zone.",
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    priority = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_PRIORITY), MaxValueValidator(MAX_PRIORITY)],
        help_text="1 to 999; the lowest matching priority wins.",
    )
    countries = ZoneCountriesField(
        default=list,
        blank=True,
        help_text='ISO country codes, comma separated, or "*" for the rest of the world.',
    )
    country_groups = CountryGroupCodesField(
        default=list,
        blank=True,
        help_text="Country group codes (eu, europe, africa…), comma separated.",
    )
    postcode_patterns = PostcodePatternsField(
        default=list,
        blank=True,
        help_text='Prefix "BT*", range "70000..74999" or exact postcode, comma separated.',
    )
    states = StatesField(
        default=list,
        blank=True,
        help_text="Province or state codes (SS, CA…), comma separated.",
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
                | models.Q(countries=[engine.WORLD], country_groups=[]),
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
        if errors:
            raise ValidationError(errors)

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
        "vendors.Vendor", on_delete=models.CASCADE, related_name="shipping_methods"
    )
    zone = models.ForeignKey(ShippingZone, on_delete=models.PROTECT, related_name="methods")
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name="methods")
    key = models.SlugField(
        max_length=50, default=engine.STANDARD, help_text="Unique per vendor and zone."
    )
    service_level = models.CharField(
        max_length=16, choices=ServiceLevel.choices, default=engine.STANDARD
    )
    min_delivery_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Indicative, shown to the buyer."
    )
    max_delivery_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Indicative, shown to the buyer."
    )
    is_default = models.BooleanField(
        default=False, help_text="Preselected at checkout; at most one per vendor and zone."
    )
    free_shipping_threshold = MoneyField(
        null=True,
        blank=True,
        help_text="Net goods value of the package from which shipping is free. Empty: never.",
    )
    # Stored only (SHP.12): the tax engine applies it (TAX.4); D-03 is open.
    tax_treatment = models.CharField(
        "VAT treatment",
        max_length=16,
        choices=ShippingTaxTreatment.choices,
        default=engine.DEFAULT_TAX_TREATMENT,
    )
    # Hook for D-06 (open): empty means "no policy of its own"; SHP.3 then applies the platform
    # default that D-06 decides. No policy is implemented here.
    over_max_weight = models.CharField(
        "weight above the last band",
        max_length=16,
        choices=OverMaxWeight.choices,
        blank=True,
        default=engine.PLATFORM_DEFAULT,
        help_text="Empty: the platform default applies (decision D-06).",
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

    vendor_scope: ClassVar[str] = "method__vendor"
    objects = VendorScopedManager()

    method = models.ForeignKey(
        ShippingMethod, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(max_length=100)

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
    from_kg = weight_field(default=0, help_text="Excluded: the band starts above this weight.")
    to_kg = weight_field(null=True, blank=True, help_text="Included. Empty: no upper limit.")
    cost_net = MoneyField(help_text="Net of VAT.")
    surcharge_net = MoneyField(default=0, help_text="Net of VAT.")

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


class PickupOption(BaseModel):
    """Local pickup at a vendor's address, offered in addition to the carrier (spec 06 rule 5)."""

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.CASCADE, related_name="pickup_options"
    )
    key = models.SlugField(max_length=50)
    cost_net = MoneyField(default=0, help_text="Net of VAT.")
    address = models.TextField()

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

    vendor_scope: ClassVar[str] = "pickup_option__vendor"
    objects = VendorScopedManager()

    pickup_option = models.ForeignKey(
        PickupOption, on_delete=models.CASCADE, related_name="translations"
    )
    label = models.CharField(max_length=100)

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
