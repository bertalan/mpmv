"""Staff admin for products, variants and their translations (Wagtail admin replaces this in
CAT.11).

Price fields stay editable, but a change to them on an existing product or variant is saved
through the audited price services, never as a plain model save.
"""

from collections.abc import Iterable
from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import models
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest
from treebeard.forms import TreeNodeChoiceField

from apps.core.audit import audit_context

from . import engine
from . import taxonomy_admin as taxonomy_admin  # registers the CAT.2 admins
from .models import Category, Product, ProductTranslation, Term, Variant, VariantTranslation
from .services import (
    PRODUCT_PRICE_FIELDS,
    VARIANT_PRICE_FIELDS,
    check_upsells,
    set_product_price,
    set_variant_price,
)

TIMESTAMPS = ("public_id", "created_at", "updated_at")


def take_price_change(
    instance: models.Model, changed_data: Iterable[str], price_fields: tuple[str, ...]
) -> dict[str, Any] | None:
    """The new prices of an existing row whose price fields changed in a form, else None.

    The stored prices are put back on `instance`, so the plain admin save keeps them and the
    caller then applies the new ones through the price service, which computes the change
    against the database and audits it. The admin wraps the request in one transaction. New
    rows keep their prices: a first price is not a price change.
    """
    if instance.pk is None or not set(price_fields) & set(changed_data):
        return None
    new_prices = {name: getattr(instance, name) for name in price_fields}
    stored = type(instance)._default_manager.filter(pk=instance.pk).values(*price_fields).get()
    for name, value in stored.items():
        setattr(instance, name, value)
    return new_prices


class ProductTranslationInline(admin.StackedInline[ProductTranslation, Product]):
    model = ProductTranslation
    extra = 1
    fields = (
        "language",
        "status",
        "title",
        "short_description",
        "description",
        "slug",
        "seo_title",
        "seo_description",
    )


class VariantInlineFormSet(BaseInlineFormSet[Variant, Product, forms.ModelForm[Variant]]):
    def clean(self) -> None:
        """Two variants of one submission with the same SKU (the database would refuse them)."""
        super().clean()
        seen: set[str] = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form in self.deleted_forms:
                continue
            sku = engine.canonical_sku(form.cleaned_data.get("sku") or "")
            if not sku:
                continue
            if sku in seen:
                form.add_error("sku", "Two variants cannot have the same SKU.")
            seen.add(sku)


class VariantInline(admin.TabularInline[Variant, Product]):
    model = Variant
    formset = VariantInlineFormSet
    extra = 0
    show_change_link = True  # translations are edited on the variant page
    fields = (
        "sku",
        "position",
        "is_default",
        "stock_qty",
        "stock_status",
        "price_net_override",
        "weight_kg_override",
    )


