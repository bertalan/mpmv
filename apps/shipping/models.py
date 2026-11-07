"""Shipping configuration: carriers, zones, vendor rates, weight bands, pickup, versions
(spec 06 §6.2, SHP.1).

The integrity rules are database constraints, so imports (SHP.5), the admin editor (SHP.6) and
the API cannot bypass them: one carrier per vendor and zone, weight bands that never overlap
(an exclusion constraint), non-negative net amounts. Amounts are net of VAT: the tax engine adds
VAT (spec 06 rule 2). Orders keep snapshots of what they used, never references to these rows.

Versioning: the configuration is versioned as a whole by `ShippingConfigVersion` snapshots
(spec 06 replaces the legacy option backups with it), not by validity periods on each row.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, DecimalRangeField, RangeOperators
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.fields import MoneyField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved
from apps.vendors.scoping import VendorScopedManager

from . import engine
from .fields import ZoneCountriesField

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


class ShippingZone(BaseModel):
    """A destination area: countries, optionally narrowed by postcode patterns or states.

    Zones are resolved by priority, lowest first (SHP.2); `["*"]` is the rest of the world,
    used only when no other zone matches.
    """

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    priority = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_PRIORITY), MaxValueValidator(MAX_PRIORITY)],
        help_text="1 to 999; the lowest matching priority wins.",
    )
    countries = ZoneCountriesField(
        help_text='ISO country codes, comma separated, or "*" for the rest of the world.'
    )
    postcode_patterns = ArrayField(
        models.CharField(max_length=32),
        default=list,
        blank=True,
        help_text='Wildcard "BT*", range "70000-74999" or exact postcode, comma separated.',
    )
    states = ArrayField(models.CharField(max_length=32), default=list, blank=True)

    class Meta:
        ordering = ["priority", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(priority__gte=MIN_PRIORITY, priority__lte=MAX_PRIORITY),
                name="shipping_zone_priority_range",
            ),
            models.CheckConstraint(
                condition=models.Q(countries__len__gte=1), name="shipping_zone_has_countries"
            ),
            models.CheckConstraint(
                condition=~models.Q(countries__contains=[engine.WORLD])
                | models.Q(countries=[engine.WORLD]),
                name="shipping_zone_world_alone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class OverMaxWeight(models.TextChoices):
    LAST_BAND = engine.LAST_BAND, "Charge the last band"
    QUOTE = engine.QUOTE, "Ask for a quote"
    BLOCK = engine.BLOCK, "Block the checkout"


class VendorZoneRate(BaseModel):
    """The carrier a vendor ships with to a zone, and its weight bands.

    One carrier per vendor and zone (spec 06 §6.3 case 9): the legacy "the first one wins" is a
    unique constraint here.
    """

    OverMaxWeight = OverMaxWeight

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.CASCADE, related_name="shipping_rates"
    )
    zone = models.ForeignKey(ShippingZone, on_delete=models.PROTECT, related_name="rates")
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name="rates")
    # Hook for D-06 (open): empty means "no policy of its own"; SHP.3 then applies the platform
    # default that D-06 decides. SHP.1 deliberately implements no policy.
    over_max_weight = models.CharField(
        "weight above the last band",
        max_length=16,
        choices=OverMaxWeight.choices,
        blank=True,
        default=engine.PLATFORM_DEFAULT,
        help_text="Empty: the platform default applies (decision D-06).",
    )

    class Meta:
        ordering = ["vendor", "zone"]
        constraints = [
            models.UniqueConstraint(fields=["vendor", "zone"], name="shipping_rate_vendor_zone"),
            models.CheckConstraint(
                condition=models.Q(
                    over_max_weight__in=(engine.PLATFORM_DEFAULT, *engine.OVER_MAX_WEIGHT_POLICIES)
                ),
                name="shipping_rate_over_max_weight_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} → {self.zone.code} via {self.carrier}"


class NumRange(models.Func):
    """PostgreSQL `numrange(lower, upper, '(]')`: right-inclusive, NULL upper = unlimited."""

    function = "NUMRANGE"
    template = "%(function)s(%(expressions)s, '(]')"
    output_field = DecimalRangeField()


class WeightBand(BaseModel):
    """Net cost for package weights in (from_kg, to_kg] of one rate; to_kg NULL = unlimited.

    Bands of a rate never overlap (exclusion constraint); gaps are allowed and reported by the
    health check (SHP.8).
    """

    vendor_scope: ClassVar[str] = "rate__vendor"
    objects = VendorScopedManager()

    rate = models.ForeignKey(VendorZoneRate, on_delete=models.CASCADE, related_name="bands")
    from_kg = weight_field(default=0, help_text="Excluded: the band starts above this weight.")
    to_kg = weight_field(null=True, blank=True, help_text="Included. Empty: no upper limit.")
    cost_net = MoneyField(help_text="Net of VAT.")
    surcharge_net = MoneyField(default=0, help_text="Net of VAT.")

    class Meta:
        ordering = ["rate", "from_kg"]
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
                    ("rate", RangeOperators.EQUAL),
                    (NumRange("from_kg", "to_kg"), RangeOperators.OVERLAPS),
                ],
                violation_error_message="Weight bands of a rate must not overlap.",
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
