"""Staff admin for the ingest app (ING.1). Staff only: vendors never reach the Django admin.

Sources and mappings are edited here through the audited services. Runs, staging rows, drafts
and review items are written by the pipeline and the review screens, so the admin only shows
them.
"""

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.audit import audit_context

from . import services
from .models import (
    ImportRun,
    MappingTable,
    ProductDraft,
    ReviewItem,
    SourceItem,
    SourceObservation,
    SupplierSource,
)


class _ReadOnly:
    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(SupplierSource)
class SupplierSourceAdmin(admin.ModelAdmin[SupplierSource]):
    list_display = ("code", "vendor", "adapter_code", "is_active", "write_enabled")
    list_filter = ("adapter_code", "is_active", "write_enabled")
    search_fields = ("code", "vendor__display_name")
    raw_id_fields = ("vendor",)

    def save_model(
        self, request: HttpRequest, obj: SupplierSource, form: Any, change: bool
    ) -> None:
        services.save_source(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: SupplierSource) -> None:
        raise PermissionError("Deactivate a source instead of deleting it.")

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[SupplierSource]) -> None:
        raise PermissionError("Deactivate a source instead of deleting it.")

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(MappingTable)
class MappingTableAdmin(admin.ModelAdmin[MappingTable]):
    list_display = ("key", "kind", "vendor", "target_value", "target_type")
    list_filter = ("kind",)
    search_fields = ("key", "key_norm", "vendor__display_name")
    raw_id_fields = ("vendor",)
    fields = ("vendor", "kind", "key", "aliases", "target_type", "target_id", "target_value")

    def save_model(self, request: HttpRequest, obj: MappingTable, form: Any, change: bool) -> None:
        services.save_mapping(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: MappingTable) -> None:
        services.delete_mapping(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[MappingTable]) -> None:
        for entry in queryset:
            services.delete_mapping(entry, ctx=audit_context(request))


@admin.register(ImportRun)
class ImportRunAdmin(_ReadOnly, admin.ModelAdmin[ImportRun]):
    list_display = ("source", "kind", "status", "dry_run", "seen", "created", "updated", "errors")
    list_filter = ("kind", "status", "dry_run")
    list_select_related = ("source__vendor",)


@admin.register(ReviewItem)
class ReviewItemAdmin(_ReadOnly, admin.ModelAdmin[ReviewItem]):
    list_display = ("kind", "decision", "blocking", "run", "resolved_by", "resolved_at")
    list_filter = ("kind", "decision", "blocking")
    list_select_related = ("run__source__vendor", "resolved_by")


@admin.register(SourceItem)
class SourceItemAdmin(_ReadOnly, admin.ModelAdmin[SourceItem]):
    list_display = ("sku", "source", "product", "last_seen_at", "missing_since")
    search_fields = ("sku", "sku_key", "source_url")
    list_select_related = ("source", "product")


@admin.register(SourceObservation)
class SourceObservationAdmin(_ReadOnly, admin.ModelAdmin[SourceObservation]):
    list_display = ("item", "observed_at", "payload_hash")
    list_select_related = ("item",)


@admin.register(ProductDraft)
class ProductDraftAdmin(_ReadOnly, admin.ModelAdmin[ProductDraft]):
    list_display = ("item", "status", "updated_at")
    list_filter = ("status",)
    list_select_related = ("item",)
