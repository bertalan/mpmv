"""Category tree, configurable taxonomies and their terms, brands, and their texts (spec 03 §3.2,
CAT.2).

These are platform data, shared by every vendor (no vendor scope). The platform serves any
market, so taxonomies are data, not code: staff create a taxonomy ("compatibility", "occasion",
"season"...), say whether its terms nest, whether it filters listings and whether its terms get
landing pages, and declare the extra fields of its terms (`term_attributes`, validated by
`engine.term_schema_errors`). Products link to them in `models.Product`.

Trees: categories and terms are treebeard materialized-path nodes (`MP_Node`). Treebeard is
already installed with Wagtail, whose pages use the same structure; a path gives the subtree and
the ancestors with one indexed `LIKE 'prefix%'` or `IN` query, which listings, breadcrumbs and
the derived upper term levels need, and siblings keep a manual order. Terms of every taxonomy
live in one table: each taxonomy's top-level terms are roots of the shared forest, and a child
always belongs to its parent's taxonomy. Move nodes with treebeard (`move`), never by writing
`path`: `Term.move` refuses, like the forms, a place its taxonomy does not allow.

Texts: one `*Translation` row per language (`apps.core.models.TranslationModel`), EN only at
go-live but ready for every language in settings.LANGUAGES. The slug is a URL segment per
language: it is unique among a node's siblings in a language (checked by `clean()`, so by forms
and the admin), and unique per language for taxonomies and brands (database constraints). The
URLs of taxonomy pages wait for decision D-07 (CAT.8): only the data is here.
"""

from collections.abc import Iterable
from typing import Any, ClassVar

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from treebeard.mp_tree import MP_Node

from apps.core import translations
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved

from . import engine

url_slug_validator = RegexValidator(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    "Use lower case letters and digits, with single hyphens between words.",
)
code_validator = RegexValidator(
    r"^[a-z][a-z0-9_]*$", "Use lower case letters, digits and _, starting with a letter."
)


def _slug_field() -> models.SlugField[Any, Any]:
    return models.SlugField(
        max_length=200, validators=[url_slug_validator], help_text="URL segment in this language."
    )


def _staff_label(rows: Iterable[TranslationModel], field: str) -> str:
    """The `field` text in the source language, else in the first language: staff labels only.

    Visitors read texts through `resolve_translation` (published only, explicit fallback).
    """
    source = translations.source_language()
    ordered = sorted(rows, key=lambda row: (row.language != source, row.language))
    return str(getattr(ordered[0], field)) if ordered else ""


