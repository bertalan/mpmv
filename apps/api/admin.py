"""Staff admin for API clients (CORE.13; Wagtail admin replaces this in CORE.8).

Read-only: a key is issued by `services.issue_api_client` (the full key exists only in that
answer) and never edited in place. Staff see which vendor each key acts for (empty: a platform
key) and revoke keys through the audited `revoke_api_client`. The key hash is never shown.

The one value staff set on a key is its rate limit override (CORE.16), on its own page that
needs `api.change_apiclient` and a fresh second factor (CORE.4 step-up), through the audited
`set_api_client_rate_limit`.
"""

from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.accounts.decorators import step_up_required
from apps.core.audit import audit_context

from .engine import InvalidRateLimit, key_wide_rate
from .models import ApiClient
from .services import (
    rate_limit_override,
    rate_policy_from_settings,
    revoke_api_client,
    set_api_client_rate_limit,
)

CHANGE_PERMISSION = "api.change_apiclient"
RATE_LIMIT_TEMPLATE = "admin/api/apiclient/rate_limit.html"


class RateLimitForm(forms.Form):
    rate_limit = forms.CharField(
        label="Rate limit override",
        required=False,
        max_length=32,
        help_text=(
            "Key-wide calls per window, e.g. 1200/minute or 50000/day (units: second, minute, "
            "hour, day). Raises or lowers this key only; empty uses the settings. Scopes with a "
            "limit of their own keep it."
        ),
    )


def effective_rate_limit(client: ApiClient) -> str:
    """The key-wide limit that applies to `client` now, and where it comes from."""
    policy = rate_policy_from_settings()
    override = rate_limit_override(client)
    rate = key_wide_rate(policy, client_id=str(client.public_id), override=override)
    if override is not None:
        source = "override set by staff"
    elif str(client.public_id) in policy.clients:
        source = "API_RATE_LIMIT_CLIENTS setting"
    else:
        source = "API_RATE_LIMIT setting"
    return f"{rate.limit} calls per {rate.period} s ({source})"


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
        "rate_limit",
        "effective_rate_limit",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    @admin.display(description="Effective rate limit")
    def effective_rate_limit(self, obj: ApiClient) -> SafeString:
        url = reverse("admin:api_apiclient_rate_limit", args=[obj.pk])
        return format_html(
            '{} &middot; <a href="{}">Change the rate limit</a>', effective_rate_limit(obj), url
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_revoke_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm(CHANGE_PERMISSION)

    @admin.action(description="Revoke the selected API keys", permissions=["revoke"])
    def revoke(self, request: HttpRequest, queryset: QuerySet[ApiClient]) -> None:
        ctx = audit_context(request)
        revoked = 0
        for client in queryset.select_related("vendor"):
            if client.is_active:
                revoke_api_client(client, ctx=ctx)
                revoked += 1
        self.message_user(request, f"{revoked} API key(s) revoked.")

    # --- Rate limit override page: the change permission and a fresh second factor (CORE.16)

    def get_urls(self) -> list[URLPattern]:
        pages = [
            path(
                "<path:object_id>/rate-limit/",
                self.admin_site.admin_view(step_up_required()(self.rate_limit_view)),
                name=f"{self.opts.app_label}_{self.opts.model_name}_rate_limit",
            ),
        ]
        return pages + super().get_urls()

    def rate_limit_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied
        obj = self.get_queryset(request).filter(pk=unquote(object_id)).first()
        if obj is None:
            raise Http404("No such API client.")
        change_url = reverse("admin:api_apiclient_change", args=[obj.pk])
        form = RateLimitForm(request.POST or None, initial={"rate_limit": obj.rate_limit})
        if request.method == "POST" and form.is_valid():
            value: str = form.cleaned_data["rate_limit"]
            try:
                changes = set_api_client_rate_limit(obj, value, ctx=audit_context(request))
            except InvalidRateLimit as exc:
                form.add_error("rate_limit", str(exc))
            else:
                if changes.is_empty:
                    self.message_user(request, "The rate limit was not changed.", messages.INFO)
                else:
                    shown = value.strip() or "the settings"
                    self.message_user(
                        request, f"The rate limit of {obj} is now {shown}.", messages.SUCCESS
                    )
                return redirect(change_url)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "original": obj,
            "title": f"Rate limit of {obj}",
            "form": form,
            "change_url": change_url,
            "effective": effective_rate_limit(obj),
        }
        return TemplateResponse(request, RATE_LIMIT_TEMPLATE, context)
