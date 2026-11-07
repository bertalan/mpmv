"""Admin of the attribute catalogue (CAT.3): attributes, their values and their texts.

Platform data managed by staff, like the taxonomies; not audited (see CatalogConfig). What a
product or a variant says of an attribute is created by `services.create_product` (API, imports).
"""

from django.contrib import admin
from django.db import models
from django.http import HttpRequest

from .models import Attribute, AttributeTranslation, AttributeValue, AttributeValueTranslation

TIMESTAMPS = ("public_id", "created_at", "updated_at")


class AttributeTranslationInline(admin.TabularInline[AttributeTranslation, Attribute]):
    model = AttributeTranslation
    extra = 1
    fields = ("language", "status", "name")


class AttributeValueInline(admin.TabularInline[AttributeValue, Attribute]):
    model = AttributeValue
    extra = 1
    fields = ("value", "position")
    show_change_link = True  # labels are translated on the value's own page


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin[Attribute]):
    list_display = ("code", "kind", "unit", "position", "languages")
    list_filter = ("kind",)
    search_fields = ("code", "translations__name")
    readonly_fields = TIMESTAMPS
    fields = ("code", "kind", "unit", "position", *TIMESTAMPS)
    inlines = (AttributeTranslationInline, AttributeValueInline)

    def get_queryset(self, request: HttpRequest) -> "models.QuerySet[Attribute]":
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="names")
    def languages(self, obj: Attribute) -> str:
        return ", ".join(row.language for row in obj.translations.all())


class AttributeValueTranslationInline(
    admin.TabularInline[AttributeValueTranslation, AttributeValue]
):
    model = AttributeValueTranslation
    extra = 1
    fields = ("language", "status", "label")


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin[AttributeValue]):
    list_display = ("__str__", "attribute", "value", "position")
    list_filter = ("attribute",)
    list_select_related = ("attribute",)
    search_fields = ("value", "attribute__code", "translations__label")
    readonly_fields = TIMESTAMPS
    fields = ("attribute", "value", "position", *TIMESTAMPS)
    inlines = (AttributeValueTranslationInline,)

    def get_queryset(self, request: HttpRequest) -> "models.QuerySet[AttributeValue]":
        return super().get_queryset(request).prefetch_related("translations")