class Category(MP_Node, BaseModel):  # type: ignore[misc,django-manager-missing]  # untyped
    """A node of the product category tree; siblings keep a manual order."""

    class Meta:
        ordering = ["path"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        if self.pk is None:
            return "New category"
        return _staff_label(self.translations.all(), "name") or f"Category {self.pk}"

    def translation(self, language: str) -> "Resolved[CategoryTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class CategoryTranslation(TranslationModel):
    """A category's name, slug, description and SEO texts in one language."""

    translation_of = "category"
    translated_fields = ("name", "description", "seo_title", "seo_description")

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(max_length=255)
    slug = _slug_field()
    description = models.TextField(blank=True, help_text="HTML, shown on the category page.")
    seo_title = models.CharField("SEO title", max_length=255, blank=True)
    seo_description = models.TextField("SEO description", blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["category", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["category", "language"], name="catalog_categorytranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"

    def clean(self) -> None:
        super().clean()
        try:
            category = self.category
        except Category.DoesNotExist:
            return
        if not category.path or not self.slug:
            return  # not in the tree yet: checked again once placed
        siblings = category.get_siblings().exclude(pk=category.pk)
        taken = CategoryTranslation.objects.filter(
            category__in=siblings, language=self.language, slug=self.slug
        )
        if taken.exists():
            raise ValidationError(
                {"slug": "Another category at this level already uses this slug in this language."}
            )


class Taxonomy(BaseModel):  # type: ignore[django-manager-missing]  # Term is untyped
    """A navigation taxonomy configured by staff, e.g. "compatibility" (maker → model)."""

    code = models.SlugField(
        max_length=64,
        unique=True,
        validators=[code_validator],
        help_text="Stable key for imports and the API, e.g. compatibility. Not shown to visitors.",
    )
    hierarchical = models.BooleanField(
        default=False, help_text="Terms can have child terms (e.g. maker → model)."
    )
    filterable = models.BooleanField(default=True, help_text="Offered as a listing filter.")
    has_landing = models.BooleanField(
        "has landing pages", default=False, help_text="Each term gets its own page."
    )
    term_attributes = models.JSONField(
        default=list,
        blank=True,
        help_text='Extra fields of the terms: a list like [{"code": "model_year", "kind": '
        '"number", "required": false, "label": "Model year"}]; kinds: '
        + ", ".join(engine.TERM_ATTRIBUTE_KINDS)
        + ".",
    )

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "taxonomies"

    def __str__(self) -> str:
        return str(self.code)

    def translation(self, language: str) -> "Resolved[TaxonomyTranslation]":
        return resolve_translation(self.translations.all(), language)

    def clean(self) -> None:
        if self.term_attributes is None:
            self.term_attributes = []
        errors: dict[str, list[str]] = {}
        schema_errors = engine.term_schema_errors(self.term_attributes)
        if schema_errors:
            errors["term_attributes"] = schema_errors
        if (
            not self.hierarchical
            and self.pk is not None
            and Term.objects.filter(taxonomy=self, depth__gt=1).exists()
        ):
            errors["hierarchical"] = [
                "Some terms of this taxonomy have children: move them to the top level first."
            ]
        if errors:
            raise ValidationError(errors)


class TaxonomyTranslation(TranslationModel):
    """A taxonomy's name and URL segment in one language."""

    translation_of = "taxonomy"
    translated_fields = ("name",)

    taxonomy = models.ForeignKey(Taxonomy, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(max_length=255)
    slug = _slug_field()

    class Meta(TranslationModel.Meta):
        ordering = ["taxonomy", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["taxonomy", "language"], name="catalog_taxonomytranslation_unique"
            ),
            models.UniqueConstraint(
                fields=["language", "slug"],
                name="catalog_taxonomytranslation_slug_unique",
                violation_error_message="Another taxonomy already uses this slug in this language.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"


class Term(MP_Node, BaseModel):  # type: ignore[misc,django-manager-missing]  # untyped
    """A term of a taxonomy: a tree per taxonomy, flat when the taxonomy is not hierarchical."""

    taxonomy = models.ForeignKey(Taxonomy, on_delete=models.PROTECT, related_name="terms")
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Values of the extra fields the taxonomy declares, by code.",
    )

    class Meta:
        ordering = ["path"]

    def __str__(self) -> str:
        if self.pk is None:
            return "New term"
        name = _staff_label(self.translations.all(), "name") or f"Term {self.pk}"
        return f"{self.taxonomy.code}: {name}"

    def translation(self, language: str) -> "Resolved[TermTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def placement_error(self, parent: "Term | None") -> str | None:
        """Why this term cannot be a child of `parent` (None: a top-level term)."""
        return engine.term_placement_error(
            hierarchical=self.taxonomy.hierarchical,
            taxonomy=self.taxonomy_id,
            parent_taxonomy=None if parent is None else parent.taxonomy_id,
        )

    def clean(self) -> None:
        if self.attributes is None:
            self.attributes = {}
        if self.taxonomy_id is None:
            return
        errors: dict[str, list[str]] = {}
        attribute_errors = engine.term_attribute_errors(
            self.taxonomy.term_attributes, self.attributes
        )
        if attribute_errors:
            errors["attributes"] = attribute_errors
        if self.path:
            placement = self.placement_error(self.get_parent())
            if placement:
                errors[NON_FIELD_ERRORS] = [placement]
        if errors:
            raise ValidationError(errors)

    def move(self, target: "Term", pos: str | None = None) -> None:
        """Treebeard's move, refusing (ValidationError) a place the taxonomy does not allow.

        Every move runs through here, the admin form's and a direct call alike. Treebeard's
        child positions end in "-child"; the others (and no position) make a sibling of `target`.
        """
        child = pos is not None and pos.endswith("-child")
        placement = self.placement_error(target if child else target.get_parent())
        if placement:
            raise ValidationError(placement)
        super().move(target, pos)

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Treebeard sets the path before the first save: refuse a child in the wrong place.
        if self._state.adding and self.path:
            placement = self.placement_error(self.get_parent())
            if placement:
                raise ValidationError(placement)
        super().save(*args, **kwargs)


class TermTranslation(TranslationModel):
    """A term's name, slug, description (the SEO intro of its page) and SEO texts."""

    translation_of = "term"
    translated_fields = ("name", "description", "seo_title", "seo_description")

    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(max_length=255)
    slug = _slug_field()
    description = models.TextField(blank=True, help_text="HTML, shown on the term page.")
    seo_title = models.CharField("SEO title", max_length=255, blank=True)
    seo_description = models.TextField("SEO description", blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["term", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["term", "language"], name="catalog_termtranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"

    def clean(self) -> None:
        super().clean()
        try:
            term = self.term
        except Term.DoesNotExist:
            return
        if not term.path or not self.slug:
            return  # not in the tree yet: checked again once placed
        siblings = term.get_siblings().filter(taxonomy_id=term.taxonomy_id).exclude(pk=term.pk)
        taken = TermTranslation.objects.filter(
            term__in=siblings, language=self.language, slug=self.slug
        )
        if taken.exists():
            raise ValidationError(
                {
                    "slug": "Another term at this level of the taxonomy already uses this slug "
                    "in this language."
                }
            )


class Brand(BaseModel):
    """A manufacturer brand (`g:brand` in feeds); its name is the same in every language."""

    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                Lower("name"),
                name="catalog_brand_name_unique",
                violation_error_message="A brand with this name already exists.",
            ),
        ]

    def __str__(self) -> str:
        return str(self.name)

    def translation(self, language: str) -> "Resolved[BrandTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class BrandTranslation(TranslationModel):
    """A brand's slug, description and SEO texts in one language."""

    translation_of = "brand"
    translated_fields = ("description", "seo_title", "seo_description")

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="translations")
    slug = _slug_field()
    description = models.TextField(blank=True, help_text="HTML, shown on the brand page.")
    seo_title = models.CharField("SEO title", max_length=255, blank=True)
    seo_description = models.TextField("SEO description", blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["brand", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["brand", "language"], name="catalog_brandtranslation_unique"
            ),
            models.UniqueConstraint(
                fields=["language", "slug"],
                name="catalog_brandtranslation_slug_unique",
                violation_error_message="Another brand already uses this slug in this language.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.brand} [{self.language}]"