class ProductAdminForm(forms.ModelForm[Product]):
    """Reports a suggestion refused by the cross-vendor consent rule (CAT.14) as a form error,
    before the many-to-many save would refuse it."""

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        upsells = self.cleaned_data.get("upsells")
        vendor = self.cleaned_data.get("vendor") or getattr(self.instance, "vendor", None)
        if upsells and vendor is not None:
            try:
                check_upsells({vendor.pk}, [product.pk for product in upsells])
            except ValidationError as exc:
                self.add_error("upsells", exc)
        return cleaned


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin[Product]):
    form = ProductAdminForm
    list_display = ("sku", "vendor", "kind", "status", "price_net", "stock_qty", "stock_status")
    list_filter = ("status", "kind", "sales_mode", "fiscal_category", "stock_status", "vendor")
    search_fields = ("sku", "gtin", "translations__title")
    list_select_related = ("vendor",)
    raw_id_fields = ("vendor", "required_companion", "upsells")
    autocomplete_fields = ("terms",)  # terms can be thousands: searched, never listed
    readonly_fields = ("status", *TIMESTAMPS)
    fieldsets = (
        (None, {"fields": ("vendor", "sku", "kind", "status", "public_id")}),
        (
            "Sales and prices",
            {
                "fields": (
                    "sales_mode",
                    "fiscal_category",
                    "price_net",
                    "sale_price_net",
                    "sale_from",
                    "sale_to",
                )
            },
        ),
        ("Stock", {"fields": ("stock_policy", "stock_qty", "stock_status")}),
        ("Shipping", {"fields": ("weight_kg", "length_cm", "width_cm", "height_cm")}),
        (
            "Classification",
            {"fields": ("category", "secondary_categories", "brand", "terms")},
        ),
        ("Identifiers", {"fields": ("gtin", "mpn", "google_category_id")}),
        ("Related products", {"fields": ("required_companion", "upsells")}),
        ("Migration", {"fields": ("legacy_wp_id_en", "legacy_wp_id_it")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (ProductTranslationInline, VariantInline)

    def get_readonly_fields(self, request: HttpRequest, obj: Product | None = None) -> Any:
        """A product never changes vendor (Q-33, CAT.14): staff duplicate it instead."""
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields if obj is None else (*fields, "vendor")

    def formfield_for_foreignkey(
        self, db_field: models.ForeignKey[Any, Any], request: HttpRequest, **kwargs: Any
    ) -> forms.ModelChoiceField[Any] | None:
        if db_field.name == "category":
            # The tree in order, children indented under their parent.
            kwargs["form_class"] = TreeNodeChoiceField
            kwargs["queryset"] = Category.objects.prefetch_related("translations")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(
        self, db_field: models.ManyToManyField[Any, Any], request: HttpRequest, **kwargs: Any
    ) -> forms.ModelMultipleChoiceField[Any] | None:
        if db_field.name == "secondary_categories":
            kwargs["queryset"] = Category.objects.prefetch_related("translations")
        elif db_field.name == "terms":
            kwargs["queryset"] = Term.objects.select_related("taxonomy").prefetch_related(
                "translations"
            )
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(
        self, request: HttpRequest, obj: Product, form: forms.ModelForm[Product], change: bool
    ) -> None:
        new_prices = take_price_change(obj, form.changed_data, PRODUCT_PRICE_FIELDS)
        super().save_model(request, obj, form, change)
        if new_prices is not None:
            set_product_price(obj, **new_prices, ctx=audit_context(request))

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        if formset.model is not Variant:
            super().save_formset(request, form, formset, change)
            return
        # Existing variants whose override changed: save the rest, then reprice them audited.
        repriced: list[tuple[Variant, dict[str, Any]]] = []
        for variant_form in formset.forms:
            if variant_form in formset.deleted_forms:
                continue
            variant = variant_form.instance
            new_prices = take_price_change(variant, variant_form.changed_data, VARIANT_PRICE_FIELDS)
            if new_prices is not None:
                repriced.append((variant, new_prices))
        super().save_formset(request, form, formset, change)
        ctx = audit_context(request)
        for variant, prices in repriced:
            set_variant_price(variant, **prices, ctx=ctx)


class VariantTranslationInline(admin.StackedInline[VariantTranslation, Variant]):
    model = VariantTranslation
    extra = 1
    fields = ("language", "status", "label", "description")


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin[Variant]):
    list_display = ("sku", "product", "vendor", "stock_qty", "stock_status", "is_default")
    list_filter = ("stock_status", "vendor")
    search_fields = ("sku", "product__sku")
    list_select_related = ("product", "vendor")
    raw_id_fields = ("product",)
    readonly_fields = ("vendor", *TIMESTAMPS)
    fields = (
        "product",
        "vendor",
        "sku",
        "position",
        "is_default",
        "stock_qty",
        "stock_status",
        "price_net_override",
        "weight_kg_override",
        *TIMESTAMPS,
    )
    inlines = (VariantTranslationInline,)

    def save_model(
        self, request: HttpRequest, obj: Variant, form: forms.ModelForm[Variant], change: bool
    ) -> None:
        new_prices = take_price_change(obj, form.changed_data, VARIANT_PRICE_FIELDS)
        super().save_model(request, obj, form, change)
        if new_prices is not None:
            set_variant_price(obj, **new_prices, ctx=audit_context(request))
