"""Typed attributes and their translated values (spec 03 §3.2, CAT.3).

Platform data shared by every vendor (no vendor scope), like the taxonomies: staff keep one
catalogue of attributes ("size", "color", "material", "bag_width"...), each of one kind:

- `choice`: values from the attribute's own list (`AttributeValue`), ordered by `position` (S, M,
  L), each with a translated label. Only choice attributes can be variant axes.
- `number`: one exact number per product, in the attribute's `unit` (e.g. cm).
- `text`: one language-neutral text per product (an author, an ISBN).

What a product says about an attribute is a `models.ProductAttribute`; the value a variant takes
on an axis is a `models.VariantOption`. Names and labels are `*Translation` rows
(`apps.core.models.TranslationModel`); visitors read them through `translation(language)`, with
the EN fallback.
"""

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved

from . import engine
from .taxonomy import code_validator

value_code_validator = RegexValidator(
    r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$",
    "Use lower case letters and digits, with single hyphens or _ between words.",
)


class AttributeKind(models.TextChoices):
    CHOICE = "choice", "Choice (values from a list)"
    NUMBER = "number", "Number"
    TEXT = "text", "Text"


if tuple(AttributeKind.values) != engine.ATTRIBUTE_KINDS:  # pragma: no cover - import guard
    raise ImportError("AttributeKind and engine.ATTRIBUTE_KINDS disagree")


def _source_text(rows: Any, field: str) -> str:
    """The `field` text in EN, else in the first language: staff labels only."""
    ordered = sorted(rows, key=lambda row: (not row.is_source, row.language))
    return str(getattr(ordered[0], field)) if ordered else ""


class Attribute(BaseModel):
    """A typed attribute of the platform's catalogue, e.g. size, color, bag_width (cm)."""

    Kind = AttributeKind

    code = models.SlugField(
        max_length=64,
        unique=True,
        validators=[code_validator],
        help_text="Stable key for imports and the API, e.g. size. Not shown to visitors.",
    )
    kind = models.CharField(max_length=16, choices=AttributeKind.choices)
    unit = models.CharField(
        max_length=16, blank=True, help_text="Number attributes only, e.g. cm, kg, ml."
    )
    position = models.PositiveIntegerField(
        default=0, help_text="Order among a product's attributes when it does not set one."
    )

    class Meta:
        ordering = ["position", "code"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(kind__in=AttributeKind.values),
                name="catalog_attribute_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(kind=AttributeKind.NUMBER) | models.Q(unit=""),
                name="catalog_attribute_unit_only_for_numbers",
                violation_error_message="Only a number attribute has a unit.",
            ),
        ]

    def __str__(self) -> str:
        return str(self.code)

    def translation(self, language: str) -> "Resolved[AttributeTranslation]":
        """The name to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def clean(self) -> None:
        if (
            self.pk is not None
            and self.kind != AttributeKind.CHOICE
            and AttributeValue.objects.filter(attribute_id=self.pk).exists()
        ):
            raise ValidationError({"kind": "This attribute has values: it must stay a choice."})


class AttributeTranslation(TranslationModel):
    """An attribute's name in one language ("Color", "Colore")."""

    translation_of = "attribute"
    translated_fields = ("name",)

    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(max_length=255)

    class Meta(TranslationModel.Meta):
        ordering = ["attribute", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["attribute", "language"], name="catalog_attributetranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"


class AttributeValue(BaseModel):
    """One value of a choice attribute ("red" of color); its label is translated."""

    attribute = models.ForeignKey(Attribute, on_delete=models.PROTECT, related_name="values")
    value = models.SlugField(
        max_length=64,
        validators=[value_code_validator],
        help_text="Stable key for imports and the API, e.g. red. Not shown to visitors.",
    )
    position = models.PositiveIntegerField(
        default=0, help_text="Order among the attribute's values (S, M, L): drives variant order."
    )

    class Meta:
        ordering = ["attribute", "position", "value"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["attribute", "value"],
                name="catalog_attributevalue_unique",
                violation_error_message="This attribute already has this value.",
            ),
            # Target of the variant options' composite foreign key (value, attribute).
            models.UniqueConstraint(
                fields=["id", "attribute"], name="catalog_attributevalue_id_attribute_unique"
            ),
        ]

    def __str__(self) -> str:
        if self.pk is None:
            return str(self.value)
        label = _source_text(self.translations.all(), "label")
        return f"{self.attribute.code}: {label or self.value}"

    def translation(self, language: str) -> "Resolved[AttributeValueTranslation]":
        """The label to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def clean(self) -> None:
        if self.attribute_id is not None and self.attribute.kind != AttributeKind.CHOICE:
            raise ValidationError({"attribute": "Only a choice attribute has a list of values."})


class AttributeValueTranslation(TranslationModel):
    """A value's label in one language ("Red", "Rosso")."""

    translation_of = "value"
    translated_fields = ("label",)

    value = models.ForeignKey(AttributeValue, on_delete=models.CASCADE, related_name="translations")
    label = models.CharField(max_length=255)

    class Meta(TranslationModel.Meta):
        ordering = ["value", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["value", "language"], name="catalog_attributevaluetranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} [{self.language}]"
