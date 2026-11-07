"""Staff admin for API clients (CORE.13; Wagtail admin replaces this in CORE.8).

Read-only: a key is issued by `services.issue_api_client` (the full key exists only in that
answer) and never edited in place. Staff see which vendor each key acts for (empty: a platform
key) and revoke keys through the audited `revoke_api_client`. The key hash is never shown.
"""

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.audit import audit_context

from .models import ApiClient
from .services import revoke_api_client


@admin.register(ApiClient)
class ApiClientAdmin(admin.ModelAdmin[ApiClient]):
    list_display = ("name", "key_prefix", "vendor", "is_active", "expires_at", "last_used_at")
    list_filter = (("vendor", admin.EmptyFieldListFilter), "is_active", "vendor")
    list_select_related = ("vendor",)
    search_fields = ("name", "key_prefix", "vendor__display_name", "vendor__legal_name")
    actions = ("revoke",)
    fields = (
        "public_id",
        "name",
        "vendor",
        "scopes",
        "key_prefix",
        "allowed_ips",
        "expires_at",
        "last_used_at",
        "is_active",
        "revoked_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_revoke_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("api.change_apiclient")

    @admin.action(description="Revoke the selected API keys", permissions=["revoke"])
    def revoke(self, request: HttpRequest, queryset: QuerySet[ApiClient]) -> None:
        ctx = audit_context(request)
        revoked = 0
        for client in queryset.select_related("vendor"):
            if client.is_active:
                revoke_api_client(client, ctx=ctx)
                revoked += 1
        self.message_user(request, f"{revoked} API key(s) revoked.")
