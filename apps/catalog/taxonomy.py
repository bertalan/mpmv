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
pages and their URLs (`/{taxonomy}/{term}/`, decision D-07) are in `views` and `urls` (CAT.8).

Descriptions: the `description` texts of categories, terms and brands are HTML as staff (or an
import) entered it, stored raw and never sanitized here. Whatever shows them to visitors (the
category, taxonomy and brand pages of CAT.8, CMS rendering) must sanitize them with an allow-list
first; never mark a stored description safe as it is.
"""

from collections.abc import Iterable
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _
from treebeard.mp_tree import MP_Node
from wagtail.models import Page

from apps.core import translations
from apps.core.fields import CountryField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved

from . import engine, slugs
from .fields import (
    language_field,
    seo_description_field,
    seo_title_field,
    translation_status_field,
)

url_slug_validator = RegexValidator(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    "Use lower case letters and digits, with single hyphens between words.",
)
code_validator = RegexValidator(
    r"^[a-z][a-z0-9_]*$", "Use lower case letters, digits and _, starting with a letter."
)


def _slug_field() -> models.SlugField[Any, Any]:
    return models.SlugField(
        max_length=200,
        validators=[url_slug_validator],
        help_text=_(
            "The last part of the web address in this language, for example 'summer-bags'. "
            "Required: use lower case letters, digits and single hyphens. It must differ from "
            "the others at the same level."
        ),
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

    language = language_field()
    status = translation_status_field()
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(
        max_length=255,
        help_text=_(
            "The name of the category in this language, for example 'Bags'. Required: without "
            "it the category has no name for buyers."
        ),
    )
    slug = _slug_field()
    description = models.TextField(
        blank=True,
        help_text=_(
            "An introduction shown on the category page, for example what you find in it. "
            "Basic formatting is allowed. Optional."
        ),
    )
    seo_title = seo_title_field()
    seo_description = seo_description_field()

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
        help_text=_(
            "A short fixed name for this set of categories or tags, for example 'season'. "
            "Required: lower case letters, digits and _, starting with a letter. Imports use "
            "it, so do not change it later; buyers never see it."
        ),
    )
    hierarchical = models.BooleanField(
        default=False,
        help_text=_(
            "Tick it if categories or tags in this set can sit inside others, for example "
            "maker and then model. Leave it unticked for a flat list."
        ),
    )
    filterable = models.BooleanField(
        default=True,
        help_text=_("Tick it to let buyers narrow a product list by these categories or tags."),
    )
    has_landing = models.BooleanField(
        "has landing pages",
        default=False,
        help_text=_(
            "Tick it to give each category or tag of this set its own page that buyers can "
            "open and share."
        ),
    )
    term_attributes = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Extra details to record for each category or tag, written as a list, for example "
            '[{"code": "model_year", "kind": "number", "required": false, "label": "Model '
            'year"}]. Leave it empty for none. The kind is one of: text, number, boolean.'
        ),
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

    language = language_field()
    status = translation_status_field()
    taxonomy = models.ForeignKey(Taxonomy, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(
        max_length=255,
        help_text=_(
            "The name of this set of categories or tags in this language, for example "
            "'Season'. Required: without it the set has no name for buyers."
        ),
    )
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

    def clean(self) -> None:
        """The slug is the first segment of the term pages (`/{taxonomy}/{term}/`, D-07): it
        cannot be a language prefix, a catalog segment or the slug of a top-level CMS page."""
        super().clean()
        if not self.slug:
            return
        languages = [str(code) for code, _name in settings.LANGUAGES]
        error = slugs.taxonomy_slug_error(self.slug, languages=languages)
        top_level_pages = Page.objects.filter(depth=CMS_TOP_LEVEL_DEPTH, slug=self.slug)
        if error is None and top_level_pages.exists():
            error = "A CMS page already uses this slug at the top of the site."
        if error is not None:
            raise ValidationError({"slug": error})


# Wagtail's tree root is depth 1 and each language's home page depth 2: top-level pages are 3.
CMS_TOP_LEVEL_DEPTH = 3


class Term(MP_Node, BaseModel):  # type: ignore[misc,django-manager-missing]  # untyped
    """A term of a taxonomy: a tree per taxonomy, flat when the taxonomy is not hierarchical."""

    taxonomy = models.ForeignKey(
        Taxonomy,
        on_delete=models.PROTECT,
        related_name="terms",
        help_text=_(
            "The set of categories or tags this one belongs to, for example 'season'. "
            "Required. It cannot be changed afterwards."
        ),
    )
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "The values of the extra details its set asks for, written as code and value, for "
            'example {"model_year": 2020}. Leave it empty if the set asks for none.'
        ),
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

    language = language_field()
    status = translation_status_field()
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(
        max_length=255,
        help_text=_(
            "The name of the category or tag in this language, for example 'Summer'. "
            "Required: without it it has no name for buyers."
        ),
    )
    slug = _slug_field()
    description = models.TextField(
        blank=True,
        help_text=_(
            "An introduction shown on the page of this category or tag. Basic formatting is "
            "allowed. Optional."
        ),
    )
    seo_title = seo_title_field()
    seo_description = seo_description_field()

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


class Manufacturer(BaseModel):
    """The company behind one or more brands (CAT.20): legal name, official URL, `sameAs` links
    and country. Shared by all vendors; edited by Catalog staff only."""

    legal_name = models.CharField(
        max_length=255,
        help_text=_(
            "The full registered name of the company that makes the products, for example "
            "'Acme Industries S.p.A.'. Required: the manufacturer cannot be saved without it."
        ),
    )
    url = models.URLField(
        blank=True,
        max_length=500,
        help_text=_(
            "The official website of the company, starting with https://, for example "
            "https://www.example.com. Optional."
        ),
    )
    same_as = models.JSONField(
        "sameAs links",
        default=list,
        blank=True,
        help_text=_(
            "Other pages that prove who the company is, such as Wikidata, Wikipedia or an "
            "official profile. Write a list of full https links. Optional."
        ),
    )
    country = CountryField(
        blank=True,
        help_text=_(
            "The country of the registered office of the company. Optional: leave it empty if "
            "you do not know it."
        ),
    )

    class Meta:
        ordering = ["legal_name"]

    def __str__(self) -> str:
        return str(self.legal_name)

    def clean(self) -> None:
        super().clean()
        errors = engine.link_field_errors(self.url, self.same_as)
        if errors:
            raise ValidationError(errors)


class Brand(BaseModel):
    """A brand (`g:brand` in feeds) as a linked entity (CAT.20); its name is the same in every
    language. One shared list for all vendors: a vendor picks any `approved` brand and proposes a
    missing one, which stays `pending` (not public) until Catalog staff approve, reject or merge
    it. Staff edit the shared data; vendors never do."""

    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending review"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(
        max_length=200,
        help_text=_(
            "The brand name as buyers know it, for example 'Acme'. Required: a brand cannot be "
            "saved without it. Two brands cannot have the same or a very similar name."
        ),
    )
    # What duplicate detection compares: `engine.brand_name_key(name)`, set by `save()`.
    name_key = models.CharField(max_length=200, blank=True, editable=False, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.APPROVED,
        db_index=True,
        help_text=_(
            "Approved brands can be picked for products and are public. Pending review brands "
            "wait for a check and are not public. Rejected brands cannot be used. If you leave "
            "it empty, the brand is approved."
        ),
    )
    wikidata_id = models.CharField(
        "Wikidata item ID",
        max_length=12,
        blank=True,
        help_text=_(
            "The brand's code on Wikidata, for example Q42. Optional. Only the format is "
            "checked: nothing is looked up online."
        ),
    )
    url = models.URLField(
        "official URL",
        blank=True,
        max_length=500,
        help_text=_(
            "The official website of the brand, starting with https://, for example "
            "https://www.example.com. Optional."
        ),
    )
    same_as = models.JSONField(
        "sameAs links",
        default=list,
        blank=True,
        help_text=_(
            "Other pages that prove who the brand is, such as Wikipedia in each language or an "
            "official profile. Write a list of full https links. Optional."
        ),
    )
    logo = models.ForeignKey(
        "core.MediaImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("The brand logo, shown on the brand page. Optional."),
    )
    logo_alt = models.CharField(
        max_length=125,
        blank=True,
        help_text=_(
            "Describe the logo in a few words for people who cannot see it, for example 'Acme, "
            "red letters on white'. Required when you add a logo; up to 125 characters."
        ),
    )
    manufacturer = models.ForeignKey(
        Manufacturer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="brands",
        help_text=_(
            "The company that makes products under this brand. Optional: leave it empty if you "
            "do not know it."
        ),
    )

    class Meta:
        ordering = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                Lower("name"),
                name="catalog_brand_name_unique",
                violation_error_message="A brand with this name already exists.",
            ),
            models.UniqueConstraint(
                fields=["wikidata_id"],
                condition=~models.Q(wikidata_id=""),
                name="catalog_brand_wikidata_unique",
                violation_error_message="Another brand has this Wikidata ID.",
            ),
        ]

    def __str__(self) -> str:
        return str(self.name)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name_key = engine.brand_name_key(self.name)
        fields = kwargs.get("update_fields")
        if fields is not None and "name" in fields:
            kwargs["update_fields"] = {*fields, "name_key"}
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = engine.link_field_errors(self.url, self.same_as)
        problem = engine.wikidata_id_error(self.wikidata_id)
        if problem:
            errors.setdefault("wikidata_id", []).append(problem)
        others = Brand.objects.exclude(pk=self.pk)
        key = engine.brand_name_key(self.name)
        # Only a new brand or a rename is checked: a legacy brand whose near-duplicate already
        # exists stays editable. No unique constraint on `name_key`, so existing data migrates;
        # imports and proposals (CAT.20 2/3) must call `full_clean()`.
        renamed = (
            self._state.adding or not Brand.objects.filter(pk=self.pk, name=self.name).exists()
        )
        if key and renamed and others.filter(name_key=key).exists():
            errors.setdefault("name", []).append("A brand with a very similar name exists.")
        if self.logo_id and not self.logo_alt.strip():
            errors.setdefault("logo_alt", []).append("Describe the logo for screen readers.")
        if self.wikidata_id and others.filter(wikidata_id=self.wikidata_id).exists():
            errors.setdefault("wikidata_id", []).append("Another brand has this Wikidata ID.")
        if errors:
            raise ValidationError(errors)

    def translation(self, language: str) -> "Resolved[BrandTranslation]":
        """Texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)


class BrandTranslation(TranslationModel):
    """A brand's slug, description and SEO texts in one language."""

    translation_of = "brand"
    translated_fields = ("description", "seo_title", "seo_description")

    language = language_field()
    status = translation_status_field()
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="translations")
    slug = _slug_field()
    description = models.TextField(
        blank=True,
        help_text=_(
            "A short story of the brand, shown on the brand page. Basic formatting is "
            "allowed. Optional."
        ),
    )
    seo_title = seo_title_field()
    seo_description = seo_description_field()

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
